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
        "发送图片(带 caption) → 图片+文字发布\n"
        "发送图片(无 caption) → 仅上传图床\n\n"
        "/status — 检查 GitHub 连接\n"
        "/help — 帮助"
    )


@authorized_only
async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help 帮助"""
    await update.message.reply_text(
        "使用方式:\n\n"
        "1. 纯文字 → 直接发布为 Essay\n"
        "2. 图片+caption → 上传图片并发布 Essay(含图片)\n"
        "3. 图片(无caption) → 上传到图床，返回 CDN URL\n"
        "4. 多图+caption → 所有图片嵌入一条 Essay\n"
        "5. 多图(无caption) → 批量上传，返回所有 CDN URL"
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
    photo = msg.photo[-1]  # 最大尺寸
    file = await photo.get_file()
    image_bytes = await file.download_as_bytearray()

    caption = msg.caption or ""

    try:
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
