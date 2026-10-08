"""Conversation language policy tests (Phase 3.6)."""

from app.core.languages import (
    DEFAULT_CONVERSATION_LANGUAGE,
    SUPPORTED_CONVERSATION_LANGUAGES,
    language_display_name,
    resolve_conversation_language,
)
from app.services.grounded_ai import GroundedAIService


def _service() -> GroundedAIService:
    # _build_system_prompt is policy-only and does not touch dependencies.
    return GroundedAIService(None, None, None, None)  # type: ignore[arg-type]


def test_english_is_the_default_conversation_language():
    assert DEFAULT_CONVERSATION_LANGUAGE == "en"
    assert SUPPORTED_CONVERSATION_LANGUAGES == {"en": "English"}


def test_default_for_missing_or_blank_settings():
    assert resolve_conversation_language(None) == "en"
    assert resolve_conversation_language("") == "en"
    assert resolve_conversation_language("   ") == "en"


def test_unsupported_language_falls_back_to_english():
    assert resolve_conversation_language("bn") == "en"
    assert resolve_conversation_language("bn-BD") == "en"
    assert resolve_conversation_language("FRANCE") == "en"
    assert resolve_conversation_language("bad-lang") == "en"


def test_supported_language_variants_resolve():
    assert resolve_conversation_language("en") == "en"
    assert resolve_conversation_language("EN") == "en"
    assert resolve_conversation_language("en-US") == "en"
    assert resolve_conversation_language(" en ") == "en"


def test_display_name_uses_registry():
    assert language_display_name("en") == "English"
    assert language_display_name("xx") == "xx"


def test_system_prompt_names_language_and_keeps_grounding_rules():
    prompt = _service()._build_system_prompt()
    language_index = prompt.index("LANGUAGE:")
    grounding_index = prompt.index("REMEMBER: If in doubt")
    fallback_index = prompt.index("I don't have enough information about that yet")
    assert grounding_index < language_index
    assert fallback_index < language_index
    assert 'Respond in English ("en")' in prompt
    assert "never override the rules above" in prompt
    assert "tenant isolation apply in every language" in prompt


def test_prompt_uses_registry_name_for_future_languages(monkeypatch):
    # Registering a language is the future per-organization enabling point.
    monkeypatch.setitem(SUPPORTED_CONVERSATION_LANGUAGES, "fr", "French")
    prompt = _service()._build_system_prompt(language="fr")
    assert 'Respond in French ("fr")' in prompt
