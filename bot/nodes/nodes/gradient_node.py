from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

import aiohttp
from loguru import logger

from nodes.base_node import BaseNode


class GradientNode(BaseNode):
    API_BASE = "https://api.gradient.network"
    DASHBOARD_URL = "https://app.gradient.network"
    is_real_data = True

    def __init__(self, config: dict[str, Any], db: Any = None, notifier: Any = None) -> None:
        super().__init__("gradient", config, db, notifier)
        self._token: str = ""
        self._cached: dict[str, Any] = {}
        self._consecutive_failures: int = 0
        self._last_failure_log: float = 0.0

    async def start(self) -> None:
        if not self.config.get("enabled", True):
            self.status = "stopped"
            return
        self.start_time = datetime.now(timezone.utc)
        self.status = "running"
        await self._refresh_token()

    async def stop(self) -> None:
        self.status = "stopped"
        self._token = ""

    async def _refresh_token(self) -> bool:
        email = self.config.get("email", "")
        password = self.config.get("password", "")
        if not email or not password:
            self.connection_issue = "이메일/비밀번호 없음"
            return False
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                captured_token: list[str] = []

                async def on_request(req: Any) -> None:
                    auth = req.headers.get("authorization", "")
                    if auth.startswith("Bearer ") and "gradient.network" in req.url:
                        captured_token.append(auth.replace("Bearer ", ""))

                page.on("request", on_request)
                await page.goto(self.DASHBOARD_URL, timeout=30000)
                await asyncio.sleep(2)
                await page.fill('input[placeholder="Enter Email"]', email)
                await page.fill('input[placeholder="Enter Password"]', password)
                await page.click('button:has-text("Log In")')
                await asyncio.sleep(8)
                await browser.close()

                if captured_token:
                    self._token = captured_token[0]
                    self.connection_issue = None
                    logger.info("[gradient] 토큰 갱신 완료")
                    return True
                self.connection_issue = "로그인 실패 (토큰 미캡처)"
                return False
        except Exception as exc:
            logger.warning(f"[gradient] 토큰 갱신 실패: {exc}")
            self.connection_issue = str(exc)[:80]
            return False

    async def _fetch_profile(self) -> dict[str, Any]:
        if not self._token:
            return {}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"{self.API_BASE}/api/user/profile",
                    headers={"Authorization": f"Bearer {self._token}"},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self._consecutive_failures = 0
                        return data.get("data", {})
                    if resp.status in (401, 403):
                        self._consecutive_failures += 1
                        now = time.time()
                        # 5분에 한 번만 로그 출력
                        if now - self._last_failure_log > 300:
                            logger.warning(f"[gradient] {resp.status} 에러 (연속 {self._consecutive_failures}회), 토큰 갱신 시도")
                            self._last_failure_log = now
                        if await self._refresh_token():
                            return await self._fetch_profile()
                        self.connection_issue = f"{resp.status} 에러, 토큰 갱신 실패"
                    else:
                        self._consecutive_failures += 1
                        logger.warning(f"[gradient] profile status={resp.status}")
        except Exception as exc:
            logger.warning(f"[gradient] API error: {exc}")
        return {}

    async def get_status(self) -> dict[str, Any]:
        uptime = 0.0
        if self.start_time:
            uptime = (datetime.now(timezone.utc) - self.start_time).total_seconds() / 3600

        profile = await self._fetch_profile()
        if profile:
            self._cached = profile
            self.connection_issue = None

        point = self._cached.get("point", {})
        node = self._cached.get("node", {})
        return {
            "status": self.status,
            "connected": bool(profile),
            "uptime_hours": uptime,
            "today_points": float(point.get("today", 0)),
            "total_points": float(point.get("total", 0)),
            "balance": float(point.get("balance", 0)),
            "sentry_active": int(node.get("sentryActive", 0)),
            "username": self._cached.get("name", ""),
        }

    async def get_earnings(self) -> dict[str, Any]:
        status = await self.get_status()
        total = float(status.get("total_points", 0.0))
        today = float(status.get("today_points", 0.0))
        usd = total * float(self.config.get("grad_point_to_usd", 0.001))
        await self.save_earnings(total, "GRAD_POINTS", usd)
        return {
            "grad_points_today": today,
            "grad_points_total": total,
            "usd_estimate": usd,
        }
