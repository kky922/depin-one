
from __future__ import annotations

import asyncio
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
from loguru import logger

from nodes.base_node import BaseNode

# Chrome Local Storage LevelDB for dashboard.teneo.pro
CHROME_LS_PATH = Path.home() / "Library/Application Support/Google/Chrome/Default/Local Storage/leveldb"
TENEO_API_BASE = "https://api.teneo.pro"
TENEO_API_KEY = "OwAG3kib1ivOJG4Y0OCZ8lJETa6ypvsDtGmdhcjB"
DASHBOARD_URL = "https://dashboard.teneo.pro"


def _read_teneo_token_from_chrome() -> str:
    """Chrome LocalStorage LevelDB에서 Teneo JWT 토큰 읽기."""
    try:
        log_files = sorted(CHROME_LS_PATH.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
        for lf in log_files[:5]:
            result = subprocess.run(
                ["strings", str(lf)],
                capture_output=True, text=True, timeout=10,
            )
            m = re.search(r'"token":"(eyJ[^"]+)"', result.stdout)
            if m:
                return m.group(1)
    except Exception as exc:
        logger.warning(f"[teneo] Chrome LocalStorage 읽기 실패: {exc}")
    return ""


def _is_token_valid(token: str) -> bool:
    """JWT exp 클레임 확인."""
    if not token:
        return False
    try:
        import base64
        parts = token.split(".")
        if len(parts) != 3:
            return False
        payload = parts[1] + "=="
        data = json.loads(base64.urlsafe_b64decode(payload))
        exp = data.get("exp", 0)
        return exp > datetime.now(timezone.utc).timestamp()
    except Exception:
        return False


class TeneoNode(BaseNode):
    is_real_data = True

    def __init__(self, config: dict[str, Any], db: Any = None, notifier: Any = None) -> None:
        super().__init__("teneo", config, db, notifier)
        self._token: str = ""
        self._cached_total: float = 0.0
        self._cached_today: float = 0.0

    async def start(self) -> None:
        if not self.config.get("enabled", True):
            self.status = "stopped"
            return
        self.start_time = datetime.now(timezone.utc)
        self.status = "running"
        # 1) Chrome LocalStorage 확인
        self._token = _read_teneo_token_from_chrome()
        if _is_token_valid(self._token):
            self.connection_issue = None
            logger.info("[teneo] Chrome 토큰 유효, API 연결 준비")
            return
        # 2) Chrome에 없으면 Playwright 로그인 시도
        logger.info("[teneo] Chrome 토큰 없음/만료, Playwright 로그인 시도")
        if await self._playwright_login():
            self.connection_issue = None
            logger.info("[teneo] Playwright 로그인 성공")
        else:
            self.connection_issue = "Teneo 로그인 실패 — dashboard.teneo.pro 에서 수동 로그인 후 재시작 필요"
            logger.warning("[teneo] 토큰 없음 또는 만료 (Chrome+Playwright 모두 실패)")

    async def stop(self) -> None:
        self.status = "stopped"

    async def _playwright_login(self) -> bool:
        """Playwright로 Teneo 로그인 시도 (Cloudflare Turnstile 있으면 실패 가능)."""
        email = self.config.get("email", "")
        password = self.config.get("password", "")
        if not email or not password:
            return False
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                captured_token: list[str] = []

                async def on_request(req: Any) -> None:
                    auth = req.headers.get("authorization", "")
                    if auth.startswith("Bearer ") and "teneo.pro" in req.url:
                        captured_token.append(auth.replace("Bearer ", ""))

                page.on("request", on_request)
                await page.goto(DASHBOARD_URL + "/auth", timeout=30000)
                await asyncio.sleep(3)
                await page.fill('input[name="email"]', email)
                await page.fill('input[name="password"]', password)
                await asyncio.sleep(1)
                try:
                    await page.click('button:has-text("Login")', timeout=5000)
                except Exception:
                    pass
                # Turnstile 해결을 기다리며 충분히 대기
                await asyncio.sleep(15)
                await browser.close()

                if captured_token:
                    self._token = captured_token[0]
                    logger.info("[teneo] Playwright 로그인 + 토큰 캡처 성공")
                    return True
                logger.warning("[teneo] Playwright 로그인 실패 (Turnstile 차단 추정)")
                return False
        except Exception as exc:
            logger.warning(f"[teneo] Playwright 로그인 예외: {exc}")
            return False

    async def _fetch_stats(self) -> dict[str, Any]:
        # 토큰이 없으면 다시 Chrome에서 읽기 시도
        if not self._token or not _is_token_valid(self._token):
            self._token = _read_teneo_token_from_chrome()
            if not _is_token_valid(self._token):
                self.connection_issue = "Teneo 토큰 만료 — dashboard.teneo.pro 재로그인 필요"
                return {}
        try:
            headers = {
                "Authorization": f"Bearer {self._token}",
                "x-api-key": TENEO_API_KEY,
            }
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"{TENEO_API_BASE}/api/users/stats",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self.connection_issue = None
                        return data
                    logger.warning(f"[teneo] stats status={resp.status}")
        except Exception as exc:
            logger.warning(f"[teneo] API error: {exc}")
        return {}

    async def get_status(self) -> dict[str, Any]:
        uptime = 0.0
        if self.start_time:
            uptime = (datetime.now(timezone.utc) - self.start_time).total_seconds() / 3600

        stats = await self._fetch_stats()
        if stats:
            self._cached_total = float(stats.get("totalPoints", stats.get("total_points", 0)))
            self._cached_today = float(stats.get("todayPoints", stats.get("today_points", 0)))

        return {
            "status": self.status,
            "connected": bool(stats),
            "uptime_hours": uptime,
            "today_points": self._cached_today,
            "total_points": self._cached_total,
            "connection_issue": self.connection_issue,
            "token_valid": _is_token_valid(self._token),
        }

    async def get_earnings(self) -> dict[str, Any]:
        stats = await self._fetch_stats()
        if stats:
            self._cached_total = float(stats.get("totalPoints", stats.get("total_points", 0)))
            self._cached_today = float(stats.get("todayPoints", stats.get("today_points", 0)))
        usd = self._cached_total * float(self.config.get("teneo_point_to_usd", 0.001))
        await self.save_earnings(self._cached_total, "TENEO_POINTS", usd)
        return {
            "teneo_points_today": self._cached_today,
            "teneo_points_total": self._cached_total,
            "usd_estimate": usd,
        }
