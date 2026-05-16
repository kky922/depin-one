from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil

from nodes.base_node import BaseNode


class RivalzNode(BaseNode):
    def __init__(self, config: dict[str, Any], db: Any = None, notifier: Any = None) -> None:
        super().__init__("rivalz", config, db, notifier)
        self.pid: int | None = None
        self.log_file = Path("logs/rivalz_runtime.log")

    def _get_subprocess_env(self) -> dict[str, str]:
        """launchd 환경에서 node를 찾을 수 있도록 PATH를 보강."""
        env = os.environ.copy()
        extra_paths = ["/opt/homebrew/bin", "/usr/local/bin"]
        current_path = env.get("PATH", "")
        for p in extra_paths:
            if p not in current_path:
                current_path = f"{p}:{current_path}"
        env["PATH"] = current_path
        return env

    async def start(self) -> None:
        if not self.config.get("enabled", True):
            self.status = "stopped"
            return
        rivalz_bin = self._resolve_rivalz_bin()
        if not rivalz_bin:
            self.status = "error"
            self.is_real_data = False
            self.connection_issue = "rivalz CLI 없음 → npm install -g rivalz 필요"
            return
        self.log_file.parent.mkdir(exist_ok=True)
        with self.log_file.open("a", encoding="utf-8") as f:
            proc = subprocess.Popen(
                [rivalz_bin, "run"],
                stdout=f,
                stderr=f,
                env=self._get_subprocess_env(),
            )
            self.pid = proc.pid
            # 프로세스가 즉시 종료되지 않았는지 확인
            import asyncio
            await asyncio.sleep(2)
            if proc.poll() is not None:
                # 프로세스가 바로 종료됨 → 실패
                self.status = "error"
                self.is_real_data = False
                self.connection_issue = f"rivalz run 즉시 종료됨 (exit code: {proc.returncode})"
                self.pid = None
                return
            self.status = "running"
            self.start_time = datetime.now(timezone.utc)
            self.is_real_data = False  # CLI 출력 파싱 미구현 — 포인트는 항상 0
            self.connection_issue = "CLI 실행 중이나 포인트 파싱 미구현"

    async def stop(self) -> None:
        if self.pid and psutil.pid_exists(self.pid):
            psutil.Process(self.pid).terminate()
        subprocess.run(["pkill", "-f", "rivalz run"], check=False)
        self.status = "stopped"

    async def get_status(self) -> dict[str, Any]:
        if not self.config.get("enabled", True):
            self.status = "stopped"
            return {
                "status": "stopped",
                "process_pid": -1,
                "uptime_hours": 0.0,
                "points_today": 0.0,
                "points_total": 0.0,
                "hardware_score": 0.0,
                "cpu_usage": 0.0,
                "ram_usage": 0.0,
            }
        running = False
        if self.pid and psutil.pid_exists(self.pid):
            running = True
        else:
            running = any("rivalz" in (p.info.get("name") or "") for p in psutil.process_iter(["name"]))
        status = "running" if running else "stopped"
        uptime = 0.0
        if self.start_time:
            uptime = (datetime.now(timezone.utc) - self.start_time).total_seconds() / 3600
        cpu = psutil.cpu_percent(interval=None)
        ram = psutil.virtual_memory().percent
        self.status = status
        return {
            "status": status,
            "process_pid": self.pid or -1,
            "uptime_hours": uptime,
            "points_today": 0.0,
            "points_total": 0.0,
            "hardware_score": max(0.0, 100.0 - cpu * 0.5 - ram * 0.5),
            "cpu_usage": cpu,
            "ram_usage": ram,
        }

    async def get_earnings(self) -> dict[str, Any]:
        status = await self.get_status()
        riz_today = float(status.get("points_today", 0.0))
        riz_total = float(status.get("points_total", 0.0))
        usd = riz_today * float(self.config.get("riz_to_usd", 0.05))
        await self.save_earnings(riz_today, "RIZ", usd)
        return {"riz_today": riz_today, "riz_total": riz_total, "usd_estimate": usd}

    def setup_autostart_macos(self) -> Path:
        plist = Path.home() / "Library/LaunchAgents/ai.rivalz.rclient.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        rivalz_bin = self._resolve_rivalz_bin() or "/usr/local/bin/rivalz"
        plist.write_text(
            f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>ai.rivalz.rclient</string>
<key>ProgramArguments</key><array><string>{rivalz_bin}</string><string>run</string></array>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>/tmp/rivalz.log</string>
<key>StandardErrorPath</key><string>/tmp/rivalz.error.log</string>
</dict></plist>
""",
            encoding="utf-8",
        )
        return plist

    def optimize_for_macos(self) -> dict[str, float]:
        cpu_alloc = max(psutil.cpu_count() - 2, 1)
        ram_alloc = psutil.virtual_memory().available * 0.5
        disk_alloc = shutil.disk_usage("/").free * 0.3
        return {"cpu_cores": float(cpu_alloc), "ram_bytes": ram_alloc, "disk_bytes": disk_alloc}

    def _resolve_rivalz_bin(self) -> str | None:
        system_bin = shutil.which("rivalz")
        if system_bin:
            return system_bin
        local_bin = Path("node_modules/.bin/rivalz")
        if local_bin.exists():
            return str(local_bin.resolve())
        shared_local_bin = Path("/Users/kangkuyun/stock_bot/node_modules/.bin/rivalz")
        if shared_local_bin.exists():
            return str(shared_local_bin)
        return None
