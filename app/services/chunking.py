"""Conservative text cleaning and page-aware chunking; no vector storage yet."""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from app.services.extraction import ExtractedPage

# Invisible formatting characters that carry no meaning in prose.
_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
_SPACES = re.compile(r"[^\S\n]+")
_BLANK_RUNS = re.compile(r"\n{3,}")
_SENTENCE_SET = "।۔。!?!"

_WHITESPACE = " \t\n\v\f\r"
_OVERLAP_LOOKBACK = 4


def clean_text(text: str) -> str:
    """Whitespace/formatting cleanup that keeps all meaningful Unicode."""
    text = _INVISIBLE.sub("", text)
    text = _SPACES.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _BLANK_RUNS.sub("\n\n", text)
    return text.strip()


def _is_extendable_mark(char: str) -> bool:
    """Combining marks (Bengali matras, Arabic diacritics...) stay attached."""
    return unicodedata.category(char) in {"Mn", "Mc", "Me"}


def _cut_point(text: str, start: int, hard_end: int) -> int:
    """Boundary at paragraph > sentence > word level; never mid-cluster."""
    window_end = min(hard_end, len(text))
    # Paragraph break first, then a sentence terminator, else any word space.
    newline = text.rfind("\n", start + 1, window_end)
    if newline != -1:
        return newline + 1
    for terminator in _SENTENCE_SET:
        pointer = window_end - 1
        while True:
            sentence = text.rfind(terminator, start + 1, pointer)
            if sentence == -1:
                break
            following = sentence + 1
            if following >= len(text) or text[following] in _WHITESPACE:
                return sentence + 1
            pointer = sentence
    space = text.rfind(" ", start + 1, window_end)
    if space != -1:
        return space
    return window_end  # CJK-style text without separators: hard split


def _extend_past_marks(text: str, end: int) -> int:
    while end < len(text) and _is_extendable_mark(text[end]):
        end += 1
    return end


def _next_start(text: str, end: int, overlap: int, previous_start: int) -> int:
    candidate = max(end - overlap, previous_start + 1, 0)
    # Snap forward to a word boundary inside the overlap window when one exists.
    for offset in range(candidate, min(end, candidate + _OVERLAP_LOOKBACK + overlap)):
        if text[offset] in _WHITESPACE:
            snapped = min(offset + 1, end)
            return snapped if snapped < end else candidate
    return candidate


def _split_page(
    text: str, size: int, overlap: int, min_chars: int
) -> list[str]:
    cleaned = clean_text(text)
    if not cleaned:
        return []
    previous_start = 0
    parts: list[str] = []
    start = 0
    while start < len(cleaned):
        end = min(start + size, len(cleaned))
        if end < len(cleaned):
            end = _extend_past_marks(
                cleaned, min(_cut_point(cleaned, start, end), len(cleaned))
            )
        parts.append(cleaned[start:end].strip())
        previous_start = start
        if end >= len(cleaned):
            break
        start = _next_start(cleaned, end, overlap, previous_start)
    return _merge_tiny(parts, min_chars)


def _merge_tiny(parts: list[str], min_chars: int) -> list[str]:
    """Absorb meaningless tails into the previous chunk of the same page."""
    merged: list[str] = []
    for part in parts:
        if merged and len(part) < min_chars and not part.endswith("\n\n"):
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return merged


@dataclass(frozen=True)
class TextChunk:
    chunk_index: int  # global across the document, 0-based
    page_number: int | None  # 1-based PDF page; None for DOCX/TXT
    content: str


def chunk_pages(
    pages: Iterable[ExtractedPage],
    size: int,
    overlap: int,
    min_chars: int,
) -> list[TextChunk]:
    """Chunking never spans pages so page metadata stays exact."""
    chunks: list[TextChunk] = []
    for page in pages:
        for part in _split_page(page.text, size, overlap, min_chars):
            chunks.append(TextChunk(len(chunks), page.page_number, part))
    return chunks
