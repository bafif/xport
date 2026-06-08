# Spec — Modelo de dominio: `Tweet` y `TweetLink`

**Fecha:** 2026-06-08
**Alcance de la sesión:** `src/tweet_extractor/domain/models.py` (el `Tweet` normalizado y su `TweetLink`) con sus tests. **No** incluye providers concretos, mappers, storage/CSV, service ni CLI.
**Documentos base:** `CLAUDE.md` (reglas innegociables), `docs/plan-extractor-tweets.md` (plan completo) y `docs/superpowers/specs/2026-06-04-compliance-gate-core-design.md` (spec del núcleo crítico ya implementado). Este spec **complementa** el plan: documenta el modelo de dominio y las desviaciones justificadas respecto del boceto del plan.

---

## 1. Objetivo

Definir el modelo de dominio `Tweet`: la **única fuente de verdad del shape de un tweet** (CLAUDE.md), al que mapean AMBOS backends (scraping y, a futuro, la X API oficial). Es la capa 3 del diseño por capas (`domain/`), de la que dependen `mappers/` (arriba) y a la que `storage/` y el exportador CSV consumen.

Es una pieza pura, sin I/O ni dependencias nuevas (pydantic v2 ya está en el proyecto): un value object validado.

Resultado verificable de la sesión: `uv run pytest` verde (incluidos los nuevos tests del dominio), `uv run mypy src` limpio, `uv run ruff check .` limpio.

---

## 2. Decisiones de diseño y desviaciones respecto del boceto del plan (`docs/plan-extractor-tweets.md:90-106`)

El boceto del plan propone un `Tweet` con campos especulativos. Las decisiones de esta sesión lo ajustan a **YAGNI** y a las columnas reales del CSV (CLAUDE.md), sin debilitar nada.

### D1 — Alcance minimalista, alineado al CSV + clave de storage
Las columnas del CSV (CLAUDE.md) son: `account`, `created_at`, `content`, `links`, `quoted_tweet_id`, `quoted_tweet_url`. El modelo agrega solo `id` (PK para la dedupe/checkpointing de `storage/`, `INSERT OR IGNORE` por `id`).

**Se quitan respecto del boceto del plan:**
- **`is_retweet`** — los retweets se **excluyen** en el mapper (CLAUDE.md: filtrar por `retweeted_status_result`); nunca llegan a construirse como `Tweet`. El flag sería siempre `False`: estado muerto.
- **`media_urls`** — la media no es columna del CSV y su política sigue abierta (ODQ 4 del plan: "¿descargar media o solo URLs?"). No se modela hasta que un consumidor real la pida; agregarla después es una migración aditiva trivial.

**Se transforma:**
- **`is_quote`** deja de ser un campo almacenado y pasa a ser una **propiedad computada** (`quoted_tweet_id is not None`). No puede desincronizarse del dato del que deriva.

### D2 — Modelo estricto de invariantes mínimas (no permisivo)
El `Tweet` valida sus propias invariantes de dominio (ver §4). Esto **no es parsing de GraphQL** — eso sigue prohibido fuera de `mappers/` (CLAUDE.md) — sino la misma clase de garantía de *forma* que ya hace `SearchQuery` (rechaza datetimes naive en `providers/base.py`). El modelo valida forma; el mapper interpreta GraphQL. Beneficio: si un mapper con bugs arma un `Tweet` inconsistente, falla fuerte y temprano en vez de propagar basura al CSV.

### D3 — Value objects inmutables (`frozen=True`)
`Tweet` y `TweetLink` son `frozen=True` (pydantic v2), consistente con el `SearchQuery` frozen de `providers/base.py`. `frozen` impide reasignar atributos tras la construcción (la inmutabilidad que nos importa para un value object); no congela en sí la mutación in-place de la `list` interna, de la que no dependemos.

---

## 3. `TweetLink` (`domain/models.py`)

Un link embebido normalizado, extraído de `legacy.entities.urls[]` por el mapper.

```python
class TweetLink(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str            # el t.co
    expanded_url: str   # destino real (el que se exporta)
    display_url: str | None = None  # versión legible, opcional
```

- **Invariante mínima:** `url` y `expanded_url` no vacíos (`field_validator`). El filtrado de `expanded_url` vacío/autorreferencial (apunta de vuelta a un status de x.com) es responsabilidad del **mapper** (CLAUDE.md / `docs/plan-extractor-tweets.md:51`), no del modelo: el modelo solo rechaza el string vacío como invariante de forma.
- Un `expanded_url == url` (link que no se pudo expandir) es válido a nivel modelo.

---

## 4. `Tweet` (`domain/models.py`)

