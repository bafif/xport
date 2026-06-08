# Modelo de dominio (`Tweet`, `TweetLink`) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir el modelo de dominio `Tweet` (con su `TweetLink`): la única fuente de verdad del shape de un tweet, validado e inmutable, al que mapearán ambos backends.

**Architecture:** Dos value objects pydantic v2 (`frozen=True`) en `src/tweet_extractor/domain/models.py`. `TweetLink` normaliza un link embebido; `Tweet` es el tweet normalizado con alcance minimalista alineado al CSV + `id`. El modelo valida invariantes de *forma* (tz-aware en UTC, no-vacío, coherencia del quote) — **no** parsing de GraphQL, que vive solo en `mappers/`. `is_quote` es una propiedad computada (deriva de `quoted_tweet_id`), no un campo.

**Tech Stack:** Python 3.12, `uv`, pydantic v2, `pytest` (`asyncio_mode = auto`), `ruff`, `mypy --strict` (solo sobre `src`).

**Spec:** `docs/superpowers/specs/2026-06-08-domain-model-design.md`

---

## File Structure

| Archivo | Responsabilidad |
|---|---|
| `src/tweet_extractor/domain/__init__.py` | Marca de paquete (vacío) |
| `src/tweet_extractor/domain/models.py` | `TweetLink`, `Tweet` (value objects pydantic) |
| `tests/domain/__init__.py` | Marca de paquete de tests (vacío) |
| `tests/domain/test_models.py` | Tests del modelo (síncronos, modelo puro) |

**Notas para el ejecutor:**
- `mypy` está configurado con `files = ["src"]`: **solo el código fuente** se type-checkea. Los tests deben quedar **ruff-clean** (reglas `E, F, I, UP, B, ASYNC`) pero no necesitan pasar mypy.
- `links: list[TweetLink] = []` es seguro en pydantic v2 (no comparte el default mutable entre instancias; lo deep-copia por instancia). Ninguna regla de ruff seleccionada lo marca (B006 es para *argumentos* de función, no atributos de clase).
- Nombres de tests en español **sin tildes** (identificadores); docstrings y comentarios **con** acentuación correcta.
- `from __future__ import annotations` arriba de cada archivo (consistente con el resto del repo).

**Fuera de alcance** (próximas fases, NO crear ahora): `providers/twscrape_provider.py`, `providers/official_api.py`, `providers/factory.py`, `mappers/`, `storage/`, `service/`, `cli.py`, extensión.

---

## Task 1: `TweetLink`

**Files:**
- Create: `src/tweet_extractor/domain/__init__.py` (vacío)
- Create: `src/tweet_extractor/domain/models.py`
- Create: `tests/domain/__init__.py` (vacío)
- Create: `tests/domain/test_models.py`

- [ ] **Step 1: Crear los paquetes vacíos**

```bash
mkdir -p src/tweet_extractor/domain tests/domain
touch src/tweet_extractor/domain/__init__.py tests/domain/__init__.py
```

- [ ] **Step 2: Escribir los tests de `TweetLink` (fallan)**

`tests/domain/test_models.py`:
```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from tweet_extractor.domain.models import TweetLink


def test_tweetlink_valido_con_y_sin_display_url():
    sin = TweetLink(url="https://t.co/abc", expanded_url="https://ejemplo.com/doc")
    assert sin.display_url is None

    con = TweetLink(
        url="https://t.co/abc",
        expanded_url="https://ejemplo.com/doc",
        display_url="ejemplo.com/doc",
    )
    assert con.display_url == "ejemplo.com/doc"


def test_tweetlink_rechaza_url_vacia():
    with pytest.raises(ValidationError):
        TweetLink(url="", expanded_url="https://ejemplo.com")


def test_tweetlink_rechaza_expanded_url_vacia():
    with pytest.raises(ValidationError):
        TweetLink(url="https://t.co/abc", expanded_url="   ")


def test_tweetlink_expanded_igual_a_url_es_valido():
    # Un link que no se pudo expandir es válido a nivel modelo (lo filtra el mapper).
    link = TweetLink(url="https://t.co/abc", expanded_url="https://t.co/abc")
    assert link.expanded_url == link.url
```

- [ ] **Step 3: Run tests → verify FAIL**

Run: `uv run pytest tests/domain/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tweet_extractor.domain.models'`.

- [ ] **Step 4: Implementar `src/tweet_extractor/domain/models.py` (solo `TweetLink`)**

