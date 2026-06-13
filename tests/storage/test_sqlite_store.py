from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.storage._factories import mapped_tweet
from tweet_extractor.domain.models import TweetLink
from tweet_extractor.mappers.twscrape_mapper import MappedTweet
from tweet_extractor.storage.sqlite_store import SqliteStore


@pytest.fixture
async def store(tmp_path: Path):
    async with SqliteStore(tmp_path / "tweets.db") as s:
        yield s


async def listar(store: SqliteStore, account: str = "someuser") -> list[MappedTweet]:
    return [m async for m in store.iter_account(account)]


# --- save / dedupe ---


async def test_save_inserta_y_devuelve_cuantos(store: SqliteStore):
    assert await store.save([mapped_tweet("1"), mapped_tweet("2")]) == 2


async def test_save_dedupea_por_pk(store: SqliteStore):
    assert await store.save([mapped_tweet("1")]) == 1
    # Re-visto en otra sub-ventana solapada: se ignora, no falla ni duplica.
    assert await store.save([mapped_tweet("1"), mapped_tweet("2")]) == 1
    assert [m.tweet.id for m in await listar(store)] == ["1", "2"]


async def test_save_vacio_es_noop(store: SqliteStore):
    assert await store.save([]) == 0


# --- roundtrip ---


async def test_roundtrip_completo(store: SqliteStore):
    original = mapped_tweet(
        "10",
        created_at=datetime(2023, 5, 1, 12, 30, 45, tzinfo=UTC),
        content='línea 1\nlínea 2, con "comillas"',
        links=[
            TweetLink(url="https://t.co/a", expanded_url="https://a.com", display_url="a.com"),
            TweetLink(url="https://t.co/b", expanded_url="https://b.com"),
        ],
        quoted_id="99",
        is_reply=True,
        conversation_id="5",
    )
    await store.save([original])
    (leido,) = await listar(store)
    assert leido == original
    assert leido.tweet.created_at.tzinfo is not None


# --- iter_account ---


async def test_iter_account_filtra_por_cuenta(store: SqliteStore):
    await store.save([mapped_tweet("1"), mapped_tweet("2", account="otra")])
    assert [m.tweet.id for m in await listar(store)] == ["1"]
    assert [m.tweet.id for m in await listar(store, "otra")] == ["2"]


async def test_iter_account_orden_cronologico(store: SqliteStore):
    base = datetime(2023, 1, 1, tzinfo=UTC)
    await store.save(
        [
            mapped_tweet("3", created_at=base + timedelta(days=2)),
            mapped_tweet("1", created_at=base),
            mapped_tweet("2", created_at=base + timedelta(days=1)),
        ]
    )
    assert [m.tweet.id for m in await listar(store)] == ["1", "2", "3"]


async def test_account_ids(store: SqliteStore):
    await store.save([mapped_tweet("1"), mapped_tweet("2"), mapped_tweet("9", account="otra")])
    assert await store.account_ids("someuser") == {"1", "2"}
    assert await store.account_ids("nadie") == set()


async def test_accounts_distintas_ordenadas(store: SqliteStore):
    await store.save([mapped_tweet("1", account="zeta"), mapped_tweet("2", account="alfa")])
    assert await store.accounts() == ["alfa", "zeta"]


# --- checkpoints de sub-ventanas ---

SINCE = datetime(2023, 1, 1, tzinfo=UTC)
UNTIL = datetime(2023, 1, 8, tzinfo=UTC)


async def test_checkpoint_marca_y_consulta(store: SqliteStore):
    assert not await store.is_window_done("someuser", SINCE, UNTIL)
    await store.mark_window_done("someuser", SINCE, UNTIL)
    assert await store.is_window_done("someuser", SINCE, UNTIL)
    # Otra ventana / otra cuenta: independientes.
    assert not await store.is_window_done("someuser", UNTIL, UNTIL + timedelta(days=7))
    assert not await store.is_window_done("otra", SINCE, UNTIL)


async def test_checkpoint_idempotente(store: SqliteStore):
    await store.mark_window_done("someuser", SINCE, UNTIL)
    await store.mark_window_done("someuser", SINCE, UNTIL)  # no falla
    assert await store.is_window_done("someuser", SINCE, UNTIL)


async def test_checkpoint_normaliza_offset_a_utc(store: SqliteStore):
    # El mismo instante expresado en -03:00 debe matchear el checkpoint UTC.
    await store.mark_window_done("someuser", SINCE, UNTIL)
    tz_ar = timezone(timedelta(hours=-3))
    assert await store.is_window_done("someuser", SINCE.astimezone(tz_ar), UNTIL.astimezone(tz_ar))


async def test_checkpoint_rechaza_naive(store: SqliteStore):
    with pytest.raises(ValueError):
        await store.mark_window_done("someuser", datetime(2023, 1, 1), UNTIL)


# --- ciclo de vida ---


async def test_context_manager_crea_schema_y_persiste(tmp_path: Path):
    db = tmp_path / "tweets.db"
    async with SqliteStore(db) as store:
        await store.save([mapped_tweet("1")])
    # Reabrir: el dato sobrevive al cierre (persistente, no in-memory).
    async with SqliteStore(db) as store:
        assert [m.tweet.id for m in await listar(store)] == ["1"]
