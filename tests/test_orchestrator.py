from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.providers._fixtures import tweet_result
from tweet_extractor.mappers.twscrape_mapper import map_tweet
from tweet_extractor.orchestrator import run_job
from tweet_extractor.providers.base import (
    Page,
    ProviderError,
    RawTweet,
    SearchQuery,
    TweetProvider,
)
from tweet_extractor.storage.sqlite_store import SqliteStore

SINCE = datetime(2023, 1, 1, tzinfo=UTC)
UNTIL = datetime(2023, 1, 15, tzinfo=UTC)  # 14 días -> 2 tramos de 7
W1 = (SINCE, datetime(2023, 1, 8, tzinfo=UTC))
W2 = (datetime(2023, 1, 8, tzinfo=UTC), UNTIL)

JAN11 = "Wed Jan 11 00:00:00 +0000 2023"

WindowKey = tuple[str, str, str]


def clave(username: str, window: tuple[datetime, datetime]) -> WindowKey:
    return (username, f"{window[0]:%Y-%m-%d}", f"{window[1]:%Y-%m-%d}")


class FakeProvider(TweetProvider):
    """Páginas enlatadas por (cuenta, tramo); cursor = índice de página. Registra
    las llamadas para poder afirmar qué tramos se fetchearon (y cuáles no)."""

    max_accessed_per_page = 60

    def __init__(self, pages: dict[WindowKey, list[list[RawTweet]]]) -> None:
        self._pages = pages
        self.calls: list[WindowKey] = []

    async def fetch_page(self, query: SearchQuery, cursor: str | None) -> Page:
        key = (query.username, f"{query.since:%Y-%m-%d}", f"{query.until:%Y-%m-%d}")
        self.calls.append(key)
        pages = self._pages.get(key, [[]])
        idx = 0 if cursor is None else int(cursor)
        tweets = pages[idx]
        next_cursor = str(idx + 1) if idx + 1 < len(pages) else None
        return Page(tweets=tweets, accessed_count=len(tweets), next_cursor=next_cursor)


class ExplodingProvider(FakeProvider):
    def __init__(self, pages: dict[WindowKey, list[list[RawTweet]]], fail_on: WindowKey) -> None:
        super().__init__(pages)
        self._fail_on = fail_on

    async def fetch_page(self, query: SearchQuery, cursor: str | None) -> Page:
        key = (query.username, f"{query.since:%Y-%m-%d}", f"{query.until:%Y-%m-%d}")
        if key == self._fail_on:
            raise ProviderError("rate limit agotado")
        return await super().fetch_page(query, cursor)


@pytest.fixture
async def store(tmp_path: Path):
    async with SqliteStore(tmp_path / "tweets.db") as s:
        yield s


