from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from loguru import logger
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError  # python-telegram-bot v13+


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str) -> None:
        self.token = token
        self.chat_id = chat_id
        self.bot = Bot(token=token) if token else None
        self._verified: bool | None = None  # None=미검사, True=정상, False=오류
        self._verify_error: str = ""
        self._restart_cooldowns: dict[str, float] = {}  # 노드별 재시작 알림 쿨다운
        self._cooldown_seconds: int = 600  # 10분 쿨다운

    # ──────────────────────────────────────────────
    # 연결 검증
    # ──────────────────────────────────────────────

    async def verify_connection(self) -> bool:
        """봇 토큰 및 Chat ID 유효성 검사. 결과를 캐시."""
        if not self.bot or not self.chat_id:
            self._verified = False
            self._verify_error = "토큰 또는 Chat ID 없음"
            return False
        try:
            me = await self.bot.get_me()
            logger.info(f"[Telegram] 봇 확인: @{me.username}")
        except TelegramError as exc:
            self._verified = False
            self._verify_error = f"봇 토큰 오류: {exc}"
            logger.error(f"[Telegram] 토큰 검증 실패: {exc}")
            return False

        try:
            await self.bot.send_chat_action(chat_id=self.chat_id, action="typing")
            self._verified = True
            self._verify_error = ""
            logger.info(f"[Telegram] Chat ID {self.chat_id} 정상 확인")
            return True
        except TelegramError as exc:
            self._verified = False
            self._verify_error = str(exc)
            logger.error(
                f"[Telegram] Chat ID {self.chat_id!r} 오류: {exc}\n"
                "→ 올바른 Chat ID 확인: 봇에게 메시지를 보내고 "
                "https://api.telegram.org/bot<TOKEN>/getUpdates 에서 chat.id 확인"
            )
            return False

    # ──────────────────────────────────────────────
    # 발송
    # ──────────────────────────────────────────────

    async def send(self, message: str) -> None:
        if not self.bot or not self.chat_id:
            logger.info(f"[Telegram disabled] {message}")
            return
        chunks = [message[i : i + 4096] for i in range(0, len(message), 4096)]
        for chunk in chunks:
            await self._retry_send(chunk)

    async def _retry_send(self, message: str, retries: int = 3) -> None:
        for i in range(retries):
            try:
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=message,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                return
            except Exception as exc:
                logger.warning(f"Telegram send failed ({i + 1}/{retries}): {exc}")
                if i == retries - 1:
                    raise
                await asyncio.sleep(2)

    # ──────────────────────────────────────────────
    # 상태 카드
    # ──────────────────────────────────────────────

    def _format_status_card(
        self,
        statuses: dict[str, dict[str, Any]],
        nodes_map: dict[str, Any] | None = None,
    ) -> str:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [f"📊 <b>DePIN 노드 현황</b> ({now})", "━━━━━━━━━━━━━━━━━━━━━━"]
        node_labels = {
            "grass": "🌿 Grass    ",
            "rivalz": "⚙️ Rivalz  ",
            "dawn": "🌅 DAWN     ",
            "gradient": "📶 Gradient",
            "teneo": "🌀 Teneo   ",
            "openloop": "🔄 OpenLoop",
            "nodepay": "💳 Nodepay ",
        }
        ordered = ["grass", "rivalz", "dawn", "gradient", "teneo", "openloop", "nodepay"]
        sim_nodes = set()

        for name in ordered:
            st = statuses.get(name)
            if st is None:
                continue
            label = node_labels.get(name, name)
            status = st.get("status", "unknown")
            node_obj = (nodes_map or {}).get(name)
            is_real = getattr(node_obj, "is_real_data", False)
            conn_issue = getattr(node_obj, "connection_issue", None)
            enabled = (node_obj.config.get("enabled", True) if node_obj else True)

            if not enabled or status == "stopped" and not conn_issue:
                status_icon = "⏸️"
                pts_str = ""
                conn_str = "비활성"
            elif status == "running":
                status_icon = "✅"
                pts = st.get("today_points", st.get("points_today", 0.0))
                pts_sim = "★" if not is_real else ""
                pts_str = f"pts: {pts:.2f}{pts_sim}"
                if not is_real:
                    sim_nodes.add(name)
                if conn_issue:
                    conn_str = f"⚠️ {conn_issue}"
                elif is_real:
                    conn_str = "✅ 실제연결"
                else:
                    conn_str = "⚠️ 시뮬"
            else:
                status_icon = "❌"
                pts_str = ""
                conn_str = conn_issue or status

            parts = [p for p in [pts_str, conn_str] if p]
            detail = " | ".join(parts)
            lines.append(f"{status_icon} {label}  {detail}")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        if sim_nodes:
            lines.append("★ = 시뮬레이션 수치 (실제 수익 아님)")
        return "\n".join(lines)

    # ──────────────────────────────────────────────
    # 수익 카드
    # ──────────────────────────────────────────────

    def _format_earnings_card(
        self,
        earnings_data: dict[str, Any],
        rewards_data: dict[str, dict[str, Any]] | None = None,
        nodes_map: dict[str, Any] | None = None,
        enabled_nodes: list[str] | None = None,
    ) -> str:
        rewards_data = rewards_data or {}
        enabled = set(enabled_nodes or ["grass", "rivalz", "dawn", "gradient", "teneo", "openloop"])
        labels = {
            "grass": "🌿 Grass    ",
            "rivalz": "⚙️ Rivalz  ",
            "dawn": "🌅 DAWN     ",
            "gradient": "📶 Gradient",
            "teneo": "🌀 Teneo   ",
            "openloop": "🔄 OpenLoop",
            "nodepay": "💳 Nodepay ",
        }
        ordered = ["grass", "rivalz", "dawn", "gradient", "teneo", "openloop", "nodepay"]
        lines = ["💰 <b>수익 현황</b> (오늘 기준)"]

        real_total = 0.0
        sim_found = False
        for node in ordered:
            if node not in enabled:
                continue
            node_obj = (nodes_map or {}).get(node)
            is_real = getattr(node_obj, "is_real_data", False)
            conn_issue = getattr(node_obj, "connection_issue", None)

            usd = earnings_data.get(node, 0.0)
            reward = rewards_data.get(node, {})
            amount = float(reward.get("amount", 0.0))
            unit = reward.get("unit", "-")

            if is_real:
                tag = "[실제]"
                real_total += usd
            else:
                tag = "[추정★]"
                sim_found = True

            note = f" → {conn_issue}" if conn_issue else ""
            lines.append(
                f"{labels.get(node, node)}  ${usd:.4f}  {tag}  {amount:.2f} {unit}{note}"
            )

        total = earnings_data.get("total_usd", 0.0)
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"오늘 합계:  ${total:.4f}  (실제확인분: ${real_total:.4f})")
        if sim_found:
            lines.append("★ 주의: 시뮬레이션 수치 포함 — 실제 수익과 다를 수 있음")
        return "\n".join(lines)

    # ──────────────────────────────────────────────
    # 기존 호환 메서드
    # ──────────────────────────────────────────────

    async def send_node_status(self, node_name: str, status: str) -> None:
        # 같은 노드의 running 알림에 10분 쿨다운 적용 (재시작 루프 스팸 방지)
        if status == "running":
            now = time.time()
            last = self._restart_cooldowns.get(node_name, 0)
            if now - last < self._cooldown_seconds:
                logger.info(f"[Telegram] {node_name} 상태 알림 쿨다운 중 (남은 {int(self._cooldown_seconds - (now - last))}초)")
                return
            self._restart_cooldowns[node_name] = now

        emoji = {"running": "✅", "stopped": "❌", "error": "⚠️"}.get(status, "ℹ️")
        await self.send(f"{emoji} <b>{node_name}</b>: {status}")

    async def send_earnings_update(
        self,
        earnings_data: dict[str, Any],
        rewards_data: dict[str, dict[str, Any]] | None = None,
        enabled_nodes: list[str] | None = None,
        nodes_map: dict[str, Any] | None = None,
    ) -> None:
        text = self._format_earnings_card(earnings_data, rewards_data, nodes_map, enabled_nodes)
        await self.send(text)

    async def send_alert(self, alert_type: str, message: str) -> None:
        emoji = {"error": "🚨", "warning": "⚠️", "info": "ℹ️", "success": "✅"}.get(alert_type, "ℹ️")
        await self.send(f"{emoji} {message}")

    async def send_level(self, level: str, title: str, message: str) -> None:
        prefix = {"error": "ERROR", "warning": "WARNING", "info": "INFO", "success": "SUCCESS"}.get(
            level, "INFO"
        )
        await self.send_alert(level, f"<b>[{prefix}] {title}</b>\n{message}")


