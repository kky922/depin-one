from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from monitor.earnings_tracker import EarningsTracker
from monitor.telegram_bot import TelegramNotifier
from nodes.dawn_node import DAWNNode
from nodes.gradient_node import GradientNode
from nodes.grass_node import GrassNode
from nodes.nodepay_node import NodepayNode
from nodes.openloop_node import OpenLoopNode
from nodes.bless_node import BlessNode
from nodes.rivalz_node import RivalzNode
from nodes.teneo_node import TeneoNode
from nodes.gata_node import GataNode


class DePINScheduler:
    def __init__(self, config: dict[str, Any], db: Any) -> None:
        self.config = config
        self.db = db
        telegram_cfg = config.get("telegram", {})
        self.notifier = TelegramNotifier(telegram_cfg.get("bot_token", ""), telegram_cfg.get("chat_id", ""))
        earnings_cfg = config.get("earnings", {})
        nodes_cfg = config.get("nodes", {})
        node_map = {
            "grass": (
                GrassNode,
                {**nodes_cfg.get("grass", {}), "point_to_usd": earnings_cfg.get("grass_point_to_usd", 0.001)},
            ),
            "rivalz": (
                RivalzNode,
                {**nodes_cfg.get("rivalz", {}), "riz_to_usd": earnings_cfg.get("riz_to_usd", 0.05)},
            ),
            "dawn": (
                DAWNNode,
                {**nodes_cfg.get("dawn", {}), "dawn_point_to_usd": earnings_cfg.get("dawn_point_to_usd", 0.001)},
            ),
            "gradient": (
                GradientNode,
                {**nodes_cfg.get("gradient", {}), "grad_point_to_usd": earnings_cfg.get("grad_point_to_usd", 0.001)},
            ),
            "teneo": (
                TeneoNode,
                {**nodes_cfg.get("teneo", {}), "teneo_point_to_usd": earnings_cfg.get("teneo_point_to_usd", 0.001)},
            ),
            "openloop": (
                OpenLoopNode,
                {**nodes_cfg.get("openloop", {}), "open_to_usd": earnings_cfg.get("open_to_usd", 0.05)},
            ),
            # keep legacy Nodepay as optional and default disabled
            "nodepay": (
                NodepayNode,
                {**nodes_cfg.get("nodepay", {}), "nc_to_usd": earnings_cfg.get("nc_to_usd", 0.01)},
            ),
            "bless": (
                BlessNode,
                {**nodes_cfg.get("bless", {}), "bls_to_usd": earnings_cfg.get("bls_to_usd", 0.01)},
            ),
            "gata": (
                GataNode,
                {**nodes_cfg.get("gata", {}), "gata_point_to_usd": earnings_cfg.get("gata_point_to_usd", 0.001)},
            ),
        }
        self.nodes: dict[str, Any] = {}
        for name, (klass, node_cfg) in node_map.items():
            if node_cfg.get("enabled", False):
                self.nodes[name] = klass(node_cfg, db, self.notifier)
        self.earnings_tracker = EarningsTracker()
        self.scheduler = AsyncIOScheduler()
        self.failed_health_checks: dict[str, int] = {name: 0 for name in self.nodes}
        self._register_jobs()

    def _register_jobs(self) -> None:
        schedule_cfg = self.config.get("schedule", {})
        weekday_map = {
            "monday": "mon",
            "tuesday": "tue",
            "wednesday": "wed",
            "thursday": "thu",
            "friday": "fri",
            "saturday": "sat",
            "sunday": "sun",
        }
        raw_weekday = str(schedule_cfg.get("weekly_report_day", "mon")).lower()
        cron_weekday = weekday_map.get(raw_weekday, raw_weekday)
        self.scheduler.add_job(self.health_check_all, "interval", minutes=schedule_cfg.get("health_check_interval", 5))
        self.scheduler.add_job(self.collect_all_earnings, "interval", minutes=schedule_cfg.get("earnings_check_interval", 60))
        self.scheduler.add_job(self.check_and_restart_failed_nodes, "interval", minutes=schedule_cfg.get("restart_check_interval", 10))
        self.scheduler.add_job(self.send_daily_report, "cron", hour=schedule_cfg.get("daily_report_hour", 9), minute=0)
        self.scheduler.add_job(self.send_weekly_report, "cron", day_of_week=cron_weekday, hour=9, minute=0)

    async def start(self) -> None:
        await self.start_all_nodes()
        self.scheduler.start()

    async def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)
        await self.stop_all_nodes()

    async def start_all_nodes(self, force_restart: bool = False) -> None:
        tasks = []
        for node in self.nodes.values():
            tasks.append(node.restart() if force_restart else node.start())
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for name, result in zip(self.nodes.keys(), results):
            if isinstance(result, Exception):
                await self.db.save_alert("error", name, str(result))
                await self.notifier.send_level("error", "NodeStartFailed", f"{name}: {result}")
        try:
            await self.notifier.send_level("info", "BotStarted", "DePIN 노드 봇 시작 완료")
        except Exception as exc:
            logger.warning(f"startup telegram notification failed: {exc}")

    async def stop_all_nodes(self) -> None:
        await asyncio.gather(*(node.stop() for node in self.nodes.values()), return_exceptions=True)

    async def health_check_all(self) -> None:
        for name, node in self.nodes.items():
            # Rivalz는 root LaunchDaemon 관리 — 유저 프로세스에서 재시작 불가
            if name == "rivalz":
                continue
            healthy = await node.health_check()
            status = await node.get_status()
            await self.db.save_status(name, status.get("status", "unknown"), status.get("uptime_hours", 0.0))
            if not healthy:
                self.failed_health_checks[name] += 1
                logger.warning(f"{name} is unhealthy; restarting")
                try:
                    await node.restart()
                    if self.failed_health_checks[name] >= 3:
                        await self.notifier.send_level(
                            "warning",
                            "NodeRecoveredAfterRetries",
                            f"{name}: 헬스체크 {self.failed_health_checks[name]}회 실패 후 복구됨",
                        )
                except Exception as exc:
                    await self.db.save_alert("error", name, str(exc))
                    level = "error" if self.failed_health_checks[name] >= 3 else "warning"
                    await self.notifier.send_level(level, "NodeRestartFailed", f"{name}: {exc}")
            else:
                self.failed_health_checks[name] = 0

    async def check_and_restart_failed_nodes(self) -> None:
        for name, node in self.nodes.items():
            status = await node.get_status()
            if status.get("status") != "running":
                await node.restart()
                await self.notifier.send_level("warning", "WatchdogRestart", f"{name}: 워치독이 재시작 수행")

    async def collect_all_earnings(self) -> None:
        for name, node in self.nodes.items():
            try:
                result = await node.get_earnings()
            except Exception as exc:
                await self.db.save_alert("warning", name, f"earnings collection failed: {exc}")
                continue
            usd = float(result.get("usd_estimate", 0.0))
            self.earnings_tracker.add(name, usd)
        if datetime.utcnow().hour % 6 == 0:
            snapshot = self.earnings_tracker.snapshot()
            rewards = await self.db.get_today_reward_totals()
            await self.notifier.send_earnings_update(snapshot, rewards, enabled_nodes=list(self.nodes.keys()))

    async def send_daily_report(self) -> str:
        today = await self.db.get_today_earnings()
        rewards = await self.db.get_today_reward_totals()
        total = sum(today.values())

        def fmt_reward(node: str) -> str:
            info = rewards.get(node, {})
            amount = float(info.get("amount", 0.0))
            unit = str(info.get("unit", "-"))
            return f"{amount:.2f} {unit}"

        labels = {
            "grass": "🌿 Grass",
            "rivalz": "⚙️ Rivalz",
            "dawn": "🌅 DAWN",
            "gradient": "📶 Gradient",
            "teneo": "🌀 Teneo",
            "openloop": "🔄 OpenLoop",
            "nodepay": "📡 Nodepay",
            "bless": "✨ Bless",
            "gata": "💠 Gata",
        }
        ordered = ["grass", "rivalz", "dawn", "gradient", "teneo", "openloop", "nodepay", "bless", "gata"]
        lines = ["📊 DePIN 일별 수익 리포트", "─────────────────────────"]
        for node in ordered:
            if node in self.nodes:
                lines.append(
                    f"{labels.get(node, node):<13} ${today.get(node, 0.0):.2f} (리워드 {fmt_reward(node)})"
                )
        lines.extend(["─────────────────────────", f"💰 총 수익:   ${total:.2f}"])
        msg = "\n".join(lines)
        await self.notifier.send(msg)
        return msg

    async def send_weekly_report(self) -> None:
        weekly = await self.db.get_weekly_summary()
        await self.notifier.send(f"📅 주간 수익: ${weekly.get('weekly_total_usd', 0.0):.2f}")

    async def get_all_status(self) -> dict[str, Any]:
        return {name: await node.get_status() for name, node in self.nodes.items()}

    async def get_all_uptimes(self) -> dict[str, float]:
        uptimes = {}
        for name, node in self.nodes.items():
            status = await node.get_status()
            uptimes[name] = float(status.get("uptime_hours", 0.0))
        return uptimes

    async def get_today_earnings(self) -> dict[str, float]:
        return await self.db.get_today_earnings()
