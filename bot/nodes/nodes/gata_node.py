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

# Gata app URL
GATA_APP_URL = "https://app.gata.net"
GATA_API_BASE = "https://app.gata.net/api"
# Chrome LocalStorage for Gata tokens
CHROME_LS_PATH = Path.home() / "Library/Application Support/Google/Chrome/Default/Local Storage/leveldb"
GATA_TOKEN_CACHE = Path(__file__).resolve().parent.parent / "data" / "gata_token.txt"


def _read_gata_token_cache() -> str:
    """로컬 캐시 파일에서 Gata 토큰 읽기 (Chrome LevelDB 압축 문제 우회)."""
    try:
        if GATA_TOKEN_CACHE.exists():
            token = GATA_TOKEN_CACHE.read_text().strip()
            if token:
                return token
    except Exception as exc:
        logger.warning(f"[gata] 토큰 캐시 읽기 실패: {exc}")
    return ""


def _save_gata_token_cache(token: str) -> None:
    """Gata 토큰을 로컬 캐시 파일에 저장."""
    try:
        GATA_TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        GATA_TOKEN_CACHE.write_text(token.strip())
        logger.info("[gata] 토큰 캐시 저장 완료")
    except Exception as exc:
        logger.warning(f"[gata] 토큰 캐시 저장 실패: {exc}")


