"""应用入口"""

import logging

from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from . import config
from .github_service import GitHubService
from . import handlers

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main() -> None:
    if not config.BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN 未设置")
    if not config.GITHUB_TOKEN:
        raise SystemExit("GITHUB_TOKEN 未设置")
    if not config.ALLOWED_USERS:
        logger.warning("TELEGRAM_ALLOWED_USERS 为空，所有消息将被拒绝")

    # 初始化 GitHub 服务
    handlers.github = GitHubService()

    app = ApplicationBuilder().token(config.BOT_TOKEN).build()

    # 命令
    app.add_handler(CommandHandler("start", handlers.start_handler))
    app.add_handler(CommandHandler("help", handlers.help_handler))
    app.add_handler(CommandHandler("status", handlers.status_handler))

    # 图片消息（优先匹配）
    app.add_handler(MessageHandler(filters.PHOTO, handlers.photo_handler))

    # 文字消息
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.text_handler))

    # 全局错误
    app.add_error_handler(handlers.error_handler)

    logger.info("Bot 启动，Polling 模式...")
    app.run_polling()


if __name__ == "__main__":
    main()
