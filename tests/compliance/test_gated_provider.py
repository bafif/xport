from __future__ import annotations

import pytest

from tweet_extractor.compliance.gate import SlidingWindowGate
from tweet_extractor.compliance.gated_provider import GatedProvider
from tweet_extractor.providers.base import Page, SearchQuery, TweetProvider


async def test_cuenta_accessed_count_no_len_tweets(tmp_path, make_provider, sample_query):
    gate = SlidingWindowGate(tmp_path / "ledger.db", hard_cap=1000)
    await gate.setup()
    pages = [
        Page(tweets=[{"id": "1"}], accessed_count=5, next_cursor="p1"),
        Page(tweets=[{"id": "2"}, {"id": "3"}], accessed_count=7, next_cursor=None),
    ]
    gated = GatedProvider(make_provider(pages, max_accessed_per_page=40), gate)

    out = [t async for t in gated.fetch_tweets(sample_query)]

    assert [t["id"] for t in out] == ["1", "2", "3"]
    assert await gate.usage() == 12  # 5+7 accedidos, NO 3 emitidos


async def test_reserva_la_cota_superior_antes_de_pedir_y_reconcilia_despues(tmp_path, sample_query):
    gate = SlidingWindowGate(tmp_path / "ledger.db", hard_cap=1000)
    await gate.setup()
    observed: dict[str, int] = {}

    class SpyProvider(TweetProvider):
        max_accessed_per_page = 40

        async def fetch_page(self, query: SearchQuery, cursor: str | None) -> Page:
            observed["usage_durante_fetch"] = await gate.usage()
            return Page(tweets=[{"id": "1"}], accessed_count=3, next_cursor=None)

    gated = GatedProvider(SpyProvider(), gate)

    _ = [t async for t in gated.fetch_tweets(sample_query)]

    assert observed["usage_durante_fetch"] == 40  # cota superior reservada ANTES
    assert await gate.usage() == 3  # reconciliado al real DESPUÉS


async def test_el_fetch_espera_si_el_gate_esta_lleno(
    tmp_path, fake_clock, make_provider, sample_query
):
    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)
        fake_clock.advance(s)

    gate = SlidingWindowGate(
        tmp_path / "ledger.db",
        hard_cap=50,
        window_s=86_400,
        clock=fake_clock.time,
        sleep=fake_sleep,
    )
    await gate.setup()
    await gate.reserve(40)  # quedan 10; la próxima reserva (cota 40) no entra

    pages = [Page(tweets=[{"id": "1"}], accessed_count=5, next_cursor=None)]
    gated = GatedProvider(make_provider(pages, max_accessed_per_page=40), gate)

    out = [t async for t in gated.fetch_tweets(sample_query)]

    assert out == [{"id": "1"}]
    assert slept == [86_400.0]
    assert await gate.usage() == 5


def test_gated_provider_rechaza_inner_sin_max_accessed_per_page(tmp_path):
    gate = SlidingWindowGate(tmp_path / "ledger.db", hard_cap=1000)

    class ProviderSinCota(TweetProvider):
        async def fetch_page(self, query: SearchQuery, cursor: str | None) -> Page:
            return Page(tweets=[], accessed_count=0, next_cursor=None)

    with pytest.raises(TypeError, match=r"max_accessed_per_page"):
        GatedProvider(ProviderSinCota(), gate)


async def test_excepcion_durante_fetch_mantiene_cota_superior_en_ledger(tmp_path, sample_query):
    gate = SlidingWindowGate(tmp_path / "ledger.db", hard_cap=1000)
    await gate.setup()

    class FailingProvider(TweetProvider):
        max_accessed_per_page = 40

        async def fetch_page(self, query: SearchQuery, cursor: str | None) -> Page:
            raise RuntimeError("network error")

    gated = GatedProvider(FailingProvider(), gate)

    with pytest.raises(RuntimeError):
        async for _ in gated.fetch_tweets(sample_query):
            pass

    # Sobre-conteo intencional: la cota superior (40) queda en el ledger, NO 0.
    # Fail-closed correcto para un tope de cumplimiento.
    assert await gate.usage() == 40