# ──────────────────────────────────────────────────────────────
# 커맨드 핸들러
# ──────────────────────────────────────────────────────────────


class TelegramCommandHandler:
    def __init__(self, scheduler: Any) -> None:
        self.scheduler = scheduler

    async def handle(self, command: str) -> str:
        if command == "/status":
            statuses = await self.scheduler.get_all_status()
            nodes_map = getattr(self.scheduler, "nodes", {})
            notifier: TelegramNotifier | None = getattr(self.scheduler, "notifier", None)
            if notifier:
                card = notifier._format_status_card(statuses, nodes_map)
                await notifier.send(card)
                return card
            return str(statuses)

        if command == "/earnings":
            earnings = await self.scheduler.get_today_earnings()
            nodes_map = getattr(self.scheduler, "nodes", {})
            notifier: TelegramNotifier | None = getattr(self.scheduler, "notifier", None)
            if notifier:
                card = notifier._format_earnings_card(earnings, nodes_map=nodes_map)
                await notifier.send(card)
                return card
            return str(earnings)

        if command == "/health":
            return await self._handle_health()

        if command == "/all_nodes":
            statuses = await self.scheduler.get_all_status()
            earnings = await self.scheduler.get_today_earnings()
            return f"statuses={statuses}\nearnings={earnings}"

        if command == "/dawn_status":
            return str((await self.scheduler.get_all_status()).get("dawn", {}))
        if command == "/gradient_status":
            return str((await self.scheduler.get_all_status()).get("gradient", {}))
        if command == "/teneo_status":
            return str((await self.scheduler.get_all_status()).get("teneo", {}))
        if command == "/openloop_status":
            return str((await self.scheduler.get_all_status()).get("openloop", {}))

        if command == "/report":
            return await self.scheduler.send_daily_report()

        if command == "/restart":
            await self.scheduler.start_all_nodes(force_restart=True)
            return "restart requested"

        for node_name in ("dawn", "gradient", "teneo", "openloop"):
            if command == f"/restart_{node_name}":
                node = self.scheduler.nodes.get(node_name)
                if node:
                    await node.restart()
                    return f"{node_name} restarted"
                return f"{node_name} not enabled"

        if command == "/stop":
            await self.scheduler.stop_all_nodes()
            return "stopped"
        if command == "/start":
            await self.scheduler.start_all_nodes()
            return "started"
        if command == "/logs":
            return "use local log files in logs/"
        if command == "/uptime":
            return str(await self.scheduler.get_all_uptimes())

        return "unknown command"

    async def _handle_health(self) -> str:
        from core.health_checker import HealthChecker

        nodes_map = getattr(self.scheduler, "nodes", {})
        notifier: TelegramNotifier | None = getattr(self.scheduler, "notifier", None)

        tg_ok = True
        tg_err = ""
        if notifier:
            verified = notifier._verified
            if verified is None:
                tg_ok = await notifier.verify_connection()
                tg_err = notifier._verify_error
            else:
                tg_ok = verified
                tg_err = notifier._verify_error

        uptime_data: dict[str, float] = {}
        try:
            uptime_data = await self.scheduler.get_all_uptimes()
        except Exception:
            pass

        checker = HealthChecker(nodes_map, telegram_ok=tg_ok, telegram_error=tg_err)
        report = checker.format_report(uptime_data)

        if notifier:
            await notifier.send(report)
        return report
