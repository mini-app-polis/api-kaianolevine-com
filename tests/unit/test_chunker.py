from __future__ import annotations

import tiktoken

from kaianolevine_api.retrieval.wcs.chunker import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    CHUNKING_VERSION,
    ENCODING_NAME,
    chunk_transcript,
)


def test_chunking_version_is_set() -> None:
    assert isinstance(CHUNKING_VERSION, int)
    assert CHUNKING_VERSION >= 1


def test_empty_text_returns_empty() -> None:
    assert chunk_transcript("") == []


def test_short_text_returns_one_chunk() -> None:
    text = "Short transcript content."
    chunks = chunk_transcript(text)
    assert len(chunks) == 1
    c = chunks[0]
    assert c.chunk_index == 0
    assert c.start_offset == 0
    assert c.end_offset == len(text)
    assert c.text == text


def test_chunk_offsets_match_text_slices() -> None:
    sentence = "The quick brown fox jumps over the lazy dog. "
    text = sentence * 200
    chunks = chunk_transcript(text)
    assert len(chunks) > 1
    assert chunks[0].start_offset == 0
    assert chunks[-1].end_offset == len(text)
    for c in chunks:
        assert text[c.start_offset : c.end_offset] == c.text


def test_chunk_indices_are_sequential_from_zero() -> None:
    text = "word " * 5000
    chunks = chunk_transcript(text)
    assert len(chunks) > 1
    for i, c in enumerate(chunks):
        assert c.chunk_index == i


def test_chunk_size_caps_at_configured_value() -> None:
    text = "alpha beta gamma delta epsilon zeta " * 200
    chunks = chunk_transcript(text)
    enc = tiktoken.get_encoding(ENCODING_NAME)
    for c in chunks:
        assert len(enc.encode(c.text)) <= CHUNK_SIZE


def test_overlap_between_adjacent_chunks() -> None:
    text = "alpha beta gamma delta epsilon zeta " * 200
    chunks = chunk_transcript(text)
    assert len(chunks) >= 2
    enc = tiktoken.get_encoding(ENCODING_NAME)
    step = CHUNK_SIZE - CHUNK_OVERLAP
    # Adjacent chunks should be CHUNK_SIZE tokens (except possibly the last)
    # and the start of chunk N+1 should equal the start of chunk N plus step.
    full_tokens = enc.encode(text)
    for i, c in enumerate(chunks[:-1]):
        # Every non-final chunk should be exactly CHUNK_SIZE tokens.
        assert len(enc.encode(c.text)) == CHUNK_SIZE
        # The first token of the next chunk should align with token (i*step + step)
        # in the full token stream.
        next_chunk_first_token = enc.encode(chunks[i + 1].text)[0]
        assert next_chunk_first_token == full_tokens[(i + 1) * step]


def test_no_redundant_tail_chunk() -> None:
    """Last chunk should reach the end of the text exactly once."""
    text = "alpha beta gamma " * 500
    chunks = chunk_transcript(text)
    assert chunks[-1].end_offset == len(text)
    # Penultimate chunk should NOT also reach the end.
    if len(chunks) > 1:
        assert chunks[-2].end_offset < len(text)
