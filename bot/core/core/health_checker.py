from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nodes.base_node import BaseNode


class NodeHealthReport:
    def __init__(self, name: str) -> None:
        self.name = name
        self.ok: bool = True
        self.level: str = "ok"  # ok | warn | error | disabled
        self.detail: str = ""

    def warn(self, msg: str) -> "NodeHealthReport":
        self.ok = False
        self.level = "warn"
        self.detail = msg
        return self

    def error(self, msg: str) -> "NodeHealthReport":
        self.ok = False
        self.level = "error"
        self.detail = msg
        return self

    def disabled(self) -> "NodeHealthReport":
        self.ok = False
        self.level = "disabled"
        self.detail = "비활성화"
        return self

    @property
    def emoji(self) -> str:
        return {"ok": "✅", "warn": "⚠️", "error": "❌", "disabled": "⏸️"}.get(self.level, "❓")


class HealthChecker:
    def __init__(self, nodes: dict[str, "BaseNode"], telegram_ok: bool = True, telegram_error: str = "") -> None:
        self.nodes = nodes
        self.telegram_ok = telegram_ok
        self.telegram_error = telegram_error

    def check_all(self) -> list[NodeHealthReport]:
        reports: list[NodeHealthReport] = []
        for name, node in self.nodes.items():
            reports.append(self._check_node(name, node))
        return reports

    def _check_node(self, name: str, node: Any) -> NodeHealthReport:
        r = NodeHealthReport(name)
        enabled = getattr(node, "config", {}).get("enabled", True)
        if not enabled:
            return r.disabled()

        status = node.status
        conn_issue = getattr(node, "connection_issue", None)

        if name == "grass":
            app_exists = Path("/Applications/Grass.app").exists()
            api_token = node.config.get("api_token") or ""
            if not app_exists and not api_token:
                return r.error("앱 미설치 + API 토큰 없음 → .env GRASS_API_TOKEN 필요")
            if not api_token:
                return r.warn("API 토큰 없음 → 포인트 조회 불가 (.env GRASS_API_TOKEN)")
            if not app_exists:
                return r.warn("Grass.app 미설치 → API 모드로만 동작")

        elif name == "rivalz":
            bin_path = shutil.which("rivalz")
            local_bin = Path("node_modules/.bin/rivalz")
            if not bin_path and not local_bin.exists():
                return r.error("rivalz CLI 없음 → npm install -g rivalz 필요")

        elif name in ("dawn", "gradient", "openloop"):
            return r.warn("시뮬레이션 모드 (Playwright 미설정, 실제 수익 아님)")

        elif name == "teneo":
            ws_connected = getattr(node, "ws_connected", False)
            reconnect = getattr(node, "reconnect_count", 0)
            if not ws_connected:
                return r.error(f"WS 연결 끊김 (재연결 {reconnect}회)")

        elif name == "nodepay":
            if status == "stopped" or conn_issue:
                return r.error(conn_issue or "403 오류 (계정 확인 필요)")

        if conn_issue:
            return r.warn(conn_issue)
        if status == "error":
            return r.error("오류 상태")
        if status == "stopped":
            return r.warn("중지됨")
        return r

    def format_report(self, uptime_data: dict[str, float] | None = None) -> str:
        reports = self.check_all()
        lines = ["🔍 <b>연결 상태 진단</b>", "━━━━━━━━━━━━━━━━━━━━━━"]

        tg_emoji = "✅" if self.telegram_ok else "❌"
        tg_detail = "" if self.telegram_ok else f" ({self.telegram_error})"
        lines.append(f"{tg_emoji} Telegram Bot   연결{'정상' if self.telegram_ok else '실패'}{tg_detail}")

        node_labels = {
            "grass": "🌿 Grass     ",
            "rivalz": "⚙️ Rivalz   ",
            "dawn": "🌅 DAWN      ",
            "gradient": "📶 Gradient ",
            "teneo": "🌀 Teneo     ",
            "openloop": "🔄 OpenLoop ",
            "nodepay": "💳 Nodepay  ",
        }

        error_count = 0
        warn_count = 0
        for r in reports:
            label = node_labels.get(r.name, r.name)
            uptime_str = ""
            if uptime_data and r.level == "ok":
                hrs = uptime_data.get(r.name, 0.0)
                h = int(hrs)
                m = int((hrs - h) * 60)
                uptime_str = f" (uptime {h}h {m}m)"
            lines.append(f"{r.emoji} {label}  {r.detail}{uptime_str}")
            if r.level == "error":
                error_count += 1
            elif r.level == "warn":
                warn_count += 1

        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"조치 필요: {error_count}개 | 경고: {warn_count}개")
        if not self.telegram_ok:
            lines.append("⚠️ 텔레그램 미연결 상태 — 이 메시지는 로컬 로그에만 기록됩니다")
        return "\n".join(lines)
