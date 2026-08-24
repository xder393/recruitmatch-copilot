# ruff: noqa: E501
"""chunk 策略 A/B 评估（真实文档 + 答案关键词）。

对单个真实文档，分别用「旧策略：100 字符硬切」和「新策略：500 字符递归切」切块，
用真实 BGE 检索，统计检索到的块是否包含答案关键词：

  - 答案命中@1：Top-1 块包含答案关键词的比例（对 chunk 质量最敏感）
  - 答案命中@3：Top-3 块拼接后包含答案关键词的比例
  - MRR@3：答案关键词首次出现的排名的倒数均值

用法：
  python scripts/eval_chunk_ab.py --doc data/数据结构.txt --questions scripts/questions_数据结构.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.embeddings.embedder import Embedder  # noqa: E402
from app.rag.loader import Segment  # noqa: E402
from app.rag.splitter import TextSplitter  # noqa: E402
from app.storage.vector_store import Chunk, VectorStore, make_chunk_id  # noqa: E402


def char_chunk(text: str, chunk_size: int = 100, overlap: int = 20) -> list[str]:
    """旧策略：按字符硬切（对应重构前的 ingest.chunk_text）。"""
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def build_index(strategy: str, source: str, text: str, embedder: Embedder) -> VectorStore:
    store = VectorStore(embedder)
    if strategy == "old":
        chunks = [
            Chunk(
                id=make_chunk_id(source, None, i),
                text=p,
                source=source,
                chunk_index=i,
                metadata={"source": source},
            )
            for i, p in enumerate(char_chunk(text, 100, 20))
        ]
    else:
        chunks = TextSplitter(500, 100).split([Segment(text=text, source=source)])
    store.add(chunks)
    return store


def evaluate(store: VectorStore, questions: list[dict], top_k: int = 3) -> dict:
    cov1 = cov3 = 0
    mrr = 0.0
    for q in questions:
        results = store.search(q["question"], top_k=top_k, threshold=0.0)
        texts = [r.chunk.text for r in results]

        if texts and q["keyword"] in texts[0]:
            cov1 += 1
        if q["keyword"] in " ".join(texts):
            cov3 += 1
        for rank, t in enumerate(texts, start=1):
            if q["keyword"] in t:
                mrr += 1.0 / rank
                break

    n = len(questions)
    return {
        "chunks": len(store),
        "cov@1": cov1 / n,
        "cov@3": cov3 / n,
        "mrr@3": mrr / n,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--doc", required=True)
    parser.add_argument("--questions", required=True)
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()

    with open(args.doc, encoding="utf-8") as f:
        text = f.read()
    source = os.path.basename(args.doc)
    with open(args.questions, encoding="utf-8") as f:
        questions = json.load(f)

    embedder = Embedder(os.getenv("OPENAI_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5"))
    print(f"文档：{source}（{len(text)} 字符），问题 {len(questions)} 条，Top-K={args.top_k}\n")
    print(f"{'策略':<24}{'块数':<7}{'答案命中@1':<12}{'答案命中@3':<12}{'MRR@3':<8}")

    for label, strategy in [("旧：100字符硬切", "old"), ("新：500字符递归切", "new")]:
        m = evaluate(build_index(strategy, source, text, embedder), questions, args.top_k)
        print(f"{label:<24}{m['chunks']:<7}{m['cov@1']:<12.4f}{m['cov@3']:<12.4f}{m['mrr@3']:<8.4f}")


if __name__ == "__main__":
    main()
