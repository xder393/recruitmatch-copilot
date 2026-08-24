"""RagAgent：基于 LangChain tool-calling 的 Agent。

决策流程（由 LLM 根据工具描述自主选择，比硬编码规则更贴近真实 Agent）：
  用户问题 → LLM 判断
    ├─ 知识库问题 → 调用 knowledge_search 工具 → 拿到检索上下文 → 生成带来源的回答
    ├─ 计算问题   → 调用 calculator 工具 → 拿到结果
    └─ 普通聊天   → 直接回答（不调工具）
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from app.core.exceptions import LLMError
from app.core.logging import get_logger
from app.rag.retriever import Retriever
from app.storage.vector_store import SearchResult
from app.tools.knowledge import make_calculator_tool, make_knowledge_search_tool

logger = get_logger(__name__)

SYSTEM_PROMPT = """你是一个知识库智能问答助手。你的能力与使用规则如下：

1. 当用户询问「已上传资料」中的内容、概念、事实、细节时，必须调用 knowledge_search 工具检索，再依据检索结果回答。
2. 当用户要求做算术 / 数值计算时，调用 calculator 工具。
3. 其他与知识库无关的普通聊天、寒暄，直接简洁回答，不要调用工具。

回答要求：
- 只依据检索到的上下文回答，若上下文不足以回答，明确说「资料中没有相关内容」，绝不编造。
- 引用资料时，说明来源（文件名 / 页码）。
- 回答使用中文，条理清晰。"""


@dataclass
class AgentResult:
    """一次 Agent 执行的结构化结果。"""

    answer: str
    tool_calls: list[str] = field(default_factory=list)
    sources: list[SearchResult] = field(default_factory=list)
    latency_ms: float = 0.0

    def dedup_sources(self) -> None:
        seen: set[str] = set()
        unique: list[SearchResult] = []
        for r in self.sources:
            if r.chunk.id not in seen:
                seen.add(r.chunk.id)
                unique.append(r)
        self.sources = unique


def _to_lc_messages(history: list[dict]) -> list:
    """把 {'role','content'} 历史转成 LangChain 消息。"""
    msgs = []
    for m in history:
        if m.get("role") == "user":
            msgs.append(HumanMessage(content=m["content"]))
        elif m.get("role") == "assistant":
            msgs.append(AIMessage(content=m["content"]))
    return msgs


class RagAgent:
    def __init__(
        self,
        llm,
        retriever: Retriever,
        top_k: int = 5,
        max_iterations: int = 3,
    ):
        self._llm = llm
        self._retriever = retriever
        self._top_k = top_k
        self._max_iterations = max_iterations
        self._prompt = ChatPromptTemplate.from_messages(
            [
                ("system", SYSTEM_PROMPT),
                MessagesPlaceholder("chat_history", optional=True),
                ("human", "{input}"),
                MessagesPlaceholder("agent_scratchpad"),
            ]
        )

    def _make_tools(self, retrieval_log: list[SearchResult]):
        """构建 Agent 工具集（独立方法，便于单独测试工具装配）。"""
        return [
            make_knowledge_search_tool(self._retriever, retrieval_log, self._top_k),
            make_calculator_tool(),
        ]

    def _build_executor(self, retrieval_log: list[SearchResult]):
        tools = self._make_tools(retrieval_log)
        agent = create_tool_calling_agent(self._llm, tools, self._prompt)
        return AgentExecutor(
            agent=agent,
            tools=tools,
            max_iterations=self._max_iterations,
            return_intermediate_steps=True,
            verbose=False,
        )

    def run(self, question: str, chat_history: list[dict] | None = None) -> AgentResult:
        retrieval_log: list[SearchResult] = []
        executor = self._build_executor(retrieval_log)
        start = time.perf_counter()
        try:
            output = executor.invoke({"input": question, "chat_history": _to_lc_messages(chat_history or [])})
        except Exception as exc:
            logger.exception("Agent 执行失败: %s", exc)
            raise LLMError(f"Agent 执行失败: {exc}") from exc

        latency_ms = (time.perf_counter() - start) * 1000
        tool_calls = [action.tool for action, _ in output.get("intermediate_steps", [])]
        result = AgentResult(
            answer=output.get("output", ""),
            tool_calls=tool_calls,
            sources=retrieval_log,
            latency_ms=latency_ms,
        )
        result.dedup_sources()
        logger.info(
            "question=%r tools=%s sources=%d latency=%.1fms",
            question,
            tool_calls,
            len(result.sources),
            latency_ms,
        )
        return result
