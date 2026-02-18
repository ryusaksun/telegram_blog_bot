"""Telegram 消息处理 — 对应 iOS 端 EditorViewModel.swift 的发布流程"""

from __future__ import annotations

import asyncio
import logging
from functools import wraps
from typing import Any

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from . import config
from .github_service import GitHubService
from .image_service import upload_image

logger = logging.getLogger(__name__)

# 全局 GitHub 服务实例（在 main.py 中初始化后注入）
github: GitHubService | None = None

# media group 收集器: {media_group_id: [updates]}
_media_groups: dict[str, list[Update]] = {}


# ------------------------------------------------------------------
# 授权装饰器
# ------------------------------------------------------------------

def authorized_only(func):
    """检查用户是否在白名单中"""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args: Any, **kwargs: Any):
        user = update.effective_user
        if not user or user.id not in config.ALLOWED_USERS:
            logger.warning("未授权用户: %s (id=%s)", user.username if user else "?", user.id if user else "?")
            return  # 静默拒绝
        return await func(update, context, *args, **kwargs)
    return wrapper


# ------------------------------------------------------------------
# 命令处理
# ------------------------------------------------------------------

@authorized_only
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start 欢迎信息"""
    await update.message.reply_text(
        "Essay Bot 已就绪\n\n"
        "直接发送文字即发布为 Essay\n"
        "发送图片 → 上传图片并发布为 Essay\n"
        "图片+文字 → 文字+图片一起发布\n"
        "发送 .md 文件 → 文件名作为标题发布\n\n"
        "/list — 最近发布的 Essay\n"
        "/delete — 删除指定 Essay\n"
        "/status — 检查 GitHub 连接\n"
        "/help — 帮助"
    )


@authorized_only
async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help 帮助"""
    await update.message.reply_text(
        "使用方式:\n\n"
        "1. 纯文字 → 直接发布为 Essay\n"
        "2. 图片 → 上传图片并发布为 Essay\n"
        "3. 图片+caption → 文字+图片一起发布\n"
        "4. 多图 → 所有图片合并为一条 Essay\n"
        "5. 多图+caption → 文字+所有图片一起发布\n"
        "6. .md 文件 → 文件名作为标题发布\n\n"
        "管理:\n"
        "/list [N] — 列出最近 N 条 Essay\n"
        "/delete <文件名> — 删除指定 Essay"
    )


@authorized_only
async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status 检查 GitHub 连接"""
    assert github is not None
    try:
        username = await github.verify_token()
        await update.message.reply_text(
            f"GitHub 连接正常\n"
            f"用户: {username}\n"
            f"内容仓库: {config.GITHUB_OWNER}/{config.GITHUB_REPO}\n"
            f"图床仓库: {config.GITHUB_OWNER}/{config.IMAGE_REPO}"
        )
    except Exception as exc:
        await update.message.reply_text(f"GitHub 连接失败: {exc}")


# ------------------------------------------------------------------
# /list — 列出最近 Essay
# ------------------------------------------------------------------

@authorized_only
async def list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/list [N] 列出最近 N 条 Essay（默认 10）"""
    assert github is not None
    # 解析参数
    limit = 10
    if context.args:
        try:
            limit = min(int(context.args[0]), 30)
        except ValueError:
            pass

    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        essays = await github.list_essays(limit)
        if not essays:
            await update.message.reply_text("暂无 Essay")
            return

        lines: list[str] = []
        for i, e in enumerate(essays, 1):
            lines.append(f"{i}. `{e['name']}`")
        text = f"最近 {len(essays)} 条 Essay:\n\n" + "\n".join(lines)
        text += "\n\n删除: /delete <文件名>"
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as exc:
        logger.exception("列出 Essay 失败")
        await update.message.reply_text(f"获取列表失败: {exc}")


# ------------------------------------------------------------------
# /delete — 删除 Essay
# ------------------------------------------------------------------

