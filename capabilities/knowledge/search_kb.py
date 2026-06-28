"""
能力：知识库检索（search_knowledge_base）。

以 function calling 形式暴露给 LLM，由 LLM 自主决定何时调用——
问到「只有本场馆/本公司文档里才有的特定信息」时调用，通用闲聊不调。
检索结果作为 tool_result 回注上下文，LLM 据此用口语化方式回答，
用完即走、不长期占用上下文。

属于纯查询、无副作用，标 CONCURRENT。
"""

from __future__ import annotations

import logging

import config
from core.capability import Concurrency, tool
from core.result import ToolResult
from capabilities.knowledge._vectorstore import get_store

log = logging.getLogger("a2.cap.search_kb")


@tool(
    name="search_knowledge_base",
    description=(
        "检索本地知识库，获取本场馆/本机构特定的事实信息——"
        "例如展品介绍、营业时间、流程规定、位置导引、专有名词解释等"
        "这类 LLM 通用知识里没有、只存在于本地文档的内容。"
        "通用常识、闲聊、天气等不要调用本工具。"
        "回答时只依据检索结果，不要编造；检索不到就如实说不知道。"
    ),
    properties={
        "query": {"type": "string", "description": "要查询的问题或关键词。"},
    },
    required=["query"],
    concurrency=Concurrency.CONCURRENT,
)
async def search_knowledge_base(query: str) -> ToolResult:
    hits = get_store().search(query, top_k=config.KB_TOP_K)
    if not hits:
        return ToolResult.success("知识库中未找到相关内容。", found=False)

    # 过滤低相关（余弦相似度阈值），拼成带来源的上下文
    kept = [(c, s) for c, s in hits if s >= config.KB_MIN_SCORE]
    if not kept:
        return ToolResult.success("知识库中没有足够相关的内容。", found=False)

    passages = []
    for chunk, score in kept:
        src = chunk.get("source", "")
        passages.append(f"[来源:{src}] {chunk['text']}")
    context = "\n".join(passages)
    log.info("KB 检索「%s」命中 %d 块", query, len(kept))
    return ToolResult.success(context, found=True, hits=len(kept))
