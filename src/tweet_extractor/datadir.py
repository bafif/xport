"""La mudanza de los datos viejos a la carpeta fija por usuario (regla 1 de docs/PLAN.md).

Hasta noviembre de 2026 todo vivía en `data/`, relativo a la carpeta desde donde se
arrancaba: la CLI corrida desde otra carpeta creaba otro registro del tope. Ahora vive en
`Settings.xport_data_dir`, y la primera vez que arranca el servicio o la CLI se MUEVE ahí lo
que haya en un `data/` viejo. Se mueve y no se copia: una copia dejaría dos registros.

Un `data/` cuenta como de xport solo si tiene `audit/ledger.db`, así no se toca una carpeta
`data/` ajena. Si en la carpeta nueva ya hay un registro, el viejo no se pisa ni se deja:
sus accesos se suman al nuevo (contar de más es seguro; perderlos no) y se borra. Con los
tweets y los CSV no se mezcla nada: si ya existen en la carpeta nueva, el viejo queda
donde estaba y se avisa.
"""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from tweet_extractor.compliance.gate import LEDGER_SCHEMA
from tweet_extractor.config import Settings

# Lo que se muda, con el campo de Settings que dice adónde. El registro va ÚLTIMO: es lo que
# marca un `data/` como de xport, así que si algo falla antes, la próxima vez se reintenta.
_ITEMS: tuple[tuple[str, str], ...] = (
    ("tweets.db", "data_db_path"),
    ("accounts.db", "accounts_db_path"),
    ("csv", "csv_dir"),
    ("captures", "captures_dir"),
    ("audit/ledger.db", "audit_db_path"),
)
_LEDGER = "audit/ledger.db"
# Archivos que SQLite deja al lado de la base (journal; WAL si alguna vez se usa).
_SIDECARS = ("-journal", "-wal", "-shm")


def migrate_legacy_data(settings: Settings) -> list[str]:
    """Muda los `data/` viejos de `settings.xport_legacy_dirs` a la carpeta de datos.
    Devuelve una línea por cosa hecha o dejada, para el log. Es síncrona (archivos y
    sqlite3): desde código async, por `asyncio.to_thread`."""
    notes: list[str] = []
    for src in settings.xport_legacy_dirs:
        if not (src / _LEDGER).is_file():
            continue
        for rel, field in _ITEMS:
            old = src / rel
            new: Path = getattr(settings, field)
            if not old.exists() or _same(old, new):
                continue
            if not new.exists():
                new.parent.mkdir(parents=True, exist_ok=True)
                _move(old, new)
                notes.append(f"moví {old} a {new}")
            elif rel == _LEDGER:
                n = _merge_ledger(old, new)
                _remove_db(old)
                notes.append(f"sumé {n} accesos del registro viejo {old} a {new} y lo borré")
            elif old.is_dir():
                left = _merge_dir(old, new)
                notes.append(
                    f"moví {old} a {new}"
                    + (f"; quedaron {left} archivos que ya estaban en {new}" if left else "")
                )
            else:
                notes.append(f"dejé {old}: ya hay {new} y no mezclo las dos bases")
        # Sin registro, el `data/` viejo ya no se vuelve a mirar; si quedó vacío, se va.
        _remove_empty_dirs(src)
    return notes


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _move(old: Path, new: Path) -> None:
    shutil.move(old, new)
    if old.suffix == ".db":
        for suffix in _SIDECARS:
            side = old.with_name(old.name + suffix)
            if side.exists():
                shutil.move(side, new.with_name(new.name + suffix))


def _remove_db(path: Path) -> None:
    path.unlink()
    for suffix in _SIDECARS:
        path.with_name(path.name + suffix).unlink(missing_ok=True)


def _merge_ledger(old: Path, new: Path) -> int:
    """Suma al registro `new` todos los accesos de `old`. No deduplica, como el gate:
    los dos registros contaron accesos distintos. Devuelve cuántos accesos sumó."""
    with closing(sqlite3.connect(new)) as db:
        db.executescript(LEDGER_SCHEMA)
        db.execute("ATTACH DATABASE ? AS old", (str(old),))
        try:
            has_table = db.execute(
                "SELECT 1 FROM old.sqlite_master WHERE type = 'table' AND name = 'access_ledger'"
            ).fetchone()
            if not has_table:
                return 0
            (total,) = db.execute(
                "SELECT COALESCE(SUM(count), 0) FROM old.access_ledger"
            ).fetchone()
            with db:  # una transacción: o entran todos los accesos o ninguno
                db.execute(
                    "INSERT INTO main.access_ledger(ts, count)"
                    " SELECT ts, count FROM old.access_ledger"
                )
            return int(total)
        finally:
            db.execute("DETACH DATABASE old")


def _merge_dir(old: Path, new: Path) -> int:
    """Mueve a `new` lo de `old` que `new` no tenga. Devuelve cuántos quedaron en `old`."""
    left = 0
    for child in old.iterdir():
        target = new / child.name
        if target.exists():
            left += 1
        else:
            shutil.move(child, target)
    return left


def _remove_empty_dirs(src: Path) -> None:
    for d in (src / "audit", src):
        try:
            d.rmdir()  # solo si está vacía
        except OSError:
            pass
