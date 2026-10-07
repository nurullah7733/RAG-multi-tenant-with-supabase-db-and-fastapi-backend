"""Phase 2.5: whitespace/paragraph cleaning plus chunking strategy."""

from itertools import pairwise

import pytest

from app.services.chunking import TextChunk, chunk_pages, clean_text
from app.services.extraction import ExtractedPage


def page(text: str, number: int | None = None) -> ExtractedPage:
    return ExtractedPage(number, text)


def chunk(text: str, size=80, overlap=15, min_chars=20) -> list[str]:
    return [
        piece.content
        for piece in chunk_pages([page(text)], size, overlap, min_chars)
    ]


def test_clean_text_collapses_whitespace_and_removes_invisible_marks():
    assert (
        clean_text("  সেবা\t \u00a0 list \u200b\u200f \n\n\n\n বাংলা \n")
        == "সেবা list\n\nবাংলা"
    )


def test_clean_text_keeps_meaningful_unicode_intact():
    text = "মাসিক ফি: ৪৯ ডলার — সকাল ৯টা। ピーク時。¿Cómo? – ok"
    assert clean_text(text) == text


def test_short_text_is_one_chunk_with_page_metadata():
    assert chunk_pages([page("Small text.", 3)], 80, 15, 20) == [
        TextChunk(0, 3, "Small text.")
    ]


def test_long_text_splits_within_size_at_natural_boundaries():
    text = "The Professional plan costs $49 per month. " * 20
    pieces = chunk(text, size=100, overlap=15, min_chars=40)
    assert len(pieces) >= 2
    for piece in pieces:
        # A tiny tail may merge into the previous chunk: target + min_chars.
        assert len(piece) <= 100 + 40
        assert all(word in text for word in piece.split())


def test_chunks_overlap_and_cover_the_whole_document():
    sentences = [
        f"Sentence {index} states a distinct fact number {index * 7} words more."
        for index in range(1, 15)
    ]
    text = " ".join(sentences)
    pieces = chunk(text, size=100, overlap=30, min_chars=40)
    assert len(pieces) >= 2
    for previous, following in pairwise(pieces):
        # continuity: every following chunk begins inside the previous tail
        assert following[:20].strip() in previous[-90:]
        assert following != previous


def test_bengali_long_text_splits_without_corrupting_words():
    sentence = "সংস্থার মাসিক ফি ঊনপঞ্চাশ ডলার এবং সেটি বছরে পর্যালোচনা করা হয়। "
    text = sentence * 12
    pieces = chunk(text, size=120, overlap=30, min_chars=60)
    assert len(pieces) >= 2
    for piece in pieces:
        words = piece.split()
        assert words
        for word in words:
            assert word in text
        joined = " ".join(words)
        assert joined in text  # whitespace normalization keeps word order


def test_chunks_never_span_pages_and_indexes_stay_ordered():
    pages = [
        page("First page carries a long enough paragraph to span windows. " * 4, 1),
        page("Second page has its own long paragraph spanning windows too. " * 4, 2),
    ]
    chunks = chunk_pages(pages, 100, 20, 30)
    assert chunks
    assert all(c.content for c in chunks)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert "First page" in chunks[0].content
    assert all("First page" not in c.content for c in chunks if c.page_number == 2)
    assert any(
        c.page_number == 2 and "Second page" in c.content for c in chunks
    )


def test_tiny_tail_is_absorbed_into_the_previous_chunk():
    text = "Repeated boundary sentence for the test. " * 6 + "tiny"
    pieces = chunk(text, size=100, overlap=20, min_chars=200)
    assert len(pieces) == 1
    assert pieces[-1].endswith("tiny")


def test_unspaced_hard_split_terminates_and_stays_contiguous():
    text = "一二三四五六七八九十" * 200
    pieces = chunk(text, size=50, overlap=10, min_chars=20)
    assert len(pieces) >= 2
    for piece in pieces:
        assert piece in text
        assert piece.strip()


def test_empty_pages_are_skipped():
    assert chunk_pages([page("   "), page("kept")], 100, 10, 5) == [
        TextChunk(0, None, "kept")
    ]


def test_global_chunk_index_increases_across_pages():
    chunks = chunk_pages(
        [page("one two three four five", 3), page("six seven eight", 4)],
        10,
        0,
        5,
    )
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert [c.page_number for c in chunks][:1] == [3]


def test_chunking_settings_bounds_and_relationships_are_validated():
    from pydantic import ValidationError

    from app.core.config import Settings

    valid = Settings(_env_file=None, openai_api_key="k")
    assert valid.document_chunk_size == 1000
    assert valid.document_chunk_overlap == 150
    assert valid.document_min_chunk_chars == 200
    for options in [
        {"document_chunk_size": 150},  # below minimum
        {"document_chunk_size": 5000},  # above maximum
        {"document_chunk_overlap": 600, "document_chunk_size": 1000},  # > half
        {"document_min_chunk_chars": 1000},  # not below the size
    ]:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, openai_api_key="k", **options)


def test_embedding_settings_dimensions_match_pgvector_column(monkeypatch):
    from pydantic import ValidationError

    from app.core.config import Settings

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    valid = Settings(_env_file=None, openai_api_key="k")
    assert valid.embedding_provider == "openai"
    assert valid.embedding_dimensions == 1536
    for options in [
        {},  # missing key
        {"openai_api_key": "k", "embedding_dimensions": 768},  # mismatched column
        {"openai_api_key": "k", "embedding_model": ""},  # empty model
    ]:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **options)
