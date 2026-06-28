"""
统一的异步 HTTP RPC 客户端。

取代原先 aiohttp / requests / curl 三套并存的混乱：
所有对 A2 各服务的调用都从这里出去，统一处理超时、日志、错误、URL 编码。

关于 URL 编码（原项目踩过的坑）：
  运动控制 channel 的 URL 含 %2F / %3A 等「已编码」字符。aiohttp 默认会
  先解码再重建 URL，把 %3A 变回 :，导致机器人路径不匹配（404）。
  原项目为此降级到同步 requests / curl 子进程——但那会阻塞事件循环。
  正确做法：用 yarl.URL(url, encoded=True) 告诉 aiohttp「这串已经编码好了，
  原样发送，别再动它」。这样既保持全异步、不阻塞 loop，又不破坏编码。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import aiohttp
from yarl import URL

log = logging.getLogger("a2.http")


class RpcResponse:
    """一次 RPC 的结果。不抛异常，失败信息打包返回。"""

    __slots__ = ("ok", "status", "text", "json")

    def __init__(self, ok: bool, status: int, text: str, json: Optional[dict]):
        self.ok = ok
        self.status = status
        self.text = text
        self.json = json

    def contains(self, needle: str) -> bool:
        """文本包含判断（兼容旧工具用字符串匹配判定成功的逻辑）。"""
        return needle in (self.text or "")


class HttpClient:
    """进程级共享的异步 HTTP 客户端。整个 Agent 复用一个 session。"""

    def __init__(self, headers: Dict[str, str], timeout: float):
        self._headers = headers
        self._timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self) -> None:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self._timeout)
            self._session = aiohttp.ClientSession(timeout=timeout, headers=self._headers)
            log.info("HttpClient session 已创建 (timeout=%.1fs)", self._timeout)

    async def stop(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            log.info("HttpClient session 已关闭")

    async def post(self, url: str, payload: dict) -> RpcResponse:
        """
        发一个 JSON POST。
        含已编码字符（%2F/%3A）的 URL 用 encoded=True 原样发送，全程异步不阻塞。
        """
        await self.start()
        assert self._session is not None

        # 含预编码字符的 URL 用 encoded=True，否则交给 aiohttp 正常处理
        target: Any = URL(url, encoded=True) if ("%2F" in url or "%3A" in url) else url

        try:
            async with self._session.post(target, json=payload) as resp:
                text = await resp.text()
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = None
                ok = resp.status == 200
                log.info("RPC %s -> %s | %s", url, resp.status, text[:300])
                return RpcResponse(ok=ok, status=resp.status, text=text, json=data)
        except Exception as e:  # 网络 / 超时
            log.error("RPC %s 失败: %s", url, e)
            return RpcResponse(ok=False, status=-1, text=str(e), json=None)


# ── 进程级单例 ───────────────────────────────────────────────────────────────
# 在 config 之外构造，避免循环依赖；实际参数在 get_client() 首次调用时注入。
_client: Optional[HttpClient] = None


def get_client() -> HttpClient:
    """获取共享客户端（懒构造）。配置从 config 读取，集中一处。"""
    global _client
    if _client is None:
        import config
        _client = HttpClient(headers=config.HTTP_HEADERS, timeout=config.HTTP_TIMEOUT)
    return _client
