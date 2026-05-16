from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger


class BaseNode(ABC):
    is_real_data: bool = False  # True이면 실제 API/WS 연결에서 수집한 데이터

    def __init__(self, name: str, config: dict[str, Any], db: Any = None, notifier: Any = None) -> None:
        self.name = name
        self.config = config
        self.db = db
        self.notifier = notifier
        self.status = "stopped"
        self.earnings = 0.0
        self.start_time: datetime | None = None
        self.connection_issue: str | None = None  # 연결 문제 설명 (없으면 정상)
        self._setup_logger()

    def _setup_logger(self) -> None:
        Path("logs").mkdir(exist_ok=True)
        node_name = self.name

        def _node_filter(record: dict) -> bool:
            """해당 노드 관련 로그만 기록."""
            msg = str(record.get("message", ""))
            return f"[{node_name}]" in msg or f"[{node_name}" in msg or node_name in record.get("name", "")

        logger.add(
            f"logs/{self.name}.log",
            rotation="10 MB",
            retention=5,
            enqueue=True,
            filter=_node_filter,
        )

    @abstractmethod
    async def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_status(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def get_earnings(self) -> dict[str, Any]:
        raise NotImplementedError

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def health_check(self) -> bool:
        status = await self.get_status()
        return status.get("status") == "running"

    async def save_earnings(self, amount: float, unit: str, usd_value: float) -> None:
        self.earnings += float(usd_value)
        if self.db:
            await self.db.save_earnings(self.name, amount, unit, usd_value)

    async def notify(self, message: str) -> None:
        if self.notifier:
            await self.notifier.send(message)

    async def _retry(
        self,
        func: Callable[[], Awaitable[Any]],
        max_attempts: int = 3,
        delay: int = 5,
    ) -> Any:
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                logger.info(f"[{self.name}] attempt {attempt}/{max_attempts}")
                return await func()
            except Exception as exc:
                last_exc = exc
                logger.warning(f"[{self.name}] retry failed: {exc}")
                if attempt < max_attempts:
                    await asyncio.sleep(delay)
        if last_exc:
            raise last_exc
