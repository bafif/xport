from __future__ import annotations

import asyncio
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated

import typer

from tweet_extractor.compliance.gate import ComplianceError, SlidingWindowGate
from tweet_extractor.compliance.gated_provider import GatedProvider
from tweet_extractor.config import Settings
from tweet_extractor.datadir import migrate_legacy_data
from tweet_extractor.i18n import Lang, argv_lang, number, parse_lang, set_lang, system_lang, t
from tweet_extractor.mappers.base import MapperError
from tweet_extractor.orchestrator import AccountResult, run_job
from tweet_extractor.providers.base import ProviderError
from tweet_extractor.providers.factory import build_backend
from tweet_extractor.storage.csv_exporter import DEFAULT_DELIMITER, DEFAULT_ENCODING
from tweet_extractor.storage.queue import Capture, CaptureQueue
from tweet_extractor.storage.sqlite_store import SqliteStore

# La ayuda de Typer se arma al importar: `--lang` se lee antes, así `--help` sale en ese idioma.
set_lang(parse_lang(argv_lang(sys.argv[1:])) or system_lang())

app = typer.Typer(help=t("cli.help"), add_completion=False)

# `--lang` va en la CLI y en cada comando, así anda antes o después del comando. La ayuda ya
# salió en ese idioma (`argv_lang`); acá se fija para los mensajes.
LangOption = Annotated[Lang | None, typer.Option("--lang", help=t("cli.opt_lang"))]


def _use(lang: Lang | None) -> None:
    if lang is not None:
        set_lang(lang)


@app.callback()
def main(lang: LangOption = None) -> None:
    _use(lang)


def _parse_day(value: str, param: str) -> datetime:
    """`YYYY-MM-DD` -> medianoche UTC tz-aware (la granularidad de `build_query`
    es por día, así que pedir solo la fecha es honesto con lo que se filtra)."""
    try:
        day = date.fromisoformat(value)
    except ValueError as e:
        raise typer.BadParameter(t("cli.bad_date", param=param, value=repr(value))) from e
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _accounts(values: list[str]) -> list[str]:
    """Las cuentas de `--account`, repetible y con varias separadas por comas. Minúsculas
    como en la API (`_clean_handle`): X no distingue mayúsculas en los handles y el store
    compara exacto. Sin repetidos, preservando el orden."""
    raw = [part for value in values for part in value.split(",")]
    accounts = list(dict.fromkeys(a.strip().lstrip("@").strip().lower() for a in raw))
    if any(not a for a in accounts):
        raise typer.BadParameter(t("cli.empty_account"))
    return accounts


