from pathlib import Path


def test_legacy_rag_is_absent_from_production_scope():
    root = Path(__file__).parents[2]
    forbidden = ("LEGACY_RAG", "app.rag", "app.agents", "ConversationStore", "VectorStore")
    files = list((root / "app").rglob("*.py")) + [root / "pyproject.toml", root / ".env.example"]
    text = "\n".join(path.read_text() for path in files if path.exists())
    assert not [token for token in forbidden if token in text]
