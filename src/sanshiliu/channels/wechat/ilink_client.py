"""iLink Bot REST API 异步客户端；只暴露我们用到的几个端点。"""

from __future__ import annotations

import httpx

from sanshiliu.foundation.errors import ChannelError
from sanshiliu.foundation.logging import get_logger

_logger = get_logger(__name__)


class ILinkClient:
    """httpx.AsyncClient 包装；超时统一 10s，错误归 ChannelError。"""

    def __init__(self, base_url: str, api_key: str | None = None, *, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        headers = {"User-Agent": "sanshiliu/1.0"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            base_url=self._base_url, headers=headers, timeout=timeout
        )

    @property
    def base_url(self) -> str:
        return self._base_url

    async def close(self) -> None:
        await self._client.aclose()

    async def ping(self) -> bool:
        """轻量探活；返回 True 表示 iLink 可达。"""
        try:
            r = await self._client.get("/ping")
            return r.status_code < 500
        except (httpx.HTTPError, OSError):
            return False

    async def send_text(self, to_wxid: str, text: str) -> dict:
        """发文本消息；失败抛 ChannelError。"""
        try:
            r = await self._client.post(
                "/send_text", json={"to_wxid": to_wxid, "content": text}
            )
        except httpx.HTTPError as exc:
            raise ChannelError(f"iLink /send_text 调用失败：{exc}") from exc
        if r.status_code >= 400:
            raise ChannelError(f"iLink /send_text 返回 {r.status_code}: {r.text[:200]}")
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else {}

    async def get_self_info(self) -> dict:
        """拿 bot 自己的 wxid / 昵称；启动期一次性。"""
        try:
            r = await self._client.get("/get_self_info")
        except httpx.HTTPError as exc:
            raise ChannelError(f"iLink /get_self_info 调用失败：{exc}") from exc
        if r.status_code >= 400:
            raise ChannelError(f"iLink /get_self_info 返回 {r.status_code}")
        return r.json()
