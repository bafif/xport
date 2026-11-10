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
        cli.app, ["extract", "--account", "u", "--since", "2023-13-01", "--until", "2024-01-01"]
    )
    assert result.exit_code != 0
    assert "YYYY-MM-DD" in salida(result)


def test_since_no_anterior_a_until_es_error():
    result = runner.invoke(
        cli.app, ["extract", "--account", "u", "--since", "2024-01-01", "--until", "2023-01-01"]
    )
    assert result.exit_code != 0
    assert "anterior" in salida(result)


def test_account_vacio_es_error():
    result = runner.invoke(cli.app, ["extract", "--account", "@", *ARGS_BASE])
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
    result = runner.invoke(cli.app, ["extract", "--account", "@someuser", *ARGS_BASE])
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
    result = runner.invoke(cli.app, ["extract", "--account", "u", *ARGS_BASE])
    assert result.exit_code == 1
    assert "Faltan cookies" in salida(result)


def test_desde_otra_carpeta_usa_el_mismo_registro(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Regla 1 de docs/PLAN.md: antes, la CLI corrida desde otra carpeta creaba otro
    # `data/audit/ledger.db` y el tope dejaba de ser uno solo.
    from tests.providers._fixtures import tweet_result
    from tests.service.conftest import fake_backend_builder

    monkeypatch.setenv("XPORT_DATA_DIR", str(tmp_path / "xport-data"))
    monkeypatch.setattr(
        cli, "build_backend", fake_backend_builder([tweet_result("1"), tweet_result("2")])
    )
    outputs = []
    for folder in ("una", "otra"):
        (tmp_path / folder).mkdir()
        monkeypatch.chdir(tmp_path / folder)
        result = runner.invoke(cli.app, ["extract", "--account", "someuser", *ARGS_BASE])
        assert result.exit_code == 0, salida(result)
        outputs.append(result.output)
    assert "Gate: 2 accesos" in outputs[0]
    # La segunda corrida ve el registro y el checkpoint de la primera: no vuelve a pedir
    # el tramo y el tope sigue en los mismos 2 accesos, no en 0.
    assert "1 salteados por checkpoint" in outputs[1]
    assert "Gate: 2 accesos" in outputs[1]
    assert (tmp_path / "xport-data" / "audit" / "ledger.db").is_file()
    assert not (tmp_path / "una" / "data").exists()


def test_capture_llena_la_cola(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from tweet_extractor.storage.queue import CaptureQueue

    monkeypatch.setenv("XPORT_DATA_DIR", str(tmp_path / "xport-data"))
    result = runner.invoke(cli.app, ["capture", "--account", "Uno,@dos", "--excel", *ARGS_BASE])
    assert result.exit_code == 0, salida(result)
    assert "@uno 2023-01-01 → 2023-01-08: queued" in result.output

    async def queued() -> list[tuple[str, str, str]]:
        async with CaptureQueue(tmp_path / "xport-data" / "queue.db") as q:
            return [(c.account, c.encoding, c.delimiter) for c in await q.recent()]

    assert asyncio.run(queued()) == [("dos", "utf-8-sig", ";"), ("uno", "utf-8-sig", ";")]


def test_capture_rechaza_lo_que_no_es_un_usuario_de_x() -> None:
    result = runner.invoke(cli.app, ["capture", "--account", "nasa since:2020", *ARGS_BASE])
    assert result.exit_code != 0
    assert "no es un nombre de usuario de X" in salida(result)
