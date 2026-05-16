from __future__ import annotations

import asyncio
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
from loguru import logger

from nodes.base_node import BaseNode


class GrassNode(BaseNode):
    API_BASE = "https://api.getgrass.io"
    is_real_data = True

    def __init__(self, config: dict[str, Any], db: Any = None, notifier: Any = None) -> None:
        super().__init__("grass", config, db, notifier)
        self._cached: dict[str, Any] = {}

    async def start(self) -> None:
        if not self.config.get("enabled", True):
            self.status = "stopped"
            return
        api_token = self.config.get("api_token") or ""
        if not api_token:
            self.status = "error"
            self.is_real_data = False
            self.connection_issue = "API 토큰 없음 (.env GRASS_API_TOKEN)"
            logger.warning("[grass] API 토큰 없음")
            return
        self.start_time = datetime.now(timezone.utc)
        self.status = "running"
        self.connection_issue = None

    async def stop(self) -> None:
        subprocess.run(["pkill", "-f", "Grass"], check=False)
        self.status = "stopped"

    async def _fetch_user(self) -> dict[str, Any]:
        token = self.config.get("api_token") or ""
        if not token:
            return {}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.API_BASE}/retrieveUser",
                    headers={"Authorization": token},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("result", {}).get("data", {})
                    logger.warning(f"[grass] retrieveUser status={resp.status}")
        except Exception as exc:
            logger.warning(f"[grass] API error: {exc}")
        return {}

    async def get_status(self) -> dict[str, Any]:
        uptime = 0.0
        if self.start_time:
            uptime = (datetime.now(timezone.utc) - self.start_time).total_seconds() / 3600

        if self.status != "running":
            return {
                "status": self.status,
                "connected": False,
                "uptime_hours": uptime,
                "today_points": 0.0,
                "total_points": 0.0,
                "connection_issue": self.connection_issue,
            }

        user = await self._fetch_user()
        if user:
            self._cached = user
            self.connection_issue = None
        else:
            self.connection_issue = "API 응답 없음"

        return {
            "status": self.status,
            "connected": bool(user),
            "uptime_hours": uptime,
            "today_points": float(self._cached.get("totalPoints", 0)),
            "total_points": float(self._cached.get("totalPoints", 0)),
            "desktop_points": float(self._cached.get("desktopPoints", 0)),
            "total_uptime_sec": int(self._cached.get("totalUptime", 0)),
            "username": self._cached.get("username", ""),
            "wallet": self._cached.get("walletAddress", ""),
        }

    async def get_earnings(self) -> dict[str, Any]:
        status = await self.get_status()
        points = float(status.get("total_points", 0.0))
        usd = points * float(self.config.get("point_to_usd", 0.001))
        await self.save_earnings(points, "GRASS", usd)
        return {
            "points_today": points,
            "points_total": points,
            "usd_estimate": usd,
            "username": status.get("username", ""),
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
