"""
知识库逻辑测试（mock 向量库，不依赖 faiss/嵌入模型）。
验证：检索命中→拼上下文、低分过滤、空结果处理、切块逻辑。
运行: python -m tests.test_knowledge
"""
import asyncio
import sys
import types

# ── mock 掉 _vectorstore.get_store，避免加载 faiss/模型 ──
import capabilities.knowledge._vectorstore as vs


class FakeStore:
    def __init__(self, hits): self._hits = hits
    def search(self, query, top_k=3): return self._hits[:top_k]


def _patch_store(hits):
    vs._store = FakeStore(hits)


async def test_hit_assembles_context():
    _patch_store([
        ({"text": "本馆开放时间为周二至周日 9:00-17:00", "source": "guide.md"}, 0.82),
        ({"text": "周一闭馆维护", "source": "guide.md"}, 0.61),
    ])
    from capabilities.knowledge.search_kb import search_knowledge_base
    r = await search_knowledge_base(query="几点开门")
    assert r.ok and r.data["found"] is True
    assert "开放时间" in r.message and "来源:guide.md" in r.message
    print("  ✓ 命中→拼接带来源的上下文")


async def test_low_score_filtered():
    _patch_store([
        ({"text": "无关内容", "source": "x.md"}, 0.12),
    ])
    from capabilities.knowledge.search_kb import search_knowledge_base
    r = await search_knowledge_base(query="毫不相关的问题")
    assert r.data["found"] is False
    print("  ✓ 低于相似度阈值的结果被过滤")


async def test_no_hits():
    _patch_store([])
    from capabilities.knowledge.search_kb import search_knowledge_base
    r = await search_knowledge_base(query="空库")
    assert r.data["found"] is False
    print("  ✓ 空结果优雅处理")


def test_chunking():
    from capabilities.knowledge.ingest import _split_text
    text = "第一段内容。\n\n第二段内容。\n\n" + "很长的一段" * 100
    chunks = _split_text(text, max_chars=120)
    assert len(chunks) >= 3
    assert all(len(c) <= 120 for c in chunks)
    print(f"  ✓ 文本切块正常（{len(chunks)} 块，均≤120字）")


async def main():
    await test_hit_assembles_context()
    await test_low_score_filtered()
    await test_no_hits()
    test_chunking()
    print("\n✅ 知识库逻辑全部通过")


if __name__ == "__main__":
    asyncio.run(main())
