import pytest

from app.config import ConfigError, Settings


def test_ai_is_disabled_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AI_ENABLED", raising=False)
    settings = Settings.load()
    assert settings.ai_enabled is False


def test_enabled_ai_requires_api_key(monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        Settings.load()
