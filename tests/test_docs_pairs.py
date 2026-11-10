"""Cada documento del repo está en castellano y en inglés.

El original es `X.md` y la traducción va al lado, `X.en.md`; cada uno enlaza al otro arriba de
todo. También revisa que los enlaces relativos de los documentos lleguen a un archivo que exista,
así un archivo renombrado no deja a uno de los dos idiomas apuntando a la nada.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Lo que no es documentación propia: notas locales, licencias, changelogs y las plantillas de
# GitHub (esas son bilingües dentro del mismo archivo).
EXCLUDED_NAMES = {"CLAUDE.md"}
EXCLUDED_PREFIXES = ("LICENSE", "CHANGELOG")
EXCLUDED_DIRS = {".github", "node_modules", ".venv", "dist", ".wxt", ".output"}

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)[^)]*\)|<img[^>]*\ssrc=\"([^\"]+)\"")
FENCE = re.compile(r"```.*?```", re.S)


def _markdown_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.md"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        files = [ROOT / f for f in out]
    except (OSError, subprocess.CalledProcessError):
        files = list(ROOT.rglob("*.md"))
    return sorted(
        f
        for f in files
        if f.is_file()
        and f.name not in EXCLUDED_NAMES
        and not f.name.startswith(EXCLUDED_PREFIXES)
        and not EXCLUDED_DIRS.intersection(f.relative_to(ROOT).parts)
    )


def _pair(doc: Path) -> Path:
    """El mismo documento en el otro idioma."""
    if doc.name.endswith(".en.md"):
        return doc.with_name(doc.name.removesuffix(".en.md") + ".md")
    return doc.with_name(doc.name.removesuffix(".md") + ".en.md")


def _rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


DOCS = _markdown_files()


def test_there_are_documents() -> None:
    assert any(d.name == "README.md" for d in DOCS)


@pytest.mark.parametrize("doc", DOCS, ids=_rel)
def test_document_has_its_translation(doc: Path) -> None:
    pair = _pair(doc)
    assert pair.is_file(), f"{_rel(doc)} no tiene traducción: falta {_rel(pair)}"


@pytest.mark.parametrize("doc", DOCS, ids=_rel)
def test_document_links_to_the_other_language_at_the_top(doc: Path) -> None:
    head = "\n".join(doc.read_text(encoding="utf-8").splitlines()[:8])
    assert f"]({_pair(doc).name})" in head, (
        f"{_rel(doc)} necesita un enlace a {_pair(doc).name} debajo del título"
    )


@pytest.mark.parametrize("doc", DOCS, ids=_rel)
def test_relative_links_resolve(doc: Path) -> None:
    text = FENCE.sub("", doc.read_text(encoding="utf-8"))
    broken = []
    for match in LINK.finditer(text):
        target = next(g for g in match.groups() if g)
        if re.match(r"[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
            continue
        path = target.split("#", 1)[0]
        if path and not (doc.parent / path).exists():
            broken.append(target)
    assert not broken, f"{_rel(doc)} enlaza a archivos que no existen: {broken}"
