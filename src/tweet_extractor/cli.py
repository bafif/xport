from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated

import typer

from tweet_extractor.compliance.gate import ComplianceError, SlidingWindowGate
from tweet_extractor.compliance.gated_provider import GatedProvider
from tweet_extractor.config import Settings
from tweet_extractor.mappers.base import MapperError
from tweet_extractor.orchestrator import AccountResult, run_job
from tweet_extractor.providers.base import ProviderError
from tweet_extractor.providers.factory import build_backend
from tweet_extractor.storage.csv_exporter import DEFAULT_DELIMITER, DEFAULT_ENCODING
from tweet_extractor.storage.sqlite_store import SqliteStore

app = typer.Typer(
    help="Extrae los tweets de una o varias cuentas de X entre dos fechas → un CSV por cuenta.",
    add_completion=False,
)


def _parse_day(value: str, param: str) -> datetime:
    """`YYYY-MM-DD` -> medianoche UTC tz-aware (la granularidad de `build_query`
    es por día, así que pedir solo la fecha es honesto con lo que se filtra)."""
    try:
        day = date.fromisoformat(value)
    except ValueError as e:
        raise typer.BadParameter(f"{param} debe ser YYYY-MM-DD: {value!r}") from e
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


@app.command()
def extract(
    account: Annotated[
        list[str],
        typer.Option("--account", "-a", help="Handle de la cuenta (sin @). Repetible."),
    ],
    since: Annotated[str, typer.Option(help="Fecha inicial YYYY-MM-DD (UTC), inclusiva.")],
    until: Annotated[str, typer.Option(help="Fecha final YYYY-MM-DD (UTC), exclusiva.")],
    out_dir: Annotated[Path, typer.Option(help="Directorio de salida de los CSV.")] = Path(
        "data/csv"
    ),
    subwindow_days: Annotated[
        int | None,
        typer.Option(min=1, help="Días por sub-ventana de búsqueda (default: Settings)."),
    ] = None,
    encoding: Annotated[
        str, typer.Option(help="Encoding del CSV (provisional; utf-8-sig agrega BOM p/ Excel).")
    ] = DEFAULT_ENCODING,
    delimiter: Annotated[
        str, typer.Option(help="Delimitador del CSV (provisional; ';' p/ Excel-AR).")
    ] = DEFAULT_DELIMITER,
) -> None:
    """Corre el job de extracción. Todo acceso pasa por el Compliance Gate
    (tope deslizante de 24 h); un re-run saltea los tramos ya completados."""
    since_dt = _parse_day(since, "--since")
    until_dt = _parse_day(until, "--until")
    if since_dt >= until_dt:
        raise typer.BadParameter("--since debe ser anterior a --until")
    accounts = [a.lstrip("@").strip() for a in account]
    if any(not a for a in accounts):
        raise typer.BadParameter("--account no puede ser vacío")

    try:
        results, usage, remaining = asyncio.run(
            _run(accounts, since_dt, until_dt, out_dir, subwindow_days, encoding, delimiter)
        )
    except (ProviderError, ComplianceError, MapperError) as e:
        typer.secho(f"error: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e

    typer.echo("")
    for r in results:
        typer.echo(
            f"{r.account}: {r.exported} tweets → {r.csv_path} "
            f"({r.saved} nuevos; {r.windows_run} tramos corridos, "
            f"{r.windows_skipped} salteados por checkpoint)"
        )
    typer.echo(f"Gate: {usage:,} accesos en la ventana de 24 h; quedan {remaining:,} del tope.")


async def _run(
    accounts: list[str],
    since: datetime,
    until: datetime,
    out_dir: Path,
    subwindow_days: int | None,
    encoding: str,
    delimiter: str,
) -> tuple[list[AccountResult], int, int]:
    """Wiring real: la factory elige el backend (scraping ↔ API oficial) según
    `PROVIDER_BACKEND`, gate persistente, store de datos, y el provider SIEMPRE
    envuelto en `GatedProvider` (regla #1: ningún fetch saltea el gate)."""
    settings = Settings()
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
            out_dir=out_dir,
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