```python
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator


class TweetLink(BaseModel):
    """Un link embebido normalizado (de `legacy.entities.urls[]`).

    El filtrado de `expanded_url` vacío/autorreferencial (apunta de vuelta a un
    status de x.com) es responsabilidad del mapper; el modelo solo rechaza el
    string vacío como invariante mínima de forma.
    """

    model_config = ConfigDict(frozen=True)

    url: str  # el t.co
    expanded_url: str  # destino real (el que se exporta)
    display_url: str | None = None  # versión legible, opcional

    @field_validator("url", "expanded_url")
    @classmethod
    def _no_vacio(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("url y expanded_url no pueden estar vacíos")
        return v
```

- [ ] **Step 5: Run tests → verify PASS**

Run: `uv run pytest tests/domain/test_models.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add src/tweet_extractor/domain/ tests/domain/
git commit -m "feat(domain): TweetLink (link embebido normalizado, validado)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `Tweet`

**Files:**
- Modify: `src/tweet_extractor/domain/models.py` (agregar `Tweet` + imports)
- Modify: `tests/domain/test_models.py` (agregar tests de `Tweet`)

- [ ] **Step 1: Agregar los tests de `Tweet` (fallan)**

Reemplazar el bloque de imports al tope de `tests/domain/test_models.py` por:
```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from tweet_extractor.domain.models import Tweet, TweetLink
```

Agregar al final de `tests/domain/test_models.py`:
```python
def test_tweet_construccion_completa():
    t = Tweet(
        id="123",
        account="someuser",
        created_at=datetime(2023, 6, 1, 12, 0, tzinfo=UTC),
        content="hola",
        links=[TweetLink(url="https://t.co/x", expanded_url="https://e.com")],
        quoted_tweet_id="999",
        quoted_tweet_url="https://x.com/i/web/status/999",
    )
    assert t.id == "123"
    assert t.is_quote is True
    assert len(t.links) == 1


def test_tweet_construccion_minima_defaults():
    t = Tweet(
        id="123",
        account="someuser",
        created_at=datetime(2023, 6, 1, 12, 0, tzinfo=UTC),
        content="hola",
    )
    assert t.links == []
    assert t.quoted_tweet_id is None
    assert t.quoted_tweet_url is None
    assert t.is_quote is False


def test_tweet_rechaza_created_at_naive():
    with pytest.raises(ValidationError):
        Tweet(
            id="123",
            account="someuser",
            created_at=datetime(2023, 6, 1, 12, 0),  # naive
            content="hola",
        )


def test_tweet_normaliza_created_at_a_utc():
    # -03:00 (Buenos Aires) → mismo instante, pero tzinfo == UTC en almacenamiento.
    ba = timezone(timedelta(hours=-3))
    t = Tweet(
        id="123",
        account="someuser",
        created_at=datetime(2023, 6, 1, 9, 0, tzinfo=ba),
        content="hola",
    )
    assert t.created_at.tzinfo == UTC
    assert t.created_at == datetime(2023, 6, 1, 12, 0, tzinfo=UTC)


def test_tweet_rechaza_id_vacio():
    with pytest.raises(ValidationError):
        Tweet(
            id="  ",
            account="someuser",
            created_at=datetime(2023, 6, 1, 12, 0, tzinfo=UTC),
            content="hola",
        )


def test_tweet_rechaza_account_vacio():
    with pytest.raises(ValidationError):
        Tweet(
            id="123",
            account="",
            created_at=datetime(2023, 6, 1, 12, 0, tzinfo=UTC),
            content="hola",
        )


def test_tweet_rechaza_quote_incoherente_solo_id():
    with pytest.raises(ValidationError):
        Tweet(
            id="123",
            account="someuser",
            created_at=datetime(2023, 6, 1, 12, 0, tzinfo=UTC),
            content="hola",
            quoted_tweet_id="999",
        )


def test_tweet_rechaza_quote_incoherente_solo_url():
    with pytest.raises(ValidationError):
        Tweet(
            id="123",
            account="someuser",
            created_at=datetime(2023, 6, 1, 12, 0, tzinfo=UTC),
            content="hola",
            quoted_tweet_url="https://x.com/i/web/status/999",
        )


def test_tweet_acepta_quote_coherente():
    t = Tweet(
        id="123",
        account="someuser",
        created_at=datetime(2023, 6, 1, 12, 0, tzinfo=UTC),
        content="hola",
        quoted_tweet_id="999",
        quoted_tweet_url="https://x.com/i/web/status/999",
    )
    assert t.is_quote is True


def test_tweet_es_inmutable():
    t = Tweet(
        id="123",
        account="someuser",
        created_at=datetime(2023, 6, 1, 12, 0, tzinfo=UTC),
        content="hola",
    )
    with pytest.raises(ValidationError):
        t.content = "otro"  # frozen: reasignar atributo falla