```python
class Tweet(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str                           # PK; snowflake como string (no int: precisión)
    account: str                      # handle sin @
    created_at: datetime              # tz-aware; normalizado a UTC en almacenamiento
    content: str                      # texto completo (note_tweet si aplica) — lo resuelve el mapper
    links: list[TweetLink] = []
    quoted_tweet_id: str | None = None
    quoted_tweet_url: str | None = None

    @property
    def is_quote(self) -> bool:
        """Deriva del id citado; nunca se desincroniza de un campo almacenado."""
        return self.quoted_tweet_id is not None
```

### Validadores (invariantes mínimas, enfoque estricto D2)

1. **`created_at` tz-aware → UTC.** Rechaza datetimes naive con `ValueError`; normaliza a UTC con `.astimezone(UTC)`. Garantiza la convención "UTC en almacenamiento" (CLAUDE.md) en la frontera del dominio.
   ```python
   @field_validator("created_at")
   @classmethod
   def _aware_utc(cls, v: datetime) -> datetime:
       if v.tzinfo is None:
           raise ValueError("created_at debe ser timezone-aware (UTC en almacenamiento)")
       return v.astimezone(UTC)
   ```

2. **`id` y `account` no vacíos.**
   ```python
   @field_validator("id", "account")
   @classmethod
   def _no_vacio(cls, v: str) -> str:
       if not v.strip():
           raise ValueError("id y account no pueden estar vacíos")
       return v
   ```

3. **Coherencia del quote: ambos o ninguno.** `quoted_tweet_id` y `quoted_tweet_url` deben venir juntos o los dos ausentes. (El mapper siempre puede construir la URL canónica `https://x.com/i/web/status/{id}` desde el id, así que un quote nunca tiene id sin url.)
   ```python
   @model_validator(mode="after")
   def _quoted_coherente(self) -> "Tweet":
       if (self.quoted_tweet_id is None) != (self.quoted_tweet_url is None):
           raise ValueError("quoted_tweet_id y quoted_tweet_url deben venir juntos o ninguno")
       return self
   ```

### Convenciones
- `account` es el handle **sin `@`** (consistente con `SearchQuery.username`). La normalización (quitar `@`) la hace el mapper; el modelo solo exige no-vacío.
- `content` guarda el texto completo, incluido el `note_tweet` (texto largo >280) cuando exista. La extracción del texto correcto (ODQ 12 del plan) es del mapper; el modelo es agnóstico.
- `id`/`quoted_tweet_id` son `str` (los snowflake IDs exceden la precisión segura de int en otros lenguajes/JSON; `str` es la convención robusta).

---

## 5. Manejo de errores

Las violaciones de invariantes se manifiestan como `pydantic.ValidationError` (los `field_validator`/`model_validator` lanzan `ValueError`, que pydantic envuelve). **Quién construye los `Tweet` es el mapper**, así que la decisión de *qué hacer* ante un tweet inválido (descartar la fila y seguir, o abortar el job) pertenece a la fase del mapper, no a esta. El modelo solo garantiza que un `Tweet` que existe es estructuralmente válido.

---

## 6. Estrategia de tests

`pytest` + `pytest-asyncio` (`asyncio_mode = auto`); estos tests son **síncronos** (modelo puro, sin I/O). Archivo nuevo `tests/domain/test_models.py` (+ `tests/domain/__init__.py` vacío).

**`Tweet`:**
1. Construcción válida con todos los campos.
2. Construcción mínima (solo requeridos): `links` default `[]`, quoted `None`.
3. `is_quote` → `True` con quote coherente; `False` sin quote.
4. `created_at` naive → `ValidationError`.
5. `created_at` tz no-UTC (p. ej. `-03:00`) → se normaliza a UTC (mismo instante, `tzinfo == UTC`).
6. `id` vacío → `ValidationError`; `account` vacío → `ValidationError`.
7. Quote incoherente: solo `quoted_tweet_id` → `ValidationError`; solo `quoted_tweet_url` → `ValidationError`.
8. Quote coherente (ambos) → válido.
9. Inmutabilidad: reasignar un atributo (`tweet.content = "x"`) → `ValidationError` (frozen).

**`TweetLink`:**
10. Construcción válida (con y sin `display_url`).
11. `url` vacío → `ValidationError`; `expanded_url` vacío → `ValidationError`.
12. `expanded_url == url` (no expandible) → válido.

---

## 7. Estructura creada en esta sesión

```
src/tweet_extractor/domain/__init__.py        # vacío (marca de paquete)
src/tweet_extractor/domain/models.py          # TweetLink, Tweet
tests/domain/__init__.py                       # vacío
tests/domain/test_models.py                    # tests del modelo
```

Sin cambios en `pyproject.toml` (no hay deps nuevas: pydantic v2 ya está).

**Fuera de alcance (próximas fases):** `providers/twscrape_provider.py`, `providers/official_api.py`, `providers/factory.py`, `mappers/`, `storage/`, `service/`, `cli.py`, extensión. Solo se crea el paquete `domain/` que se toca ahora.

---

## 8. Decisiones de implementación registradas

