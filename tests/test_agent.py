"""Agent：工具装配、结果解析（tool_calls / sources / 去重）、历史转换。"""

from __future__ import annotations

from langchain_core.agents import AgentAction

from app.agents.agent import AgentResult, RagAgent, _to_lc_messages
from app.storage.vector_store import SearchResult


class _FakeExecutor:
    def __init__(self, output, steps):
        self._output = output
        self._steps = steps

    def invoke(self, _input):
        return {"output": self._output, "intermediate_steps": self._steps}


def test_to_lc_messages():
    msgs = _to_lc_messages([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}])
    assert msgs[0].type == "human" and msgs[0].content == "hi"
    assert msgs[1].type == "ai" and msgs[1].content == "hello"


def test_run_extracts_tool_calls(monkeypatch, retriever):
    agent = RagAgent(llm=None, retriever=retriever)
    monkeypatch.setattr(
        agent,
        "_build_executor",
        lambda log: _FakeExecutor(
            "答案是 4",
            [(AgentAction(tool="calculator", tool_input={"expression": "2+2"}, log=""), "4")],
        ),
    )
    result = agent.run("2+2 等于几")
    assert result.answer == "答案是 4"
    assert result.tool_calls == ["calculator"]


def test_make_tools_has_two_tools(retriever):
    agent = RagAgent(llm=None, retriever=retriever)
    tools = agent._make_tools([])
    names = {t.name for t in tools}
    assert names == {"knowledge_search", "calculator"}


def test_dedup_sources(store):
    results = store.search("链表", top_k=1)
    r = AgentResult(answer="x", sources=results * 2)
    r.dedup_sources()
    assert len(r.sources) == 1


def test_knowledge_search_tool_records_sources(retriever):
    from app.tools.knowledge import make_knowledge_search_tool

    log = []
    tool = make_knowledge_search_tool(retriever, log, top_k=1)
    out = tool.invoke({"query": "链表"})
    assert "链表" in out
    assert len(log) == 1
    assert isinstance(log[0], SearchResult)