@authorized_only
async def delete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/delete <文件名> 删除指定 Essay"""
    assert github is not None
    if not context.args:
        await update.message.reply_text("用法: /delete <文件名>\n先用 /list 查看文件名")
        return

    name = context.args[0].strip()
    # 支持只传文件名，自动补全路径
    if not name.startswith("src/"):
        path = f"src/content/essays/{name}"
    else:
        path = name
    if not path.endswith(".md"):
        path += ".md"

    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        await github.delete_file(path)
        await update.message.reply_text(f"已删除 ✓\n{path}")
    except FileNotFoundError:
        await update.message.reply_text(f"文件不存在: {path}")
    except Exception as exc:
        logger.exception("删除失败")
        await update.message.reply_text(f"删除失败: {exc}")


# ------------------------------------------------------------------
# .md 文件上传 → 发布 Essay
# ------------------------------------------------------------------

@authorized_only
async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """.md 文件上传 → 文件名作为标题发布"""
    assert github is not None
    msg = update.message
    doc = msg.document

    # 校验文件扩展名
    file_name = doc.file_name or ""
    if not file_name.lower().endswith(".md"):
        await msg.reply_text("仅支持 .md 文件")
        return

    # 校验文件大小 (5MB)
    if doc.file_size and doc.file_size > 5 * 1024 * 1024:
        await msg.reply_text("文件过大，限制 5MB")
        return

    await msg.chat.send_action(ChatAction.TYPING)
    try:
        file = await doc.get_file()
        raw = await file.download_as_bytearray()

        # UTF-8 解码，处理 BOM
        content = bytes(raw).decode("utf-8-sig")
        if not content.strip():
            await msg.reply_text("文件内容为空")
            return

        # 标题 = 文件名去 .md
        title = file_name.rsplit(".", 1)[0]

        result = await github.publish_markdown_file(content, title)
        await msg.reply_text(
            f"已发布 Post ✓\n标题: {title}\n路径: {result.file_path}"
        )
    except Exception as exc:
        logger.exception(".md 文件处理失败")
        await msg.reply_text(f"发布失败: {exc}")


# ------------------------------------------------------------------
# 文字消息 → 直接发布 Essay
# ------------------------------------------------------------------

@authorized_only
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """纯文字消息 → 发布 Essay"""
    assert github is not None
    text = update.message.text
    if not text or not text.strip():
        return

    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        result = await github.publish_content(text)
        await update.message.reply_text(
            f"已发布 ✓\n路径: {result.file_path}"
        )
    except Exception as exc:
        logger.exception("发布失败")
        await update.message.reply_text(f"发布失败: {exc}")


# ------------------------------------------------------------------
# 图片消息
# ------------------------------------------------------------------

@authorized_only
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """图片消息处理入口"""
    msg = update.message

    # 多图 media group: 收集后延迟处理
    if msg.media_group_id:
        group_id = msg.media_group_id
        if group_id not in _media_groups:
            _media_groups[group_id] = []
            # 延迟 1.5s 收集同组消息后统一处理
            context.application.job_queue.run_once(
                _process_media_group,
                when=1.5,
                data=group_id,
            )
        _media_groups[group_id].append(update)
        return

    # 单图处理
    await msg.chat.send_action(ChatAction.UPLOAD_PHOTO)
    await _process_single_photo(update)


async def _process_single_photo(update: Update) -> None:
    """处理单张图片"""
    assert github is not None
    msg = update.message
    caption = msg.caption or ""

    try:
        photo = msg.photo[-1]  # 最大尺寸
        file = await photo.get_file()
        image_bytes = await file.download_as_bytearray()

        cdn_url = await upload_image(bytes(image_bytes), github)

        if caption.strip():
            body = f"{caption}\n\n![image]({cdn_url})"
        else:
            body = f"![image]({cdn_url})"
        result = await github.publish_content(body)
        await msg.reply_text(f"已发布 ✓\n路径: {result.file_path}")
    except Exception as exc:
        logger.exception("图片处理失败")
        await msg.reply_text(f"处理失败: {exc}")


async def _process_media_group(context: ContextTypes.DEFAULT_TYPE) -> None:
    """延迟处理 media group（多图）"""
    assert github is not None
    group_id: str = context.job.data
    updates = _media_groups.pop(group_id, [])
    if not updates:
        return

    # 收集 caption（取第一个有 caption 的消息）
    caption = ""
    for u in updates:
        if u.message.caption:
            caption = u.message.caption
            break

    # 下载并上传所有图片
    cdn_urls: list[str] = []
    reply_msg = updates[0].message  # 用第一条消息回复
    await reply_msg.chat.send_action(ChatAction.UPLOAD_PHOTO)

    try:
        for u in updates:
            photo = u.message.photo[-1]
            file = await photo.get_file()
            image_bytes = await file.download_as_bytearray()
            url = await upload_image(bytes(image_bytes), github)
            cdn_urls.append(url)

        images_md = "\n\n".join(f"![image]({url})" for url in cdn_urls)
        if caption.strip():
            body = f"{caption}\n\n{images_md}"
        else:
            body = images_md
        result = await github.publish_content(body)
        await reply_msg.reply_text(
            f"已发布 ✓ ({len(cdn_urls)} 张图片)\n路径: {result.file_path}"
        )
    except Exception as exc:
        logger.exception("多图处理失败")
        await reply_msg.reply_text(f"处理失败: {exc}")


# ------------------------------------------------------------------
# 全局错误处理
# ------------------------------------------------------------------

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """全局错误处理"""
    logger.error("异常: %s", context.error, exc_info=context.error)
    if isinstance(update, Update) and update.message:
        await update.message.reply_text("内部错误，请稍后重试")
