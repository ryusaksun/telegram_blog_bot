"""图片处理 — 移植自 iOS 端 ImageService.swift"""

from __future__ import annotations

import io
import logging
import random
import time

from PIL import Image
from pillow_heif import register_heif_opener

from . import config
from .github_service import GitHubService

logger = logging.getLogger(__name__)

# 注册 HEIC/HEIF 格式支持，让 Pillow 可以直接 Image.open() 读取
register_heif_opener()

_HEIC_EXTS = {".heic", ".heif"}


def compress_image(image_bytes: bytes) -> bytes:
    """压缩图片 — 移植自 ImageService.swift 第 33-94 行

    < 10 MB: 保持原图（转 JPEG）
    >= 10 MB: smart_compress — 先缩放到 1920×1080 以内，再循环降质量
    """
    if len(image_bytes) < config.IMAGE_COMPRESSION_THRESHOLD:
        # 小于 10 MB，仅转为 JPEG 保持质量
        img = Image.open(io.BytesIO(image_bytes))
        img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        return buf.getvalue()

    # >= 10 MB: smart compress
    return _smart_compress(image_bytes)


def _smart_compress(image_bytes: bytes) -> bytes:
    """智能压缩 — 对应 ImageService.swift smartCompress()"""
    img = Image.open(io.BytesIO(image_bytes))
    img = img.convert("RGB")

    # 缩放到 maxWidth × maxHeight 以内
    max_w, max_h = config.MAX_IMAGE_WIDTH, config.MAX_IMAGE_HEIGHT
    w, h = img.size
    if w > max_w:
        ratio = max_w / w
        w, h = int(max_w), int(h * ratio)
    if h > max_h:
        ratio = max_h / h
        w, h = int(w * ratio), int(max_h)
    if (w, h) != img.size:
        img = img.resize((w, h), Image.LANCZOS)

    # 循环降质量: 0.85 → 0.75 → ... → 0.15
    quality = int(config.IMAGE_QUALITY * 100)  # 85
    target = config.MAX_FILE_SIZE  # 5 MB

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    data = buf.getvalue()

    while len(data) > target and quality > 10:
        prev_size = len(data)
        quality -= 10
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        data = buf.getvalue()
        # 提前退出：缩小不到 5%
        if len(data) > int(prev_size * 0.95):
            break

    return data


def generate_filename(ext: str = ".jpg") -> str:
    """生成唯一文件名 — 对应 ImageService.swift 第 169-172 行"""
    ts = int(time.time() * 1000)
    rnd = random.randint(1000, 9999)
    return f"img-{ts}-{rnd}{ext}"


def convert_heic_to_webp(image_bytes: bytes) -> bytes:
    """将 HEIC/HEIF 转为 WebP 无损格式，保留 Display P3 ICC Profile"""
    img = Image.open(io.BytesIO(image_bytes))

    # 保留 ICC Profile（Display P3 广色域）和 EXIF 信息
    save_kwargs: dict = {"format": "WebP", "lossless": True}
    icc_profile = img.info.get("icc_profile")
    if icc_profile:
        save_kwargs["icc_profile"] = icc_profile
    exif = img.info.get("exif")
    if exif:
        save_kwargs["exif"] = exif

    buf = io.BytesIO()
    img.save(buf, **save_kwargs)
    logger.info("HEIC → WebP 无损: %d KB → %d KB", len(image_bytes) // 1024, buf.tell() // 1024)
    return buf.getvalue()


async def upload_image(image_bytes: bytes, github: GitHubService, ext: str = ".jpg") -> str:
    """完整流程：生成文件名 → 上传 → 返回 CDN URL（保持原图不压缩）"""
    # HEIC/HEIF → WebP 无损（保留 Display P3 广色域）
    if ext.lower() in _HEIC_EXTS:
        image_bytes = convert_heic_to_webp(image_bytes)
        ext = ".webp"

    file_name = generate_filename(ext)
    result = await github.upload_image(image_bytes, file_name)
    return result.url
