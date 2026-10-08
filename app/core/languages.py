"""Conversation language policy (Phase 3.6).

The default conversation language is English. This module is the single
place where conversation languages become available later per organization
through organization settings; enabling a language must never weaken the
grounding, fallback, or tenant-isolation rules defined elsewhere.
"""

DEFAULT_CONVERSATION_LANGUAGE = "en"

# Registry of conversation languages currently supported for assistant
# responses, "en" only for the initial release. Future language support is
# enabled per organization by extending this registry with organization
# settings (default_language, supported_languages).
SUPPORTED_CONVERSATION_LANGUAGES: dict[str, str] = {
    "en": "English",
}


def resolve_conversation_language(code: str | None) -> str:
    """Resolve a requested conversation language to a supported code.

    Missing, blank, or unsupported codes never raise; they fall back to the
    default language so language handling can never bypass grounding rules
    or the English fallback message.
    """
    normalized = (code or "").strip().lower()
    if not normalized:
        return DEFAULT_CONVERSATION_LANGUAGE
    if normalized in SUPPORTED_CONVERSATION_LANGUAGES:
        return normalized
    primary = normalized.split("-", 1)[0]
    if primary in SUPPORTED_CONVERSATION_LANGUAGES:
        return primary
    return DEFAULT_CONVERSATION_LANGUAGE


def language_display_name(code: str) -> str:
    """Human-readable name for a supported language code."""
    return SUPPORTED_CONVERSATION_LANGUAGES.get(code, code)
