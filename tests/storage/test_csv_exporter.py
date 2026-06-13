from __future__ import annotations

import csv
from collections.abc import AsyncIterator, Iterable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.storage._factories import mapped_tweet
from tweet_extractor.domain.models import Tweet, TweetLink
from tweet_extractor.storage.csv_exporter import CSV_COLUMNS, export_account, write_csv
from tweet_extractor.storage.sqlite_store import SqliteStore


async def aiter(tweets: Iterable[Tweet]) -> AsyncIterator[Tweet]:
    for tweet in tweets:
        yield tweet


def un_tweet(**kwargs: object) -> Tweet:
    return mapped_tweet(**kwargs).tweet  # type: ignore[arg-type]


def leer(path: Path, *, delimiter: str = ",", encoding: str = "utf-8") -> list[list[str]]:
    with path.open(newline="", encoding=encoding) as f:
        return list(csv.reader(f, delimiter=delimiter))


def tmps_residuales(directorio: Path) -> list[Path]:
    return list(directorio.glob("*.tmp"))


# --- write_csv ---


async def test_header_y_fila_basica(tmp_path: Path):
    path = tmp_path / "out.csv"
    written = await write_csv(aiter([un_tweet(tweet_id="1")]), path)
    assert written == 1
    rows = leer(path)
    assert rows[0] == list(CSV_COLUMNS)
    assert rows[1] == ["someuser", "2023-01-04T00:00:00+00:00", "texto 1", "", "", ""]


async def test_quote_all_en_el_crudo(tmp_path: Path):
    path = tmp_path / "out.csv"
    await write_csv(aiter([un_tweet(tweet_id="1")]), path)
    primera_linea = path.read_text(encoding="utf-8").splitlines()[0]
    # QUOTE_ALL: TODAS las celdas van entre comillas, header incluido.
    esperado = ",".join(f'"{col}"' for col in CSV_COLUMNS)
    assert primera_linea == esperado


async def test_content_multilinea_queda_en_una_fila_logica(tmp_path: Path):
    path = tmp_path / "out.csv"
    contenido = 'línea 1\nlínea 2, con "comillas" y ;'
    await write_csv(aiter([un_tweet(tweet_id="1", content=contenido)]), path)
    rows = leer(path)
    assert len(rows) == 2  # header + UNA fila lógica (el salto vive dentro de la celda)
    assert rows[1][2] == contenido


async def test_links_multiples_separados_por_espacio(tmp_path: Path):
    links = [
        TweetLink(url="https://t.co/a", expanded_url="https://a.com/x"),
        TweetLink(url="https://t.co/b", expanded_url="https://b.com/y"),
    ]
    path = tmp_path / "out.csv"
    await write_csv(aiter([un_tweet(tweet_id="1", links=links)]), path)
    assert leer(path)[1][3] == "https://a.com/x https://b.com/y"


async def test_quote_presente_llena_columnas(tmp_path: Path):
    path = tmp_path / "out.csv"
    await write_csv(aiter([un_tweet(tweet_id="1", quoted_id="99")]), path)
    assert leer(path)[1][4:] == ["99", "https://x.com/i/web/status/99"]


async def test_delimitador_configurable(tmp_path: Path):
    path = tmp_path / "out.csv"
    await write_csv(aiter([un_tweet(tweet_id="1")]), path, delimiter=";")
    rows = leer(path, delimiter=";")
    assert rows[0] == list(CSV_COLUMNS)


async def test_encoding_configurable_bom(tmp_path: Path):
    # El candidato para Excel-AR (ODQ abierta): utf-8-sig antepone el BOM.
    path = tmp_path / "out.csv"
    await write_csv(aiter([un_tweet(tweet_id="1")]), path, encoding="utf-8-sig")
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")


async def test_escritura_atomica_sin_tmp_residual(tmp_path: Path):
    path = tmp_path / "out.csv"
    await write_csv(aiter([un_tweet(tweet_id="1")]), path)
    assert path.exists()
    assert tmps_residuales(tmp_path) == []


async def test_fallo_a_mitad_limpia_tmp_y_preserva_el_csv_previo(tmp_path: Path):
    path = tmp_path / "out.csv"
    await write_csv(aiter([un_tweet(tweet_id="1")]), path)

    async def explota() -> AsyncIterator[Tweet]:
        yield un_tweet(tweet_id="2")
        raise RuntimeError("se cortó a mitad de export")

    with pytest.raises(RuntimeError):
        await write_csv(explota(), path)
    assert tmps_residuales(tmp_path) == []
    assert leer(path)[1][0:3] == ["someuser", "2023-01-04T00:00:00+00:00", "texto 1"]


async def test_created_at_iso_utc(tmp_path: Path):
    path = tmp_path / "out.csv"
    tweet = un_tweet(tweet_id="1", created_at=datetime(2024, 7, 1, 23, 59, 59, tzinfo=UTC))
    await write_csv(aiter([tweet]), path)
    assert leer(path)[1][1] == "2024-07-01T23:59:59+00:00"


# --- export_account (store -> política de replies -> CSV) ---


async def test_export_account_aplica_politica_y_un_archivo_por_cuenta(tmp_path: Path):
    async with SqliteStore(tmp_path / "tweets.db") as store:
        await store.save(
            [
                mapped_tweet("1"),  # raíz propia
                mapped_tweet("2", is_reply=True, conversation_id="1"),  # self-thread: queda
                mapped_tweet("3", is_reply=True, conversation_id="900"),  # conv ajena: fuera
                mapped_tweet("9", account="otra"),  # otra cuenta: no va en este CSV
            ]
        )
        path, written = await export_account(store, "someuser", tmp_path / "csv")
    assert path == tmp_path / "csv" / "someuser.csv"
    assert written == 2
    rows = leer(path)
    assert [r[2] for r in rows[1:]] == ["texto 1", "texto 2"]
    assert all(r[0] == "someuser" for r in rows[1:])


async def test_export_account_sin_tweets_deja_solo_header(tmp_path: Path):
    async with SqliteStore(tmp_path / "tweets.db") as store:
        path, written = await export_account(store, "nadie", tmp_path / "csv")
    assert written == 0
    assert leer(path) == [list(CSV_COLUMNS)]


async def test_export_account_rechaza_handle_con_separadores(tmp_path: Path):
    async with SqliteStore(tmp_path / "tweets.db") as store:
        with pytest.raises(ValueError):
            await export_account(store, "../fuera", tmp_path / "csv")
