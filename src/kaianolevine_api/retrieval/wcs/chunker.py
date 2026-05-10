"""Token-based chunking of WCS transcripts for embedding.

400-token windows with 50-token overlap, anchored on tiktoken cl100k_base.
Character offsets are stored on each chunk so get_transcript_window can
assemble adjacent chunks back into a contiguous reading window without
re-tokenizing. Bumping CHUNKING_VERSION triggers a full re-chunk and
re-embed in the convergence flow.

The chunk text stored here does NOT include a transcript-title prefix. The
embedding-input composition (title + chunk_text) is the convergence flow's
concern.
"""

from __future__ import annotations

from dataclasses import dataclass

import tiktoken

CHUNKING_VERSION = 1
CHUNK_SIZE = 400
CHUNK_OVERLAP = 50
ENCODING_NAME = "cl100k_base"


@dataclass(frozen=True)
class Chunk:
    chunk_index: int
    start_offset: int
    end_offset: int
    text: str


def chunk_transcript(text: str) -> list[Chunk]:
    """Token-window a transcript into overlapping chunks."""
    if not text:
        return []
    enc = tiktoken.get_encoding(ENCODING_NAME)
    tokens = enc.encode(text)
    n = len(tokens)
    if n == 0:
        return []

    step = CHUNK_SIZE - CHUNK_OVERLAP
    chunks: list[Chunk] = []
    i = 0
    start = 0
    while True:
        end = min(start + CHUNK_SIZE, n)
        prefix_text = enc.decode(tokens[:start]) if start > 0 else ""
        chunk_text = enc.decode(tokens[start:end])
        start_offset = len(prefix_text)
        end_offset = start_offset + len(chunk_text)
        chunks.append(
            Chunk(
                chunk_index=i,
                start_offset=start_offset,
                end_offset=end_offset,
                text=chunk_text,
            )
        )
        if end >= n:
            break
        start += step
        i += 1
    return chunks
