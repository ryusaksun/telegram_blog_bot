"""GitHub API 服务 — 移植自 iOS 端 GitHubService.swift"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from . import config
from .content_service import assemble_content, assemble_content_with_title, generate_file_path

logger = logging.getLogger(__name__)


@dataclass
class PublishResult:
    success: bool
    file_path: str
    action: str  # "add" | "update"


@dataclass
class ImageUploadResult:
    success: bool
    path: str
    url: str


class GitHubService:
    """异步 GitHub API 客户端 — 对应 GitHubService.swift"""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=config.GITHUB_API_BASE,
            headers={
                "Authorization": f"token {config.GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # 通用请求 — 对应 GitHubService.swift 第 45-99 行
    # ------------------------------------------------------------------

    async def request(
        self,
        endpoint: str,
        method: str = "GET",
        body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        resp = await self._client.request(method, endpoint, json=body)
        resp.raise_for_status()
        return resp

    # ------------------------------------------------------------------
    # 文件操作 — 对应 GitHubService.swift 第 172-228 行
    # ------------------------------------------------------------------

    async def get_file(self, path: str, repo: str | None = None, branch: str | None = None) -> dict[str, Any] | None:
        """获取文件内容 + SHA，404 返回 None"""
        repo = repo or config.GITHUB_REPO
        endpoint = f"/repos/{config.GITHUB_OWNER}/{repo}/contents/{path}"
        if branch:
            endpoint += f"?ref={branch}"
        try:
            resp = await self.request(endpoint)
            data = resp.json()
            raw = base64.b64decode(data["content"]).decode()
            return {"content": raw, "sha": data["sha"]}
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    async def create_or_update_file(
        self,
        path: str,
        content: str,
        message: str,
        sha: str | None = None,
        repo: str | None = None,
        branch: str | None = None,
    ) -> dict[str, Any]:
        """PUT 创建/更新文件 — 对应 GitHubService.swift 第 210-228 行"""
        repo = repo or config.GITHUB_REPO
        body: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content.encode()).decode(),
        }
        if sha:
            body["sha"] = sha
        if branch:
            body["branch"] = branch

        resp = await self.request(
            f"/repos/{config.GITHUB_OWNER}/{repo}/contents/{path}",
            method="PUT",
            body=body,
        )
        return resp.json()

    # ------------------------------------------------------------------
    # 发布内容 — 对应 GitHubService.swift 第 231-261 行
    # ------------------------------------------------------------------

    async def publish_content(self, body: str) -> PublishResult:
        """完整 Essay 发布流程"""
        full_content = assemble_content(body)
        file_path = generate_file_path(full_content)

        existing = await self.get_file(file_path, branch=config.GITHUB_BRANCH)
        action = "Update" if existing else "Add"
        sha = existing["sha"] if existing else None

        # 提交消息中用前 20 字做预览
        preview = body.replace("\n", " ")[:20]
        commit_msg = f"{action} essay: {preview}"

        await self.create_or_update_file(
            path=file_path,
            content=full_content,
            message=commit_msg,
            sha=sha,
            branch=config.GITHUB_BRANCH,
        )
        return PublishResult(success=True, file_path=file_path, action=action.lower())

    async def publish_markdown_file(self, body: str, title: str) -> PublishResult:
        """上传 .md 文件发布 Essay，保留已有 frontmatter 或生成新的"""
        full_content = assemble_content_with_title(body, title)
        file_path = generate_file_path(full_content, title=title)

        existing = await self.get_file(file_path, branch=config.GITHUB_BRANCH)
        action = "Update" if existing else "Add"
        sha = existing["sha"] if existing else None

        preview = title[:20]
        commit_msg = f"{action} essay: {preview}"

        await self.create_or_update_file(
            path=file_path,
            content=full_content,
            message=commit_msg,
            sha=sha,
            branch=config.GITHUB_BRANCH,
        )
        return PublishResult(success=True, file_path=file_path, action=action.lower())

    # ------------------------------------------------------------------
    # 图片上传 — 对应 GitHubService.swift 第 327-362 行
    # ------------------------------------------------------------------

    async def upload_image(
        self, image_data: bytes, file_name: str
    ) -> ImageUploadResult:
        """上传图片到图床仓库"""
        from datetime import datetime, timezone, timedelta

        now = datetime.now(timezone(timedelta(hours=8)))
        year = now.strftime("%Y")
        month = now.strftime("%m")
        file_path = f"{config.IMAGE_PATH}/{year}/{month}/{file_name}"

        b64 = base64.b64encode(image_data).decode()
        body: dict[str, Any] = {
            "message": f"Upload image: {file_name}",
            "content": b64,
            "branch": config.IMAGE_BRANCH,
        }

        await self.request(
            f"/repos/{config.GITHUB_OWNER}/{config.IMAGE_REPO}/contents/{file_path}",
            method="PUT",
            body=body,
        )

        cdn_url = config.generate_cdn_url(
            config.GITHUB_OWNER, config.IMAGE_REPO, config.IMAGE_BRANCH, file_path
        )
        return ImageUploadResult(success=True, path=file_path, url=cdn_url)

    # ------------------------------------------------------------------
    # 列出目录 / 删除文件
    # ------------------------------------------------------------------

    async def list_essays(self, limit: int = 10) -> list[dict[str, str]]:
        """列出最近的 Essay 文件，按文件名倒序（即按日期倒序）"""
        endpoint = f"/repos/{config.GITHUB_OWNER}/{config.GITHUB_REPO}/contents/src/content/essays"
        if config.GITHUB_BRANCH:
            endpoint += f"?ref={config.GITHUB_BRANCH}"
        resp = await self.request(endpoint)
        items = resp.json()
        # 只保留 .md 文件，按名称倒序取最新
        md_files = [
            {"name": f["name"], "path": f["path"], "sha": f["sha"]}
            for f in items if f["name"].endswith(".md")
        ]
        md_files.sort(key=lambda x: x["name"], reverse=True)
        return md_files[:limit]

    async def delete_file(self, path: str) -> None:
        """删除文件"""
        existing = await self.get_file(path, branch=config.GITHUB_BRANCH)
        if not existing:
            raise FileNotFoundError(f"文件不存在: {path}")
        name = path.rsplit("/", 1)[-1]
        await self.request(
            f"/repos/{config.GITHUB_OWNER}/{config.GITHUB_REPO}/contents/{path}",
            method="DELETE",
            body={
                "message": f"Delete essay: {name}",
                "sha": existing["sha"],
                "branch": config.GITHUB_BRANCH,
            },
        )

    # ------------------------------------------------------------------
    # Token 验证 — 对应 GitHubService.swift 第 141-171 行
    # ------------------------------------------------------------------

    async def verify_token(self) -> str:
        """验证 Token 有效性，返回用户名"""
        resp = await self.request("/user")
        return resp.json()["login"]