```

- [ ] **Step 2: Run tests → verify FAIL**

Run: `uv run pytest tests/domain/test_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'Tweet'`.

- [ ] **Step 3: Implementar `Tweet` en `src/tweet_extractor/domain/models.py`**

Sobrescribir el archivo completo (agrega `Tweet`, sus validadores y los imports nuevos `UTC`, `datetime`, `model_validator`):
```python
from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class TweetLink(BaseModel):
    """Un link embebido normalizado (de `legacy.entities.urls[]`).

    El filtrado de `expanded_url` vacío/autorreferencial (apunta de vuelta a un
    status de x.com) es responsabilidad del mapper; el modelo solo rechaza el
    string vacío como invariante mínima de forma.
    """

    model_config = ConfigDict(frozen=True)

    url: str  # el t.co
    expanded_url: str  # destino real (el que se exporta)
    display_url: str | None = None  # versión legible, opcional

    @field_validator("url", "expanded_url")
    @classmethod
    def _no_vacio(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("url y expanded_url no pueden estar vacíos")
        return v


class Tweet(BaseModel):
    """El tweet normalizado: única fuente de verdad del shape (ver CLAUDE.md).

    Value object inmutable. Valida invariantes de *forma* — no parsing de
    GraphQL, que vive solo en `mappers/`: `created_at` tz-aware en UTC,
    `id`/`account` no vacíos, y coherencia del quote (id y url juntos o ninguno).
    Alcance minimalista (columnas del CSV + `id` como PK de storage); `is_quote`
    es propiedad computada; sin `is_retweet` (los RT se excluyen en el mapper) ni
    `media_urls` (ODQ 4 abierta).
    """

    model_config = ConfigDict(frozen=True)

    id: str  # PK; snowflake como string (no int: precisión)
    account: str  # handle sin @
    created_at: datetime  # tz-aware; normalizado a UTC
    content: str  # texto completo (note_tweet si aplica) — lo resuelve el mapper
    links: list[TweetLink] = []
    quoted_tweet_id: str | None = None
    quoted_tweet_url: str | None = None

    @property
    def is_quote(self) -> bool:
        """Deriva del id citado; nunca se desincroniza de un campo almacenado."""
        return self.quoted_tweet_id is not None

    @field_validator("id", "account")
    @classmethod
    def _no_vacio(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("id y account no pueden estar vacíos")
        return v

    @field_validator("created_at")
    @classmethod
    def _aware_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("created_at debe ser timezone-aware (UTC en almacenamiento)")
        return v.astimezone(UTC)

    @model_validator(mode="after")
    def _quoted_coherente(self) -> Tweet:
        if (self.quoted_tweet_id is None) != (self.quoted_tweet_url is None):
            raise ValueError("quoted_tweet_id y quoted_tweet_url deben venir juntos o ninguno")
        return self
```

- [ ] **Step 4: Run tests → verify PASS**

Run: `uv run pytest tests/domain/test_models.py -v`
Expected: PASS (14 tests: 4 de `TweetLink` + 10 de `Tweet`).

- [ ] **Step 5: Type-check estricto**

Run: `uv run mypy src`
Expected: `Success: no issues found`. (Si pydantic+mypy se quejan de algún validador, corregir de forma genuina — **no** usar `# type: ignore`.)

- [ ] **Step 6: Commit**

```bash
git add src/tweet_extractor/domain/models.py tests/domain/test_models.py
git commit -m "feat(domain): Tweet (shape minimalista, is_quote computada, validacion estricta)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Verificación final (suite + lint + types)

**Files:** ninguno (verificación)

- [ ] **Step 1: Correr toda la suite**

Run: `uv run pytest -q`
Expected: PASS — toda la suite verde (los tests existentes del Compliance Gate / providers / config / smoke **siguen** verdes + los 14 nuevos del dominio). Ningún test existente debe romperse.

- [ ] **Step 2: Lint y formato**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: sin errores. (Si `format --check` falla, correr `uv run ruff format .`, revisar el diff y re-commitear.)

- [ ] **Step 3: Type-check estricto del código fuente**

Run: `uv run mypy src`
Expected: `Success: no issues found`.

- [ ] **Step 4: Commit de cierre (solo si lint/format hizo cambios)**

```bash
git add -A -- ':!.planning'
git commit -m "chore(domain): verificacion final del modelo (tests, ruff, mypy)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>" || echo "nada que commitear"
```

---

## Notas para el ejecutor

- **No tocar el Compliance Gate** ni nada fuera de `domain/` y sus tests. Esta fase es puramente aditiva.
- **No introducir deps nuevas:** pydantic v2 ya está en el proyecto.
- **`.planning/`** queda sin trackear (los `git add` lo excluyen o son explícitos por path).
- Si `mypy` reporta un problema con los validadores de pydantic, resolverlo correctamente (firma del método, `Self`/anotación) — el repo tiene política de **cero `# type: ignore`** (ver historia del Compliance Gate).
