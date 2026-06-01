"""AI-Chats-Together v2: автономные AI-боты в Telegram с Managed Bots API + ручной ввод токена."""
import os
import sys
import asyncio
import logging
import random
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from database import Database
from ai import AIClient, PersonalityGenerator
from bots.master import MasterBot
from lifecycle.manager import LifecycleManager

# ── logging ──
LOG_LEVEL = getattr(logging, os.getenv("LOG_LEVEL", "INFO"))
LOG_FILE = os.getenv("LOG_FILE", "data/bots.log")
Path("data").mkdir(exist_ok=True)

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")


def validate_env() -> bool:
    required = ["MASTER_BOT_TOKEN", "GROUP_ID", "FREEMODEL_API_KEY"]
    missing = [v for v in required if not os.getenv(v)]
    if missing:
        logger.error("Missing env vars: %s", ", ".join(missing))
        logger.error("Скопируй .env.example → .env и заполни.")
        return False
    return True


def build_config() -> dict:
    admin_ids_str = os.getenv("ADMIN_USER_IDS", "")
    admin_ids = []
    if admin_ids_str:
        try:
            admin_ids = [int(x.strip()) for x in admin_ids_str.split(",") if x.strip()]
        except ValueError:
            logger.warning("ADMIN_USER_IDS содержит невалидные ID")

    return {
        "MASTER_BOT_TOKEN": os.getenv("MASTER_BOT_TOKEN"),
        "GROUP_ID": int(os.getenv("GROUP_ID")),
        "FREEMODEL_API_KEY": os.getenv("FREEMODEL_API_KEY"),
        "FREEMODEL_BASE_URL": os.getenv("FREEMODEL_BASE_URL", "https://freemodel.dev/v1"),
        "DATABASE_PATH": os.getenv("DATABASE_PATH", "data/bots.db"),
        "AVAILABLE_MODELS": os.getenv("AVAILABLE_MODELS", "gpt-5.5,gpt-5.4,gpt-5.4-mini,gpt-5.3-codex").split(","),
        "MAX_CLONES": int(os.getenv("MAX_CLONES_PER_CHARACTER", "3")),
        "MAX_BOTS": int(os.getenv("MAX_TOTAL_BOTS", "10")),
        "DEFAULT_REPLY_CHANCE": float(os.getenv("DEFAULT_REPLY_CHANCE", "0.7")),
        "INITIATE_CHAT_CHANCE": float(os.getenv("INITIATE_CHAT_CHANCE", "0.15")),
        "MIN_MESSAGE_INTERVAL": int(os.getenv("MIN_MESSAGE_INTERVAL", "120")),
        "MAX_MESSAGE_INTERVAL": int(os.getenv("MAX_MESSAGE_INTERVAL", "1800")),
        "ADMIN_USER_IDS": admin_ids,
    }


async def main():
    if not validate_env():
        sys.exit(1)

    config = build_config()
    logger.info("🚀 AI-Chats-Together v2 starting...")

    master = MasterBot(config)
    await master.initialize()

    lifecycle = LifecycleManager(
        db=master.db, ai_client=master.ai_client,
        pg=master.pg, master_bot=master,
    )
    await lifecycle.start()

    try:
        await master.start()
    except KeyboardInterrupt:
        logger.info("👋 Shutting down...")
        await lifecycle.stop()
        await master.stop()


if __name__ == "__main__":
    asyncio.run(main())
