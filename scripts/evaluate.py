"""RAG 检索效果评估：Recall@K / Precision@K / Hit@K / MRR。

用法：
    python scripts/evaluate.py --questions scripts/eval_questions.sample.json

评估问题格式（JSON 数组）：
    [
      {"question": "链表如何插入节点？", "relevant_sources": ["数据结构.txt"]},
      ...
    ]

说明：
    - 只评估「检索」环节（不调 LLM），指标全部来自真实检索结果，不编造。
    - relevant_sources 表示该问题答案所在的文档，命中即算相关。
"""

from __future__ import annotations

import argparse
import json
import os

# 让 scripts 目录也能 import app 包
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Settings  # noqa: E402
from app.embeddings.embedder import Embedder  # noqa: E402
from app.rag.retriever import Retriever  # noqa: E402
from app.storage.vector_store import VectorStore  # noqa: E402


def load_questions(path: str) -> list[dict]:
    if not os.path.exists(path):
        print(f"[跳过] 未找到评估问题文件：{path}")
        print("       请参考 scripts/eval_questions.sample.json 创建自己的评估集。")
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def evaluate(retriever: Retriever, questions: list[dict], top_k: int = 5) -> dict:
    recall_sum = precision_sum = 0.0
    hit_count = 0
    mrr_sum = 0.0
    n = len(questions)

    print(f"{'问题':<32} {'命中':<4} {'Recall':<8} {'Precision':<10} {'MRR':<6}")
    print("-" * 66)

    for q in questions:
        text = q["question"]
        relevant = set(q.get("relevant_sources", []))
        results = retriever.retrieve(text, top_k=top_k)
        retrieved_sources = [r.chunk.source for r in results]

        hits = [s for s in retrieved_sources if s in relevant]
        recall = len(set(hits)) / len(relevant) if relevant else 0.0
        precision = len(set(hits)) / top_k
        hit = 1 if any(s in relevant for s in retrieved_sources) else 0

        # MRR：第一个相关结果位置的倒数
        mrr = 0.0
        for rank, s in enumerate(retrieved_sources, start=1):
            if s in relevant:
                mrr = 1.0 / rank
                break

        recall_sum += recall
        precision_sum += precision
        hit_count += hit
        mrr_sum += mrr

        print(f"{text:<32} {hit:<4} {recall:<8.3f} {precision:<10.3f} {mrr:<6.3f}")

    print("-" * 66)
    return {
        "questions": n,
        "recall_at_k": recall_sum / n if n else 0.0,
        "precision_at_k": precision_sum / n if n else 0.0,
        "hit_at_k": hit_count / n if n else 0.0,
        "mrr_at_k": mrr_sum / n if n else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG 检索效果评估")
    parser.add_argument("--questions", default="scripts/eval_questions.sample.json")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    questions = load_questions(args.questions)
    if not questions:
        return

    # 评估不需要 LLM，也不校验 API key
    embedding_model = os.getenv("OPENAI_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
    data_dir = os.getenv("DATA_DIR", "data")
    index_dir = os.getenv("INDEX_DIR", "data/index")

    embedder = Embedder(embedding_model)
    store = VectorStore(embedder)
    if not store.load(index_dir):
        print(f"[提示] 未找到索引 {index_dir}，正在从 {data_dir} 重建……")
        # 复用入库逻辑：解析 + 切块 + 向量化
        from app.rag.loader import load_file
        from app.rag.splitter import TextSplitter

        splitter = TextSplitter()
        for filename in sorted(os.listdir(data_dir)):
            if not filename.lower().endswith((".txt", ".pdf")):
                continue
            path = os.path.join(data_dir, filename)
            store.add(splitter.split(load_file(path, filename)))
        store.save(index_dir)

    if len(store) == 0:
        print("[提示] 知识库为空，请先往 data/ 放一些 txt/pdf 再评估。")
        return

    settings = Settings(api_key="unused")
    retriever = Retriever(store, settings)

    print(f"\n知识库：{len(store)} 块，评估问题：{len(questions)} 条，Top-K={args.top_k}\n")
    metrics = evaluate(retriever, questions, top_k=args.top_k)

    print("\n=== 汇总指标 ===")
    for k, v in metrics.items():
        print(f"{k}: {v:.4f}")


if __name__ == "__main__":
    main()