- **pydantic v2** idiomático: `BaseModel` + `ConfigDict(frozen=True)`, `field_validator`/`model_validator`. `from __future__ import annotations` (consistente con el resto del código).
- **`is_quote` como `@property`**, no campo: deriva de `quoted_tweet_id`, no puede desincronizarse.
- **Sin `is_retweet` ni `media_urls`** (YAGNI; ver D1). Agregarlos a futuro es aditivo y no rompe consumidores.
- **Validación de forma en el modelo, parsing de GraphQL solo en el mapper** (D2): el límite de capas del CLAUDE.md se mantiene.
- **`account` sin `@` y `content` con texto completo**: convenciones que cumple el mapper; el modelo solo valida no-vacío / forma.

---

## 9. Refinamientos post-revisión adversarial (2026-06-08)

Una revisión adversarial multi-lente (pydantic, typing, CLAUDE.md, edge cases) sobre la implementación confirmó tres mejoras de robustez, todas dentro del espíritu de D2 ("el modelo valida *forma* estricta y falla temprano"). No cambian el shape ni tocan el Compliance Gate.

- **R1 — Los validadores `_no_vacio` normalizan (trim), no solo chequean.** Antes hacían `v.strip()` para *decidir* pero devolvían `v` sin trimear: incoherente (ni normalizaban ni rechazaban el whitespace de borde). Ahora devuelven `v.strip()`. Aplica a `Tweet.id`/`account` y `TweetLink.url`/`expanded_url`. Esto es higiene de *forma* (whitespace de borde); **quitar el `@` de `account` sigue siendo del mapper** (normalización semántica, distinta).

- **R2 — `quoted_tweet_id`/`quoted_tweet_url` no pueden ser strings vacíos cuando están presentes.** `_quoted_coherente` usa `is None`, así que un `quoted_tweet_id=""` (string vacío) pasaba la coherencia y dejaba `is_quote == True` con basura (quote degenerado). Se agrega `_quote_no_vacio` (`field_validator`): `None` → `None`; si está presente, trimea y **rechaza** el vacío (fail-fast, en vez de silenciarlo). `is_quote` se mantiene como `quoted_tweet_id is not None` (ahora seguro: nunca `""`).

- **R3 — Tests guardián del contrato de tipo.** Se ancla con tests que `id`/`quoted_tweet_id` como `int` se **rechazan** (pydantic v2 no coerciona `int → str`): refuerza "snowflake como string, no int" como invariante verificada, no solo comentario.

Tests del dominio: 14 → 20. (Los descartes de la revisión: la narrativa "rompe la dedupe por PK / severidad high" estaba inflada — la normalización del mapper y el storage aún no existen; el fix se aplicó por coherencia de bajo riesgo, no como bug crítico.)

---

## 10. Hardening post code-review xhigh recall (2026-06-08)

Un segundo code-review (9 finder angles → verify 3-estado → sweep; 31 candidatos → 15 findings consolidados en 7 temas) motivó cinco endurecimientos, todos alineados con D2 (fail-fast de forma). No cambian el shape.

- **T1 — `created_at` naive: usar `utcoffset() is None`, no `tzinfo is None`.** El test canónico de "naive" en Python es `utcoffset() is None`; un `tzinfo` cuyo `utcoffset()` devuelve `None` pasaba el guard `tzinfo is None` y `.astimezone(UTC)` lo interpretaba como hora **local** (timestamp no-determinista por máquina). El check más fuerte cierra el hueco.
- **T2 — `created_at` rechaza epoch/string.** `field_validator(mode="before")` que exige `isinstance(v, datetime)`. Antes, pydantic coercionaba un `int` (epoch seg **o** ms) o un string a un `datetime` en silencio; un mixup seg/ms producía una fecha plausible-pero-errada sin señal. El mapper debe pasar un `datetime` ya parseado.
- **T4 — `display_url` normaliza whitespace.** Era el único string de forma que escapaba la higiene R1: `display_url="   "` se guardaba tal cual. Ahora un `field_validator` lo trimea y mapea whitespace-only → `None` (campo opcional/cosmético).
- **T5 — `extra="forbid"` en `Tweet` y `TweetLink`.** Un campo desconocido (typo del mapper, p. ej. `quoted_tweet_ur`) ahora **falla** en vez de descartarse en silencio — coherente con el fail-fast de D2.
- **T3 — `content` vacío es válido a propósito.** Asimétrico con `id`/`account` (que se trimean y rechazan vacíos), pero deliberado: un tweet de solo-media/link/quote tiene body vacío, y `content` es texto libre que **no** se trimea (alteraría el contenido). Se documenta en el docstring y se ancla con un test.

**No accionado:**
- **T6 — `Tweet` no es hasheable** (el campo `links: list` lo impide pese a `frozen`). Decisión consciente previa (`list` sobre `tuple`): la dedupe es por `id` en SQLite, no `set[Tweet]` en memoria. Se documenta en el docstring; sin cambio de tipo.

Tests del dominio: 20 → 32.
