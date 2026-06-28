"""
离线灌库脚本：把文本文档切块、嵌入、构建 FAISS 索引。

用法:
    python -m capabilities.knowledge.ingest <文档目录或文件...>
支持 .txt / .md。按段落切块（可调），每块带来源文件名。
构建一次即可，运行时只读加载。
"""

from __future__ import annotations

import logging
import os
import sys
from typing import List

import config
from capabilities.knowledge._vectorstore import get_store

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("a2.kb.ingest")


def _split_text(text: str, max_chars: int = None) -> List[str]:
    """按空行分段，过长的段再按句号切，控制每块长度。"""
    max_chars = max_chars or config.KB_CHUNK_CHARS
    blocks, cur = [], ""
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if len(cur) + len(para) <= max_chars:
            cur = (cur + "\n" + para).strip()
        else:
            if cur:
                blocks.append(cur)
            # 单段过长再细切
            if len(para) > max_chars:
                for i in range(0, len(para), max_chars):
                    blocks.append(para[i:i + max_chars])
                cur = ""
            else:
                cur = para
    if cur:
        blocks.append(cur)
    return blocks


def _gather_files(paths: List[str]) -> List[str]:
    files = []
    for p in paths:
        if os.path.isdir(p):
            for root, _d, names in os.walk(p):
                for n in names:
                    if n.endswith((".txt", ".md")):
                        files.append(os.path.join(root, n))
        elif p.endswith((".txt", ".md")):
            files.append(p)
    return files


def main(paths: List[str]):
    files = _gather_files(paths)
    if not files:
        log.error("没有找到 .txt/.md 文件: %s", paths)
        return
    chunks = []
    for fp in files:
        with open(fp, encoding="utf-8") as f:
            text = f.read()
        src = os.path.basename(fp)
        for blk in _split_text(text):
            chunks.append({"text": blk, "source": src})
    log.info("共 %d 个文件，切出 %d 块，开始构建…", len(files), len(chunks))
    get_store().build(chunks)
    log.info("完成。索引位于 %s", config.KB_INDEX_DIR)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python -m capabilities.knowledge.ingest <目录或文件...>")
        sys.exit(1)
    main(sys.argv[1:])
