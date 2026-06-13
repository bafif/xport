from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tweet_extractor import cli
from tweet_extractor.orchestrator import AccountResult
from tweet_extractor.providers.base import ProviderError

runner = CliRunner()

ARGS_BASE = ["--since", "2023-01-01", "--until", "2023-01-08"]


def salida(result: Any) -> str:
    """stdout + stderr, tolerando ambas variantes de captura del CliRunner
    (click viejo mezcla; click nuevo separa en `.stderr`)."""
    try:
        err = result.stderr
    except ValueError:
        err = ""
    return str(result.output) + str(err)


def test_fecha_invalida_es_error_de_uso():
    result = runner.invoke(
        cli.app, ["--account", "u", "--since", "2023-13-01", "--until", "2024-01-01"]
    )
    assert result.exit_code != 0
    assert "YYYY-MM-DD" in salida(result)


def test_since_no_anterior_a_until_es_error():
    result = runner.invoke(
        cli.app, ["--account", "u", "--since", "2024-01-01", "--until", "2023-01-01"]
    )
    assert result.exit_code != 0
    assert "anterior" in salida(result)


def test_account_vacio_es_error():
    result = runner.invoke(cli.app, ["--account", "@", *ARGS_BASE])
    assert result.exit_code != 0
    assert "vacío" in salida(result)


def test_happy_path_parsea_y_resume(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    capturado: dict[str, Any] = {}

    async def fake_run(
        accounts: list[str], since: datetime, until: datetime, *args: Any
    ) -> tuple[list[AccountResult], int, int]:
        capturado.update(accounts=accounts, since=since, until=until)
        return (
            [
                AccountResult(
                    account="someuser",
                    csv_path=tmp_path / "someuser_2023-01-01_2023-01-08.csv",
                    exported=2,
                    saved=2,
                    windows_run=1,
                    windows_skipped=0,
                )
            ],
            123,
            899_877,
        )

    monkeypatch.setattr(cli, "_run", fake_run)
    result = runner.invoke(cli.app, ["--account", "@someuser", *ARGS_BASE])
    assert result.exit_code == 0
    assert capturado["accounts"] == ["someuser"]  # el @ se tolera y se limpia
    assert capturado["since"] == datetime(2023, 1, 1, tzinfo=UTC)
    assert capturado["until"] == datetime(2023, 1, 8, tzinfo=UTC)
    assert "someuser: 2 tweets" in result.output
    assert "123" in result.output  # uso del gate en el resumen


def test_error_de_dominio_sale_con_codigo_1(monkeypatch: pytest.MonkeyPatch):
    async def fake_run(*args: Any) -> Any:
        raise ProviderError("Faltan cookies X_AUTH_TOKEN/X_CT0 en el entorno (.env).")

    monkeypatch.setattr(cli, "_run", fake_run)
    result = runner.invoke(cli.app, ["--account", "u", *ARGS_BASE])
    assert result.exit_code == 1
    assert "Faltan cookies" in salida(result)
