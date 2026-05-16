from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
from typing import Any

import aiohttp
import cloudscraper
from loguru import logger

from nodes.base_node import BaseNode

API_BASE = "https://api.openloop.so"
PING_INTERVAL = 120  # seconds
EARNINGS_PER_PING = 0.01  # OPEN per ping


class OpenLoopNode(BaseNode):
    is_real_data = True

    def __init__(self, config: dict[str, Any], db: Any = None, notifier: Any = None) -> None:
        super().__init__("openloop", config, db, notifier)
        self.email: str = config.get("email", "")
        self.password: str = config.get("password", "")
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.ping_task: asyncio.Task[Any] | None = None
        self.total_pings: int = 0
        self.session_active: bool = False
        self._scraper: cloudscraper.CloudScraper | None = None

    @property
    def scraper(self) -> cloudscraper.CloudScraper:
        if self._scraper is None:
            self._scraper = cloudscraper.create_scraper()
        return self._scraper

    async def start(self) -> None:
        if not self.config.get("enabled", True):
            self.status = "stopped"
            return
        if not self.email or not self.password:
            self.status = "error"
            self.connection_issue = "이메일/비번 없음"
            logger.warning("[openloop] 로그인 정보 없음")
            return
        self.start_time = datetime.now(timezone.utc)
        self.status = "running"
        self.connection_issue = None

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

    async def _login(self) -> bool:
        """POST /users/login to get access token."""
        url = f"{API_BASE}/users/login"
        payload = {"username": self.email, "password": self.password}
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Origin": "chrome-extension://effapmdildnpkiaeghlkicpfflpiambm",
        }
        try:
            loop = asyncio.get_running_loop()
            resp = await loop.run_in_executor(
                None, lambda: self.scraper.post(url, json=payload, headers=headers, timeout=15)
            )
            data = resp.json()
            if resp.status == 200 and "data" in data:
                self.access_token = data["data"].get("accessToken")
                self.refresh_token = data["data"].get("refreshToken")
                logger.info(f"[openloop] 로그인 성공")
                return True
            else:
                logger.warning(f"[openloop] 로그인 실패 (status={resp.status}): {data}")
                self.connection_issue = f"로그인 실패"
                return False
        except Exception as exc:
            logger.warning(f"[openloop] 로그인 오류: {exc}")
            self.connection_issue = f"로그인 오류: {exc}"
            return False

    async def _profile(self) -> dict | None:
        """GET /users/profile to verify token."""
        if not self.access_token:
            return None
        url = f"{API_BASE}/users/profile"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Origin": "chrome-extension://effapmdildnpkiaeghlkicpfflpiambm",
        }
        try:
            loop = asyncio.get_running_loop()
            resp = await loop.run_in_executor(
                None, lambda: self.scraper.get(url, headers=headers, timeout=15)
            )
            if resp.status == 200:
                data = resp.json()
                return data.get("data", {})
            return None
        except Exception:
            return None

    async def _ping(self) -> bool:
        """POST /missions to earn rewards."""
        if not self.access_token:
            return False
        url = f"{API_BASE}/missions"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Origin": "chrome-extension://effapmdildnpkiaeghlkicpfflpiambm",
        }
        payload = {"action": "ping"}
        try:
            loop = asyncio.get_running_loop()
            resp = await loop.run_in_executor(
                None, lambda: self.scraper.post(url, json=payload, headers=headers, timeout=15)
            )
            if resp.status == 200:
                self.total_pings += 1
                self.connection_issue = None
                return True
            elif resp.status == 401:
                # Token expired, try re-login
                logger.info("[openloop] 토큰 만료, 재로그인 시도")
                if await self._login():
                    return await self._ping()
                return False
            else:
                text = resp.text[:200]
                logger.warning(f"[openloop] ping 실패 (status={resp.status}): {text}")
                self.connection_issue = f"ping 실패"
                return False
        except Exception as exc:
            logger.warning(f"[openloop] ping 오류: {exc}")
            self.connection_issue = str(exc)
            return False

    async def _maintain_connection(self) -> None:
        """Background loop: login → profile (verify) → ping loop."""
        retry_count = 0
        while self.status == "running":
            try:
                if not self.access_token:
                    logged_in = await self._login()
                    if not logged_in:
                        retry_count += 1
                        delay = min(2**retry_count, 120)
                        await asyncio.sleep(delay)
                        continue

                # Verify token with profile check
                profile = await self._profile()
                if not profile:
                    self.access_token = None
                    retry_count += 1
                    await asyncio.sleep(30)
                    continue

                self.session_active = True
                retry_count = 0

                # Ping loop
                while self.status == "running" and self.session_active:
                    success = await self._ping()
                    if not success:
                        self.session_active = False
                        break
                    await asyncio.sleep(PING_INTERVAL)

                # Reset on ping loop exit
                self.session_active = False
                delay = min(2**retry_count, 120) if retry_count > 0 else 5
                await asyncio.sleep(delay)

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.session_active = False
                retry_count += 1
                delay = min(2**retry_count, 300)
                logger.warning(f"[openloop] 연결 루프 오류 ({exc}), {delay}s 후 재시도")
                self.connection_issue = str(exc)
                await asyncio.sleep(delay)

    async def get_status(self) -> dict[str, Any]:
        uptime = 0.0
        if self.start_time:
            uptime = (datetime.now(timezone.utc) - self.start_time).total_seconds() / 3600
        return {
            "status": self.status,
            "method": "rest-api",
            "connected": self.session_active and bool(self.access_token),
            "uptime_hours": uptime,
            "total_pings": self.total_pings,
            "total_open": round(self.total_pings * EARNINGS_PER_PING, 4),
            "connection_issue": self.connection_issue,
        }

    async def get_earnings(self) -> dict[str, Any]:
        open_earned = round(self.total_pings * EARNINGS_PER_PING, 4)
        usd = open_earned * float(self.config.get("open_to_usd", 0.05))
        await self.save_earnings(open_earned, "OPEN", usd)
        return {
            "open_tokens": open_earned,
            "total_pings": self.total_pings,
            "usd_estimate": round(usd, 4),
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