@app.command(help=t("cli.extract"))
def extract(
    account: Annotated[
        list[str],
        typer.Option("--account", "-a", help=t("cli.opt_account")),
    ],
    since: Annotated[str, typer.Option(help=t("cli.opt_since"))],
    until: Annotated[str, typer.Option(help=t("cli.opt_until"))],
    out_dir: Annotated[
        Path | None,
        typer.Option(help=t("cli.opt_out_dir")),
    ] = None,
    subwindow_days: Annotated[
        int | None,
        typer.Option(min=1, help=t("cli.opt_subwindow")),
    ] = None,
    encoding: Annotated[str, typer.Option(help=t("cli.opt_encoding"))] = DEFAULT_ENCODING,
    delimiter: Annotated[str, typer.Option(help=t("cli.opt_delimiter"))] = DEFAULT_DELIMITER,
    lang: LangOption = None,
) -> None:
    """Extrae del lado del servidor (twscrape o la API oficial). Todo acceso pasa por el
    Compliance Gate (tope deslizante de 24 h); un re-run saltea los tramos ya completados."""
    _use(lang)
    since_dt = _parse_day(since, "--since")
    until_dt = _parse_day(until, "--until")
    if since_dt >= until_dt:
        raise typer.BadParameter(t("cli.since_after_until"))
    accounts = _accounts(account)

    try:
        results, usage, remaining = asyncio.run(
            _run(accounts, since_dt, until_dt, out_dir, subwindow_days, encoding, delimiter)
        )
    except (ProviderError, ComplianceError, MapperError) as e:
        typer.secho(t("cli.error", error=e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e

    typer.echo("")
    for r in results:
        typer.echo(
            t(
                "cli.result",
                account=r.account,
                exported=r.exported,
                path=r.csv_path,
                saved=r.saved,
                run=r.windows_run,
                skipped=r.windows_skipped,
            )
        )
    typer.echo(t("cli.gate", usage=number(usage), remaining=number(remaining)))


@app.command(help=t("cli.capture"))
def capture(
    account: Annotated[
        list[str],
        typer.Option("--account", "-a", help=t("cli.opt_account")),
    ],
    since: Annotated[str, typer.Option(help=t("cli.opt_since"))],
    until: Annotated[str, typer.Option(help=t("cli.opt_until"))],
    excel: Annotated[bool, typer.Option("--excel", help=t("cli.opt_excel"))] = False,
    lang: LangOption = None,
) -> None:
    """Encola capturas para la extensión, una por cuenta: las toma de a una mientras el
    navegador con xport esté abierto, y al terminar cada una el servicio exporta su CSV."""
    _use(lang)
    since_day = _parse_day(since, "--since").date()
    until_day = _parse_day(until, "--until").date()
    if since_day >= until_day:
        raise typer.BadParameter(t("cli.since_after_until"))
    accounts = _accounts(account)
    bad = [a for a in accounts if not re.fullmatch(r"[a-z0-9_]{1,15}", a)]
    if bad:
        raise typer.BadParameter(t("cli.not_x_handle", handle=bad[0]))
    encoding, delimiter = ("utf-8-sig", ";") if excel else (DEFAULT_ENCODING, DEFAULT_DELIMITER)
    caps = asyncio.run(_enqueue(accounts, since_day, until_day, encoding, delimiter))
    for c in caps:
        typer.echo(f"#{c.id} @{c.account} {c.since} → {c.until}: {c.state.value}")
    typer.echo(t("cli.queued"))


async def _enqueue(
    accounts: list[str], since: date, until: date, encoding: str, delimiter: str
) -> list[Capture]:
    settings = Settings()
    for note in await asyncio.to_thread(migrate_legacy_data, settings):
        typer.echo(t("cli.legacy_data", note=note))
    async with CaptureQueue(settings.queue_db_path) as queue:
        return await queue.add(accounts, since, until, encoding=encoding, delimiter=delimiter)


async def _run(
    accounts: list[str],
    since: datetime,
    until: datetime,
    out_dir: Path | None,
    subwindow_days: int | None,
    encoding: str,
    delimiter: str,
) -> tuple[list[AccountResult], int, int]:
    """Wiring real: la factory elige el backend (scraping ↔ API oficial) según
    `PROVIDER_BACKEND`, gate persistente, store de datos, y el provider SIEMPRE
    envuelto en `GatedProvider` (regla #1: ningún fetch saltea el gate)."""
    settings = Settings()
    # La misma carpeta de datos que el servicio, se corra desde donde se corra (regla 1 de
    # docs/PLAN.md: un solo registro del tope); la primera vez muda el `data/` viejo.
    for note in await asyncio.to_thread(migrate_legacy_data, settings):
        typer.echo(t("cli.legacy_data", note=note))
    backend = await build_backend(settings)
    async with (
        SlidingWindowGate(settings.audit_db_path, settings.hard_cap, settings.window_s) as gate,
        SqliteStore(settings.data_db_path) as store,
    ):
        gated = GatedProvider(backend.provider, gate)
        results = await run_job(
            accounts,
            since,
            until,
            provider=gated,
            mapper=backend.mapper,
            store=store,
            out_dir=out_dir or settings.csv_dir,
            subwindow_days=subwindow_days or settings.subwindow_days,
            encoding=encoding,
            delimiter=delimiter,
            log=typer.echo,
        )
        usage = await gate.usage()
        remaining = await gate.remaining()
    return results, usage, remaining


if __name__ == "__main__":
    app()