def contenidos(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as f:
        return [row[2] for row in list(csv.reader(f))[1:]]  # columna content, sin header


async def correr(provider: TweetProvider, store: SqliteStore, out: Path, accounts=("someuser",)):
    return await run_job(
        list(accounts),
        SINCE,
        UNTIL,
        provider=provider,
        mapper=map_tweet,
        store=store,
        out_dir=out,
    )


async def test_happy_path_dos_tramos(store: SqliteStore, tmp_path: Path):
    provider = FakeProvider(
        {
            clave("someuser", W1): [
                [tweet_result("1"), tweet_result("2", retweeted=tweet_result("rt"))]
            ],
            clave("someuser", W2): [[tweet_result("3", created_at=JAN11)]],
        }
    )
    (result,) = await correr(provider, store, tmp_path / "csv")
    assert result.saved == 2  # el RT no se persiste
    assert result.windows_run == 2
    assert result.windows_skipped == 0
    assert result.exported == 2
    assert contenidos(result.csv_path) == ["texto 1", "texto 3"]  # orden cronológico
    assert await store.is_window_done("someuser", *W1)
    assert await store.is_window_done("someuser", *W2)


async def test_paginacion_multipagina_dentro_de_un_tramo(store: SqliteStore, tmp_path: Path):
    provider = FakeProvider({clave("someuser", W1): [[tweet_result("1")], [tweet_result("2")]]})
    (result,) = await correr(provider, store, tmp_path / "csv")
    assert result.saved == 2


async def test_rerun_saltea_tramos_checkpointeados(store: SqliteStore, tmp_path: Path):
    pages = {clave("someuser", W1): [[tweet_result("1")]]}
    await correr(FakeProvider(pages), store, tmp_path / "csv")

    segunda_vuelta = FakeProvider(pages)
    (result,) = await correr(segunda_vuelta, store, tmp_path / "csv")
    assert segunda_vuelta.calls == []  # cero fetches: cero presupuesto del gate
    assert result.windows_skipped == 2
    assert result.saved == 0
    assert result.exported == 1  # el CSV se regenera desde el store igual


async def test_dedupe_entre_tramos(store: SqliteStore, tmp_path: Path):
    # El mismo tweet aparece en ambos tramos (p.ej. por rarezas del search):
    # INSERT OR IGNORE lo absorbe y el CSV lo trae una sola vez.
    provider = FakeProvider(
        {
            clave("someuser", W1): [[tweet_result("1")]],
            clave("someuser", W2): [[tweet_result("1"), tweet_result("3", created_at=JAN11)]],
        }
    )
    (result,) = await correr(provider, store, tmp_path / "csv")
    assert result.saved == 2
    assert contenidos(result.csv_path) == ["texto 1", "texto 3"]


async def test_politica_de_replies_se_aplica_al_exportar(store: SqliteStore, tmp_path: Path):
    provider = FakeProvider(
        {
            clave("someuser", W1): [
                [
                    tweet_result("1"),  # raíz propia
                    tweet_result("2", in_reply_to="1", conversation_id="1"),  # self-thread
                    tweet_result("3", in_reply_to="900", conversation_id="900"),  # conv ajena
                ]
            ]
        }
    )
    (result,) = await correr(provider, store, tmp_path / "csv")
    assert result.saved == 3  # se persisten TODOS (la política es del export)
    assert result.exported == 2
    assert contenidos(result.csv_path) == ["texto 1", "texto 2"]


async def test_fallo_a_mitad_no_marca_checkpoint_ni_exporta(store: SqliteStore, tmp_path: Path):
    provider = ExplodingProvider(
        {clave("someuser", W1): [[tweet_result("1")]]},
        fail_on=clave("someuser", W2),
    )
    with pytest.raises(ProviderError):
        await correr(provider, store, tmp_path / "csv")
    assert await store.is_window_done("someuser", *W1)  # lo cerrado queda cerrado
    assert not await store.is_window_done("someuser", *W2)  # el roto se re-corre entero
    assert not (tmp_path / "csv" / "someuser.csv").exists()
    # Lo guardado del tramo cerrado sobrevive para el re-run:
    assert await store.account_ids("someuser") == {"1"}


async def test_multiples_cuentas_un_csv_cada_una(store: SqliteStore, tmp_path: Path):
    provider = FakeProvider(
        {
            clave("ana", W1): [[tweet_result("1")]],
            clave("beto", W2): [[tweet_result("2", created_at=JAN11)]],
        }
    )
    resultados = await correr(provider, store, tmp_path / "csv", accounts=("ana", "beto"))
    assert [r.account for r in resultados] == ["ana", "beto"]
    assert (tmp_path / "csv" / "ana.csv").exists()
    assert (tmp_path / "csv" / "beto.csv").exists()
    assert contenidos(tmp_path / "csv" / "ana.csv") == ["texto 1"]
    assert contenidos(tmp_path / "csv" / "beto.csv") == ["texto 2"]


async def test_log_recibe_progreso(store: SqliteStore, tmp_path: Path):
    provider = FakeProvider({clave("someuser", W1): [[tweet_result("1")]]})
    mensajes: list[str] = []
    await run_job(
        ["someuser"],
        SINCE,
        UNTIL,
        provider=provider,
        mapper=map_tweet,
        store=store,
        out_dir=tmp_path / "csv",
        log=mensajes.append,
    )
    assert any("someuser" in m and "tweets nuevos" in m for m in mensajes)
