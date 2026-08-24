"""知识库检索工具 + 计算器工具：供 LangChain Agent 调用。"""

from __future__ import annotations

from langchain_core.tools import tool

from app.rag.retriever import Retriever, format_context
from app.storage.vector_store import SearchResult
from app.tools.calculator import safe_calc


def make_knowledge_search_tool(retriever: Retriever, retrieval_log: list[SearchResult], top_k: int):
    """返回一个绑定了 retriever 的检索工具；命中的结构化结果写入 retrieval_log。"""

    @tool
    def knowledge_search(query: str) -> str:
        """在知识库中检索与 query 相关的文档片段。
        当用户询问已上传资料里的内容、概念、事实时调用此工具。
        参数 query 应是一个独立的、明确的检索关键词或问句。"""
        results = retriever.retrieve(query, top_k=top_k)
        retrieval_log.extend(results)
        if not results:
            return "（知识库中未检索到相关内容，请如实告知用户资料里没有该信息）"
        return format_context(results)

    return knowledge_search


def make_calculator_tool():
    @tool
    def calculator(expression: str) -> str:
        """计算一个数学表达式，例如 "2+3*4"、"sqrt(9)"、"2**10"。
        当用户要求算术 / 数值计算时调用。仅支持 + - * / // % ** 与常见数学函数。"""
        try:
            return str(safe_calc(expression))
        except Exception as exc:  # ToolExecutionError 等，转成字符串返回给 LLM
            return f"计算错误：{exc}"

    return calculator
