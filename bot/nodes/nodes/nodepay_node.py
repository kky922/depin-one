from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import datetime
from typing import Any
from uuid import uuid4

import aiohttp
import websockets
from loguru import logger

from nodes.base_node import BaseNode


class NodepayNode(BaseNode):
    # Nodepay APIs may be served behind multiple hostnames.
    # Prefer api.nodepay.ai when available, but fallback to app/nodepay domains.
    API_HOSTS = ("https://api.nodepay.ai", "https://app.nodepay.ai", "https://nodepay.ai")
    LOGIN_PATH = "/api/auth/login"
    EARN_PATH = "/api/earn/info"

    def __init__(self, config: dict[str, Any], db: Any = None, notifier: Any = None) -> None:
        super().__init__("nodepay", config, db, notifier)
        self.ws_task: asyncio.Task[Any] | None = None
        self.token: str = ""
        self.ws_connected = False
        self.session_count = 0

    async def start(self) -> None:
        if not self.config.get("enabled", True):
            self.status = "stopped"
            return
        self.status = "running"
        self.start_time = datetime.utcnow()
        if not self.ws_task or self.ws_task.done():
            self.ws_task = asyncio.create_task(self._maintain_connection())

    async def stop(self) -> None:
        self.status = "stopped"
        if self.ws_task:
            self.ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.ws_task
        self.ws_connected = False

    async def _login(self) -> str:
        accounts = self.config.get("accounts", [])
        if not accounts:
            return ""
        account = accounts[0]
        payload = {"email": account.get("email", ""), "password": account.get("password", "")}
        if not payload["email"] or not payload["password"]:
            return ""
        async def _do_login() -> str:
            last_exc: Exception | None = None
            had_non_exception_response = False
            for base in self.API_HOSTS:
                url = f"{base}{self.LOGIN_PATH}"
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(url, json=payload, timeout=10) as resp:
                            if resp.status != 200:
                                had_non_exception_response = True
                                continue
                            data = await resp.json()
                            token = data.get("token") or data.get("access_token", "")
                            if token:
                                return token
                            had_non_exception_response = True
                except Exception as exc:
                    last_exc = exc
                    continue
            # If we managed to reach any host (even if it returned 4xx),
            # treat it as an auth/endpoint issue, not a hard network error.
            if not had_non_exception_response and last_exc:
                raise last_exc
            return ""

        return await self._retry(_do_login, max_attempts=2, delay=2)

    async def _maintain_connection(self) -> None:
        retry_count = 0
        while self.status == "running":
            try:
                if not self.token:
                    self.token = await self._login()
                headers = {"Authorization": f"Bearer {self.token}"} if self.token else None
                async with websockets.connect(self.config.get("ws_url"), extra_headers=headers) as ws:
                    self.ws_connected = True
                    self.session_count += 1
                    retry_count = 0
                    while self.status == "running":
                        await ws.send(json.dumps({"type": "PING", "id": str(uuid4())}))
                        await ws.recv()
                        await asyncio.sleep(int(self.config.get("ping_interval", 30)))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.ws_connected = False
                retry_count += 1
                delay = min(2**retry_count, 300)
                logger.warning(f"nodepay ws reconnect in {delay}s: {exc}")
                await asyncio.sleep(delay)

    async def get_status(self) -> dict[str, Any]:
        if not self.config.get("enabled", True):
            return {
                "status": "stopped",
                "ws_connected": False,
                "today_nc": 0.0,
                "total_nc": 0.0,
                "session_count": self.session_count,
            }
        data = {
            "status": self.status,
            "ws_connected": self.ws_connected,
            "today_nc": 0.0,
            "total_nc": 0.0,
            "session_count": self.session_count,
        }
        if self.token:
            try:
                for base in self.API_HOSTS:
                    url = f"{base}{self.EARN_PATH}"
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(
                                url, headers={"Authorization": f"Bearer {self.token}"}, timeout=10
                            ) as resp:
                                if resp.status == 200:
                                    payload = await resp.json()
                                    data["today_nc"] = float(payload.get("today_nc", 0.0))
                                    data["total_nc"] = float(payload.get("total_nc", 0.0))
                                    break
                    except Exception:
                        continue
            except Exception as exc:
                logger.warning(f"nodepay earnings api unavailable: {exc}")
        return data

    async def get_earnings(self) -> dict[str, Any]:
        status = await self.get_status()
        nc_today = float(status["today_nc"])
        nc_total = float(status["total_nc"])
        usd = nc_today * float(self.config.get("nc_to_usd", 0.01))
        await self.save_earnings(nc_today, "NC", usd)
        return {"nc_today": nc_today, "nc_total": nc_total, "usd_estimate": usd}
