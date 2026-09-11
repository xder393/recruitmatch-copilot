from pathlib import Path
import subprocess


def test_console_contains_transparent_ai_surfaces():
    html = Path("web/index.html").read_text(encoding="utf-8")
    for text in [
        "AI 状态",
        "招聘知识库",
        "规则分",
        "语义补充分",
        "证据不足",
        "重新索引",
        "result.grounded_explanation",
        "result.interview_questions",
        "item.content",
        "最终招聘决定由招聘人员作出",
    ]:
        assert text in html
    for endpoint in ["/ai/status", "/knowledge-documents", "mode=hybrid-v1"]:
        assert endpoint in html


def test_console_renders_semantic_score_in_its_native_hundred_point_scale():
    """Executes the real renderer; catches 80 displaying as 8000 and NULL as zero."""
    result = subprocess.run(["node", "tests/ui/render_matches_scores.cjs"], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
