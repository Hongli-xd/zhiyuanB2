"""
本地向量库封装（FAISS + 本地嵌入）。

设计为断网可用：嵌入模型用本地 sentence-transformers，向量检索用 FAISS，
全程不依赖网络。库文件持久化在磁盘（构建一次，运行时只读加载）。

延迟加载：嵌入模型首次检索时才加载，避免拖慢启动。
"""

from __future__ import annotations

import json
import logging
import os
from typing import List, Tuple

import config

log = logging.getLogger("a2.kb.store")


class VectorStore:
    def __init__(self, index_dir: str = None, model_name: str = None):
        self.index_dir = index_dir or config.KB_INDEX_DIR
        self.model_name = model_name or config.KB_EMBED_MODEL
        self._model = None
        self._index = None
        self._chunks: List[dict] = []   # [{"text":..., "source":...}]

    # ── 嵌入模型（延迟加载）────────────────────────────────────────────────
    def _ensure_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            log.info("加载本地嵌入模型: %s", self.model_name)
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def _embed(self, texts: List[str]):
        import numpy as np
        model = self._ensure_model()
        vecs = model.encode(texts, normalize_embeddings=True)
        return np.asarray(vecs, dtype="float32")

    # ── 构建（离线 ingest 调用）────────────────────────────────────────────
    def build(self, chunks: List[dict]):
        """chunks: [{"text":..., "source":...}]。构建 FAISS 索引并落盘。"""
        import faiss
        self._chunks = chunks
        vecs = self._embed([c["text"] for c in chunks])
        dim = vecs.shape[1]
        index = faiss.IndexFlatIP(dim)   # 归一化向量内积 = 余弦相似度
        index.add(vecs)
        self._index = index

        os.makedirs(self.index_dir, exist_ok=True)
        faiss.write_index(index, os.path.join(self.index_dir, "kb.faiss"))
        with open(os.path.join(self.index_dir, "chunks.json"), "w", encoding="utf-8") as f:
            json.dump(chunks, f, ensure_ascii=False)
        log.info("向量库已构建：%d 块 -> %s", len(chunks), self.index_dir)

    # ── 加载（运行时只读）─────────────────────────────────────────────────
    def load(self) -> bool:
        import faiss
        idx_path = os.path.join(self.index_dir, "kb.faiss")
        chunk_path = os.path.join(self.index_dir, "chunks.json")
        if not (os.path.exists(idx_path) and os.path.exists(chunk_path)):
            log.warning("向量库不存在: %s（先运行 ingest 构建）", self.index_dir)
            return False
        self._index = faiss.read_index(idx_path)
        with open(chunk_path, encoding="utf-8") as f:
            self._chunks = json.load(f)
        log.info("向量库已加载：%d 块", len(self._chunks))
        return True

    # ── 检索 ──────────────────────────────────────────────────────────────
    def search(self, query: str, top_k: int = 3) -> List[Tuple[dict, float]]:
        if self._index is None and not self.load():
            return []
        qv = self._embed([query])
        scores, idxs = self._index.search(qv, top_k)
        out = []
        for score, i in zip(scores[0], idxs[0]):
            if 0 <= i < len(self._chunks):
                out.append((self._chunks[i], float(score)))
        return out


# 进程级单例
_store: VectorStore = None


def get_store() -> VectorStore:
    global _store
    if _store is None:
        _store = VectorStore()
    return _store
