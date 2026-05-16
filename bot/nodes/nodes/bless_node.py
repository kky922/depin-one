from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiohttp
from loguru import logger

from nodes.base_node import BaseNode

API_BASE = "https://gateway-run.bls.dev/api/v1"
NODE_ID_FILE = Path("data/bless_node_id.txt")
PING_INTERVAL = 60  # seconds
EARNINGS_PER_PING = 0.01  # BLS per successful ping


def _load_or_create_node_id() -> str:
    """Load persistent node ID from disk, or create a new one."""
    NODE_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
    if NODE_ID_FILE.exists():
        node_id = NODE_ID_FILE.read_text(encoding="utf-8").strip()
        if node_id:
            return node_id
    node_id = str(uuid4())
    NODE_ID_FILE.write_text(node_id, encoding="utf-8")
    logger.info(f"[bless] 생성된 새 node ID: {node_id}")
    return node_id


class BlessNode(BaseNode):
    is_real_data = True

    def __init__(self, config: dict[str, Any], db: Any = None, notifier: Any = None) -> None:
        super().__init__("bless", config, db, notifier)
        self.node_id: str = _load_or_create_node_id()
        self.api_token: str = config.get("api_token", "")
        self.ping_task: asyncio.Task[Any] | None = None
        self.total_pings: int = 0
        self.session_active: bool = False
        self.node_registered: bool = False

    async def start(self) -> None:
        if not self.config.get("enabled", True):
            self.status = "stopped"
            return
        if not self.api_token:
            self.status = "error"
            self.is_real_data = False
            self.connection_issue = "API 토큰 없음 (.env BLESS_API_TOKEN)"
            logger.warning("[bless] API 토큰 없음")
            return
        self.start_time = datetime.now(timezone.utc)
        self.status = "running"
        self.connection_issue = None

        # Register node and start session, then begin ping loop
        if not self.ping_task or self.ping_task.done():
            self.ping_task = asyncio.create_task(self._maintain_connection())

    async def stop(self) -> None:
        self.status = "stopped"
        self.session_active = False
        if self.ping_task:
            self.ping_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.ping_task
            self.ping_task = None

    async def _register_node(self) -> bool:
        """POST /nodes/{nodeId} to register with hardware info."""
        url = f"{API_BASE}/nodes/{self.node_id}"
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "nodeId": self.node_id,
            "hardware": {
                "platform": "macOS",
                "cpuCores": self._get_cpu_count(),
            },
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    text = await resp.text()
                    if resp.status in (200, 201):
                        logger.info(f"[bless] 노드 등록 성공: {self.node_id}")
                        self.node_registered = True
                        return True
                    elif resp.status == 409:
                        # Node already registered — treat as success
                        logger.info(f"[bless] 노드 이미 등록됨: {self.node_id}")
                        self.node_registered = True
                        return True
                    else:
                        logger.warning(f"[bless] 노드 등록 실패 (status={resp.status}): {text}")
                        self.connection_issue = f"등록 실패 (HTTP {resp.status})"
                        return False
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning(f"[bless] 노드 등록 네트워크 오류: {exc}")
            self.connection_issue = f"등록 네트워크 오류: {exc}"
            return False

    async def _start_session(self) -> bool:
        """POST /nodes/{nodeId}/start-session."""
        url = f"{API_BASE}/nodes/{self.node_id}/start-session"
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    text = await resp.text()
                    if resp.status in (200, 201):
                        logger.info(f"[bless] 세션 시작 성공")
                        self.session_active = True
                        return True
                    elif resp.status == 409:
                        # Session already active
                        logger.info(f"[bless] 세션 이미 활성 상태")
                        self.session_active = True
                        return True
                    else:
                        logger.warning(f"[bless] 세션 시작 실패 (status={resp.status}): {text}")
                        self.connection_issue = f"세션 시작 실패 (HTTP {resp.status})"
                        return False
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning(f"[bless] 세션 시작 네트워크 오류: {exc}")
            self.connection_issue = f"세션 네트워크 오류: {exc}"
            return False

    async def _ping(self) -> bool:
        """POST /nodes/{nodeId}/ping to keep alive."""
        url = f"{API_BASE}/nodes/{self.node_id}/ping"
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status == 200:
                        self.total_pings += 1
                        self.connection_issue = None
                        return True
                    else:
                        text = await resp.text()
                        logger.warning(f"[bless] ping 실패 (status={resp.status}): {text}")
                        self.connection_issue = f"ping 실패 (HTTP {resp.status})"
                        return False
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning(f"[bless] ping 네트워크 오류: {exc}")
            self.connection_issue = f"ping 네트워크 오류: {exc}"
            return False

    async def _maintain_connection(self) -> None:
        """Background loop: register → start session → ping every 60s."""
        retry_count = 0
        while self.status == "running":
            try:
                # Step 1: Register node
                if not self.node_registered:
                    registered = await self._register_node()
                    if not registered:
                        retry_count += 1
                        delay = min(2**retry_count, 120)
                        logger.warning(f"[bless] 등록 재시도 in {delay}s (시도 {retry_count})")
                        await asyncio.sleep(delay)
                        continue

                # Step 2: Start session
                if not self.session_active:
                    started = await self._start_session()
                    if not started:
                        retry_count += 1
                        delay = min(2**retry_count, 120)
                        logger.warning(f"[bless] 세션 시작 재시도 in {delay}s (시도 {retry_count})")
                        await asyncio.sleep(delay)
                        continue

                # Step 3: Ping loop
                while self.status == "running" and self.session_active:
                    success = await self._ping()
                    if success:
                        retry_count = 0
                    else:
                        # Ping failed — try to restart session next iteration
                        self.session_active = False
                        break
                    await asyncio.sleep(PING_INTERVAL)

                # If we broke out of the ping loop, reset and re-register
                self.session_active = False
                self.node_registered = False
                delay = min(2**retry_count, 120) if retry_count > 0 else 5
                logger.info(f"[bless] 연결 재설정, {delay}s 후 재시도")
                await asyncio.sleep(delay)

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.session_active = False
                self.node_registered = False
                retry_count += 1
                delay = min(2**retry_count, 300)
                logger.warning(f"[bless] 연결 루프 오류 ({exc}), {delay}s 후 재시도")
                self.connection_issue = str(exc)
                await asyncio.sleep(delay)

    def _get_cpu_count(self) -> int:
        """Get number of CPU cores."""
        try:
            import os
            return os.cpu_count() or 1
        except Exception:
            return 1

    async def get_status(self) -> dict[str, Any]:
        uptime = 0.0
        if self.start_time:
            uptime = (datetime.now(timezone.utc) - self.start_time).total_seconds() / 3600

        if self.status != "running":
            return {
                "status": self.status,
                "connected": False,
                "uptime_hours": uptime,
                "total_pings": self.total_pings,
                "total_bls": self.total_pings * EARNINGS_PER_PING,
                "session_active": self.session_active,
                "node_registered": self.node_registered,
                "connection_issue": self.connection_issue,
            }

        return {
            "status": self.status,
            "connected": self.session_active and self.node_registered,
            "uptime_hours": uptime,
            "total_pings": self.total_pings,
            "total_bls": round(self.total_pings * EARNINGS_PER_PING, 4),
            "session_active": self.session_active,
            "node_registered": self.node_registered,
            "connection_issue": self.connection_issue,
            "node_id": self.node_id,
        }

    async def get_earnings(self) -> dict[str, Any]:
        bls_earned = round(self.total_pings * EARNINGS_PER_PING, 4)
        usd = bls_earned * float(self.config.get("bls_to_usd", 0.01))
        await self.save_earnings(bls_earned, "BLS", usd)
        return {
            "bls_earned": bls_earned,
            "total_pings": self.total_pings,
            "usd_estimate": round(usd, 4),
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
