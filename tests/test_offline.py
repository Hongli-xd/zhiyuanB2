"""
离线自测：不连真机，mock 掉 HTTP 客户端，验证能力注册 / 编排逻辑。

运行: python -m tests.test_offline
"""

import asyncio
import logging

import core.http as http

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


class MockClient:
    """拦截所有 RPC，按 url 末段返回符合各工具判定逻辑的假响应。"""

    def __init__(self):
        self.calls = []

    async def start(self): ...
    async def stop(self): ...

    async def post(self, url: str, payload: dict):
        self.calls.append((url, payload))

        class R:
            status = 200
            ok = True

            def contains(self, n):
                return n in self.text

        r = R()
        if url.endswith("MigrateSystemStateSync") or url.endswith("SetCurrentTask"):
            r.text = '{"code":"0"}'
        elif url.endswith("LaunchTask"):
            r.text = '{"result":"ReturnType_SUCCEED"}'
        else:
            r.text = '{"state":"ok"}'
        return r


async def main():
    mock = MockClient()
    http._client = mock

    from core.registry import discover
    from core.dispatcher import execute
    from core.capability import get_capability

    caps = discover()
    names = sorted(c.name for c in caps)
    print(f"\n=== 自动发现能力: {names} ===")
    assert "set_status_light" in names
    assert "launch_aimmaster_task" in names
    assert "move" in names
    assert "play_motion" in names

    print("\n=== 测试1: 灯带预设 waiting ===")
    r = await execute(get_capability("set_status_light"), {"preset": "waiting"})
    print(r.ok, r.message, r.data)
    assert r.ok and r.data["applied"]["red"] == 180

    print("\n=== 测试2: 启动任务(LangGraph 三步) ===")
    r = await execute(get_capability("launch_aimmaster_task"), {"task_id": "2"})
    print(r.ok, r.message)
    assert r.ok
    # 三步 RPC + 灯带反馈
    steps = [u for u, _ in mock.calls]
    assert any("MigrateSystemStateSync" in u for u in steps)
    assert any("LaunchTask" in u for u in steps)

    print("\n=== 测试3: 未知动作模糊匹配 ===")
    r = await execute(get_capability("play_motion"), {"name": "点头"})
    print(r.ok, r.message)
    assert not r.ok and r.data.get("suggest") == "点点头"

    print("\n✅ 全部离线测试通过")


if __name__ == "__main__":
    asyncio.run(main())
