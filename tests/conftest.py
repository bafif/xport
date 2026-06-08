from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio

from tweet_extractor.compliance.gate import SlidingWindowGate
from tweet_extractor.providers.base import Page, SearchQuery, TweetProvider


class FakeClock:
    """Reloj controlable para tests deterministas."""

    def __init__(self, start: int = 1_700_000_000) -> None:
        self.now = start

    def time(self) -> float:
        return float(self.now)

    def advance(self, seconds: float) -> None:
        self.now += int(seconds)


class FakeProvider(TweetProvider):
    """Provider en memoria para tests.

    El cursor opaco usa el formato 'p{idx}' donde idx es el índice de página
    (p1 → pages[1], etc.); cursor None devuelve pages[0].
    """

    def __init__(self, pages: list[Page], max_accessed_per_page: int = 40) -> None:
        self._pages = pages
        self.max_accessed_per_page = max_accessed_per_page
        self.cursors_seen: list[str | None] = []

    async def fetch_page(self, query: SearchQuery, cursor: str | None) -> Page:
        self.cursors_seen.append(cursor)
        idx = 0 if cursor is None else int(cursor[1:])
        return self._pages[idx]


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def make_provider():
    def _make(pages: list[Page], max_accessed_per_page: int = 40) -> FakeProvider:
        return FakeProvider(pages, max_accessed_per_page)

    return _make


@pytest_asyncio.fixture
async def make_gate():
    """Factory de SlidingWindowGate que cierra la conexión persistente de cada
    gate creado al terminar el test (dentro de su event loop). Determinista y sin
    ResourceWarnings de aiosqlite; reemplaza el cierre por GC."""
    gates: list[SlidingWindowGate] = []

    def _make(*args, **kwargs) -> SlidingWindowGate:
        gate = SlidingWindowGate(*args, **kwargs)
        gates.append(gate)
        return gate

    yield _make
    for gate in gates:
        await gate.close()


@pytest.fixture
def sample_query() -> SearchQuery:
    return SearchQuery(
        username="someuser",
        since=datetime(2023, 1, 1, tzinfo=UTC),
        until=datetime(2024, 1, 1, tzinfo=UTC),
    )
