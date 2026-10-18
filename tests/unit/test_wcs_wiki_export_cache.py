"""The export cache: one build per revision, and a bounded line behind it."""

from __future__ import annotations

import asyncio

import pytest

from kaianolevine_api.services.wcs_wiki_export import (
    ExportBusy,
    ExportCache,
    etag_matches,
)


class _Corpus:
    """A corpus whose ETag the test moves, and a build it can hold open."""

    def __init__(self) -> None:
        self.etag = '"rev-1"'
        self.builds = 0
        self.release = asyncio.Event()
        self.release.set()

    async def read_etag(self) -> str:
        return self.etag

    async def build(self) -> bytes:
        self.builds += 1
        built_for = self.etag
        await self.release.wait()
        return f"body {built_for}".encode()


async def test_the_same_revision_is_built_once() -> None:
    cache, corpus = ExportCache(), _Corpus()

    first = await cache.get(corpus.etag, corpus.read_etag, corpus.build)
    second = await cache.get(corpus.etag, corpus.read_etag, corpus.build)

    assert corpus.builds == 1
    assert first is second
    assert first.body == b'body "rev-1"'


async def test_a_new_revision_is_built_again() -> None:
    cache, corpus = ExportCache(), _Corpus()
    await cache.get(corpus.etag, corpus.read_etag, corpus.build)

    corpus.etag = '"rev-2"'
    built = await cache.get(corpus.etag, corpus.read_etag, corpus.build)

    assert corpus.builds == 2
    assert built.etag == '"rev-2"'


async def test_readers_arriving_during_a_build_share_it() -> None:
    cache, corpus = ExportCache(max_waiting=4), _Corpus()
    corpus.release.clear()

    readers = [
        asyncio.create_task(cache.get(corpus.etag, corpus.read_etag, corpus.build))
        for _ in range(4)
    ]
    await asyncio.sleep(0)
    corpus.release.set()
    results = await asyncio.gather(*readers)

    assert corpus.builds == 1
    assert len({id(r) for r in results}) == 1


async def test_a_reader_past_the_line_is_turned_away() -> None:
    cache, corpus = ExportCache(max_waiting=2), _Corpus()
    corpus.release.clear()

    in_line = [
        asyncio.create_task(cache.get(corpus.etag, corpus.read_etag, corpus.build))
        for _ in range(2)
    ]
    await asyncio.sleep(0)
    with pytest.raises(ExportBusy):
        await cache.get(corpus.etag, corpus.read_etag, corpus.build)

    corpus.release.set()
    await asyncio.gather(*in_line)
    assert corpus.builds == 1
    # Once the build is done the line is empty again.
    await cache.get(corpus.etag, corpus.read_etag, corpus.build)


async def test_a_reader_that_waited_takes_the_build_of_its_revision() -> None:
    """A write during the build does not send the waiters off to rebuild."""
    cache, corpus = ExportCache(), _Corpus()
    corpus.release.clear()

    first = asyncio.create_task(cache.get(corpus.etag, corpus.read_etag, corpus.build))
    second = asyncio.create_task(cache.get(corpus.etag, corpus.read_etag, corpus.build))
    await asyncio.sleep(0)
    corpus.etag = '"rev-2"'  # a write commits while rev-1 is being built
    corpus.release.set()

    assert (await first).etag == '"rev-1"'
    assert (await second).etag == '"rev-1"'
    assert corpus.builds == 1


async def test_a_reader_that_waited_builds_the_latest_revision() -> None:
    """Neither its own revision nor the current one: it builds the latest."""
    cache, corpus = ExportCache(), _Corpus()
    corpus.release.clear()

    first = asyncio.create_task(cache.get(corpus.etag, corpus.read_etag, corpus.build))
    await asyncio.sleep(0)
    corpus.etag = '"rev-2"'
    second = asyncio.create_task(cache.get(corpus.etag, corpus.read_etag, corpus.build))
    await asyncio.sleep(0)
    corpus.etag = '"rev-3"'
    corpus.release.set()

    assert (await first).etag == '"rev-1"'
    assert (await second).etag == '"rev-3"'
    assert corpus.builds == 2


async def test_a_failed_build_keeps_nothing_and_frees_the_line() -> None:
    cache, corpus = ExportCache(max_waiting=1), _Corpus()

    async def broken() -> bytes:
        raise RuntimeError("database went away")

    with pytest.raises(RuntimeError):
        await cache.get(corpus.etag, corpus.read_etag, broken)

    built = await cache.get(corpus.etag, corpus.read_etag, corpus.build)
    assert built.body == b'body "rev-1"'


@pytest.mark.parametrize(
    ("header", "matches"),
    [
        (None, False),
        ("", False),
        ('"rev-1"', True),
        ('W/"rev-1"', True),
        ('"rev-0", "rev-1"', True),
        ("*", True),
        ('"rev-2"', False),
        ("rev-1", False),
    ],
)
def test_if_none_match(header: str | None, matches: bool) -> None:
    assert etag_matches(header, '"rev-1"') is matches