def _read_gata_token_from_chrome() -> str:
    """Chrome LocalStorage LevelDB에서 Gata JWT 토큰 읽기."""
    try:
        log_files = sorted(CHROME_LS_PATH.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
        # 최신 파일 먼저 검색
        seen_tokens: set[str] = set()
        for lf in log_files[:10]:
            result = subprocess.run(
                ["strings", str(lf)],
                capture_output=True, text=True, timeout=10,
            )
            stdout = result.stdout
            # Gata 토큰 패턴 — eyJhbGciOiJIUzI1Ni... 형식의 JWT + key_user 포함
            for m in re.finditer(r'(eyJhbGciOiJIUzI1Ni[^"]{50,})', stdout):
                token = m.group(1)
                # key_user (지갑 주소 포함)이 들어간 토큰 = Gata auth token
                if 'key_user' in token or 'gata' in stdout.lower():
                    if token not in seen_tokens:
                        seen_tokens.add(token)
                        try:
                            import base64, json
                            parts = token.split(".")
                            if len(parts) >= 2:
                                payload = parts[1] + "=="
                                data = json.loads(base64.urlsafe_b64decode(payload))
                                exp = data.get("exp", 0)
                                if exp > 0:  # JWT-like with expiry
                                    return token
                        except Exception:
                            continue
            # "token":"..." 패턴도 확인
            for m in re.finditer(r'"token":"(eyJ[^"]+)"', stdout):
                token = m.group(1)
                if token not in seen_tokens:
                    seen_tokens.add(token)
                    try:
                        import base64, json
                        parts = token.split(".")
                        if len(parts) >= 2:
                            payload = parts[1] + "=="
                            data = json.loads(base64.urlsafe_b64decode(payload))
                            if data.get("exp", 0) > 0:
                                return token
                    except Exception:
                        continue
    except Exception as exc:
        logger.warning(f"[gata] Chrome LocalStorage 읽기 실패: {exc}")
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


class GataNode(BaseNode):
    is_real_data = True

    def __init__(self, config: dict[str, Any], db: Any = None, notifier: Any = None) -> None:
        super().__init__("gata", config, db, notifier)
        self._token: str = ""
        self._cached_points: float = 0.0
        self._cached_dva_jobs: int = 0
        self._wallet_address: str = config.get("wallet_address", "")
        self._private_key: str = config.get("private_key", "")

    async def start(self) -> None:
        if not self.config.get("enabled", True):
            self.status = "stopped"
            return
        self.start_time = datetime.now(timezone.utc)
        self.status = "running"

        # 1) 로컬 캐시 파일 확인 (가장 빠름)
        self._token = _read_gata_token_cache()
        if _is_token_valid(self._token):
            self.connection_issue = None
            logger.info("[gata] 캐시 토큰 유효, API 연결 준비")
            return

        # 2) Chrome LocalStorage 확인
        self._token = _read_gata_token_from_chrome()
        if _is_token_valid(self._token):
            self.connection_issue = None
            _save_gata_token_cache(self._token)
            logger.info("[gata] Chrome 토큰 유효, 캐시 저장 완료")
            return

        # 3) private key로 web3 인증 시도
        if self._private_key:
            logger.info("[gata] private_key 있음, web3 인증 시도")
            if await self._web3_auth():
                self.connection_issue = None
                _save_gata_token_cache(self._token)
                logger.info("[gata] web3 인증 성공, 캐시 저장 완료")
                return

        # 3) Playwright 로그인 시도 (wallet connect 방식)
        logger.info("[gata] 토큰 없음, Playwright 로그인 시도")
        if await self._playwright_login():
            self.connection_issue = None
            _save_gata_token_cache(self._token)
            logger.info("[gata] Playwright 로그인 성공, 캐시 저장 완료")
        else:
            self.connection_issue = (
                "Gata 로그인 실패 — app.gata.net 에서 수동 로그인 후 재시작 필요, "
                "또는 config에 wallet_address + private_key 설정 필요"
            )
            logger.warning("[gata] 토큰 없음 (Chrome+web3+Playwright 모두 실패)")

    async def stop(self) -> None:
        self.status = "stopped"

    async def _web3_auth(self) -> bool:
        """wallet private key로 Gata API 인증."""
        pk = self._private_key
        if not pk:
            return False
        try:
            from eth_account.messages import encode_defunct
            from web3 import Web3

            w3 = Web3()
            acct = w3.eth.account.from_key(pk)
            addr = acct.address
            if self._wallet_address and self._wallet_address.lower() != addr.lower():
                logger.warning(f"[gata] wallet_address mismatch: config={self._wallet_address}, derived={addr}")
                return False

            # 1) nonce 요청
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"{GATA_API_BASE}/auth/nonce",
                    json={"walletAddress": addr},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"[gata] nonce 요청 실패: {resp.status}")
                        return False
                    nonce_data = await resp.json()
                    nonce = nonce_data.get("nonce", "")

                # 2) 메시지 서명
                message = encode_defunct(text=nonce)
                signed = acct.sign_message(message)
                signature = signed.signature.hex()

                # 3) 인증
                async with s.post(
                    f"{GATA_API_BASE}/auth/verify",
                    json={
                        "walletAddress": addr,
                        "signature": f"0x{signature}",
                    },
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"[gata] auth verify 실패: {resp.status}")
                        return False
                    auth_data = await resp.json()
                    self._token = auth_data.get("token") or auth_data.get("accessToken", "")
                    if self._token:
                        logger.info("[gata] web3 인증 + 토큰 발급 성공")
                        return True
                    logger.warning("[gata] web3 인증 응답에 토큰 없음")
                    return False
        except Exception as exc:
            logger.warning(f"[gata] web3 인증 예외: {exc}")
            return False

    async def _playwright_login(self) -> bool:
        """Playwright로 Gata 웹앱 로그인 시도."""
        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                captured_token: list[str] = []

                async def on_request(req: Any) -> None:
                    auth = req.headers.get("authorization", "")
                    if auth.startswith("Bearer ") and "gata.net" in req.url:
                        captured_token.append(auth.replace("Bearer ", ""))
                    # X-API-Key 형태도 확인
                    x_key = req.headers.get("x-api-key", "")
                    if x_key and "gata.net" in req.url:
                        captured_token.append(x_key)

                page.on("request", on_request)
                await page.goto(GATA_APP_URL, timeout=30000)
                await asyncio.sleep(5)

                # 이미 로그인되어 있는지 체크
                current_url = page.url
                if "dataAgent" in current_url or "earnings" in current_url:
                    await asyncio.sleep(3)
                    await browser.close()
                    if captured_token:
                        self._token = captured_token[0]
                        logger.info("[gata] Playwright 페이지 로드 + 토큰 캡처 성공")
                        return True

                # MetaMask 버튼 클릭
                try:
                    mm_btn = page.locator('button:has-text("MetaMask")')
                    if await mm_btn.is_visible(timeout=3000):
                        await mm_btn.click()
                        await asyncio.sleep(5)
                except Exception:
                    pass

                await asyncio.sleep(10)
                await browser.close()

                if captured_token:
                    self._token = captured_token[0]
                    logger.info("[gata] Playwright 로그인 + 토큰 캡처 성공")
                    return True

                logger.warning("[gata] Playwright 로그인 실패")
                return False
        except Exception as exc:
            logger.warning(f"[gata] Playwright 로그인 예외: {exc}")
            return False

    async def _fetch_stats(self) -> dict[str, Any]:
        """Gata API에서 DVA 상태 및 포인트 조회."""
        if not self._token or not _is_token_valid(self._token):
            self._token = _read_gata_token_from_chrome()
            if not _is_token_valid(self._token):
                self.connection_issue = "Gata 토큰 만료 — app.gata.net 재로그인 필요"
                return {}

        try:
            headers = {
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            }
            async with aiohttp.ClientSession() as s:
                # DVA 상태 조회
                async with s.get(
                    f"{GATA_API_BASE}/dva/status",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self.connection_issue = None
                        return data
                    elif resp.status == 401:
                        self.connection_issue = "Gata 토큰 만료 — 재인증 필요"
                        logger.warning("[gata] 토큰 만료 (401)")
                        # 재인증 시도
                        if self._private_key:
                            await self._web3_auth()
                        return {}
                    logger.warning(f"[gata] dva/status: {resp.status}")

                # 포인트 조회
                async with s.get(
                    f"{GATA_API_BASE}/user/points",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        points_data = await resp.json()
                        return {"points_data": points_data}
        except Exception as exc:
            logger.warning(f"[gata] API error: {exc}")
        return {}

    async def _start_dva_job(self) -> bool:
        """DVA 작업 시작 (active하게 작업 수행)."""
        if not self._token or not _is_token_valid(self._token):
            return False
        try:
            headers = {
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            }
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"{GATA_API_BASE}/dva/start",
                    headers=headers,
                    json={},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        logger.info("[gata] DVA 작업 시작 성공")
                        return True
                    logger.warning(f"[gata] DVA 작업 시작 실패: {resp.status}")
                    return False
        except Exception as exc:
            logger.warning(f"[gata] DVA start error: {exc}")
            return False

    async def get_status(self) -> dict[str, Any]:
        uptime = 0.0
        if self.start_time:
            uptime = (datetime.now(timezone.utc) - self.start_time).total_seconds() / 3600

        stats = await self._fetch_stats()
        points_data: dict = {}
        dva_status: dict = {}

        if isinstance(stats, dict):
            if "points_data" in stats:
                points_data = stats["points_data"]
            else:
                dva_status = stats

        self._cached_points = float(
            points_data.get("totalPoints", points_data.get("total_points", 0))
            or dva_status.get("points", 0)
        )
        self._cached_dva_jobs = int(
            points_data.get("dvaJobs", dva_status.get("jobs", 0))
        )

        return {
            "status": self.status,
            "connected": bool(points_data or dva_status),
            "uptime_hours": uptime,
            "points": self._cached_points,
            "dva_jobs": self._cached_dva_jobs,
            "dva_running": dva_status.get("running", False),
            "connection_issue": self.connection_issue,
            "token_valid": _is_token_valid(self._token),
        }

    async def get_earnings(self) -> dict[str, Any]:
        stats = await self._fetch_stats()
        points_data: dict = {}
        dva_status: dict = {}

        if isinstance(stats, dict):
            if "points_data" in stats:
                points_data = stats["points_data"]
            else:
                dva_status = stats

        self._cached_points = float(
            points_data.get("totalPoints", points_data.get("total_points", 0))
            or dva_status.get("points", 0)
        )
        usd = self._cached_points * float(self.config.get("gata_point_to_usd", 0.001))
        await self.save_earnings(self._cached_points, "GATA_POINTS", usd)
        return {
            "gata_points": self._cached_points,
            "dva_jobs": self._cached_dva_jobs,
            "usd_estimate": usd,
        }
