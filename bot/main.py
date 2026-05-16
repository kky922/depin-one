from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger

from core.config_manager import ConfigManager
from core.database import Database
from core.scheduler import DePINScheduler


async def run() -> None:
    project_root = Path(__file__).resolve().parent
    logs_dir = project_root / "logs"
    data_dir = project_root / "data"
    logs_dir.mkdir(exist_ok=True)
    data_dir.mkdir(exist_ok=True)
    logger.add(str(logs_dir / "main.log"), rotation="10 MB", retention=5, enqueue=True)

    config = ConfigManager(project_root / "config.yaml").load()
    db = Database(f"sqlite+aiosqlite:///{data_dir / 'depin_bot.db'}")
    await db.init()

    scheduler = DePINScheduler(config, db)
    await scheduler.start()
    logger.info("DePIN scheduler started")

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await scheduler.shutdown()
        await db.close()


if __name__ == "__main__":
    asyncio.run(run())
