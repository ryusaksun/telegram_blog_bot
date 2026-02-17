"""配置管理 — 移植自 iOS 端 AppConfig.swift"""

import os
from dotenv import load_dotenv

load_dotenv()

# Telegram
BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USERS: set[int] = set()
_raw = os.environ.get("TELEGRAM_ALLOWED_USERS", "")
if _raw.strip():
    for _uid in _raw.split(","):
        _uid = _uid.strip()
        if _uid.isdigit():
            ALLOWED_USERS.add(int(_uid))

# GitHub — 内容仓库
GITHUB_TOKEN: str = os.environ.get("GITHUB_TOKEN", "")
GITHUB_OWNER: str = os.environ.get("GITHUB_OWNER", "ryusaksun")
GITHUB_REPO: str = os.environ.get("GITHUB_REPO", "astro_blog")
GITHUB_BRANCH: str = os.environ.get("GITHUB_BRANCH", "main")

# GitHub — 图床仓库
IMAGE_REPO: str = os.environ.get("IMAGE_REPO", "picx-images-hosting")
IMAGE_BRANCH: str = os.environ.get("IMAGE_BRANCH", "master")
IMAGE_PATH: str = os.environ.get("IMAGE_PATH", "images")
CDN_TYPE: str = os.environ.get("CDN_TYPE", "jsdelivr")

# 图片压缩
MAX_IMAGE_WIDTH: int = 1920
MAX_IMAGE_HEIGHT: int = 1080
IMAGE_QUALITY: float = 0.85
MAX_FILE_SIZE: int = 5 * 1024 * 1024          # 5 MB
IMAGE_COMPRESSION_THRESHOLD: int = 10 * 1024 * 1024  # 10 MB

GITHUB_API_BASE: str = "https://api.github.com"


def generate_cdn_url(owner: str, repo: str, branch: str, path: str) -> str:
    """生成图片 CDN URL — 移植自 AppConfig.generateImageCDNUrl()"""
    if CDN_TYPE == "jsdelivr":
        return f"https://cdn.jsdelivr.net/gh/{owner}/{repo}@{branch}/{path}"
    elif CDN_TYPE == "statically":
        return f"https://cdn.statically.io/gh/{owner}/{repo}/{branch}/{path}"
    else:
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
