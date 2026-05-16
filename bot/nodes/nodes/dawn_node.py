from __future__ import annotations

import asyncio
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from nodes.base_node import BaseNode

# Dawn 확장 ID
DAWN_EXT_ID = "fpdkjdnhkakefebpekbdhillbhonfjjp"
LEVELDB_PATH = Path.home() / "Library/Application Support/Google/Chrome/Default/Local Extension Settings" / DAWN_EXT_ID / "000003.log"


def _read_dawn_points_from_local() -> tuple[float, float]:
    """Chrome 확장 LevelDB 파일에서 Dawn 포인트 직접 읽기."""
    if not LEVELDB_PATH.exists():
        return 0.0, 0.0
    try:
        result = subprocess.run(
            ["strings", str(LEVELDB_PATH)],
            capture_output=True, text=True, timeout=10,
        )
        lines = result.stdout.splitlines()
        points_values = []
        referral_values = []
        for line in lines:
            m = re.search(r'"points"\s*:\s*(\d+)', line)
            if m:
                points_values.append(int(m.group(1)))
            m2 = re.search(r'"referral_points"\s*:\s*(\d+)', line)
            if m2:
                referral_values.append(int(m2.group(1)))
        total = float(points_values[-1]) if points_values else 0.0
        referral = float(referral_values[-1]) if referral_values else 0.0
        return total, referral
    except Exception as exc:
        logger.warning(f"[dawn] LevelDB 읽기 실패: {exc}")
        return 0.0, 0.0


class DAWNNode(BaseNode):
    is_real_data = True

    def __init__(self, config: dict[str, Any], db: Any = None, notifier: Any = None) -> None:
        super().__init__("dawn", config, db, notifier)
        self.today_points = 0.0
        self.total_points = 0.0
        self._start_points = 0.0

    async def start(self) -> None:
        if not self.config.get("enabled", True):
            self.status = "stopped"
            return
        self.start_time = datetime.now(timezone.utc)
        self.status = "running"
        pts, _ = _read_dawn_points_from_local()
        self._start_points = pts
        self.total_points = pts
        if LEVELDB_PATH.exists():
            self.connection_issue = None
            logger.info(f"[dawn] LevelDB 읽기 성공, 현재 포인트: {pts}")
        else:
            self.connection_issue = "Dawn 확장 LevelDB 파일 없음 (Chrome 확장 설치 필요)"
            logger.warning("[dawn] LevelDB 없음")

    async def stop(self) -> None:
        self.status = "stopped"

    async def get_status(self) -> dict[str, Any]:
        uptime = 0.0
        if self.start_time:
            uptime = (datetime.now(timezone.utc) - self.start_time).total_seconds() / 3600

        pts, referral = _read_dawn_points_from_local()
        self.total_points = pts
        self.today_points = max(0.0, pts - self._start_points)

        return {
            "status": self.status,
            "connected": LEVELDB_PATH.exists(),
            "uptime_hours": uptime,
            "today_points": self.today_points,
            "total_points": self.total_points,
            "referral_points": referral,
            "source": "local_leveldb",
        }

    async def get_earnings(self) -> dict[str, Any]:
        pts, referral = _read_dawn_points_from_local()
        self.total_points = pts
        self.today_points = max(0.0, pts - self._start_points)
        usd = self.total_points * float(self.config.get("dawn_point_to_usd", 0.001))
        await self.save_earnings(self.total_points, "DAWN_POINTS", usd)
        return {
            "points_today": self.today_points,
            "points_total": self.total_points,
            "referral_points": referral,
            "usd_estimate": usd,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
