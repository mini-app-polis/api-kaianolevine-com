"""OpenAI embedding client for the WCS Q&A retrieval pipeline.

Thin async wrapper around openai.AsyncOpenAI. Batching keeps calls under
OpenAI's per-request input limits and amortizes round-trip cost when the
convergence flow processes many notes/chunks at once.
"""

from __future__ import annotations

from openai import AsyncOpenAI

EMBEDDING_DIMENSIONS = 1536
DEFAULT_MODEL = "text-embedding-3-small"
BATCH_SIZE = 50


class OpenAIEmbedder:
    """Async embedder for the OpenAI text-embedding-3-* family."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self.model = model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text, in input order.

        Texts are sent to the OpenAI Embeddings API in batches of
        ``BATCH_SIZE`` to stay under per-request input limits and
        amortize round-trip cost. Returns an empty list when called with
        no inputs (no API call is made).
        """
        if not texts:
            return []
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i : i + BATCH_SIZE]
            resp = await self._client.embeddings.create(
                model=self.model,
                input=batch,
            )
            out.extend(d.embedding for d in resp.data)
        return out
