"""
兼容垫片：保留 a2_client 的生命周期接口（start/stop），底层委托给 core.http。

历史上各处通过 `from services.a2_client import a2_client` 拿到共享客户端。
重构后真正的 HTTP 客户端在 core.http，这里只做生命周期转发，避免改动 main 的调用习惯。
新代码请直接用 core.http.get_client()。
"""

from __future__ import annotations

from core.http import get_client


class _A2ClientCompat:
    async def start(self) -> None:
        await get_client().start()

    async def stop(self) -> None:
        await get_client().stop()


a2_client = _A2ClientCompat()
