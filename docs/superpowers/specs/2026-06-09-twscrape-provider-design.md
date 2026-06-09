# Spec — `TwscrapeProvider` (provider concreto de scraping)

**Fecha:** 2026-06-09
**Alcance de la sesión:** `src/tweet_extractor/providers/twscrape_provider.py`, `src/tweet_extractor/providers/_twscrape_gql.py`, `src/tweet_extractor/providers/subwindows.py`, ajustes a `config.py` y `.env.example`, y sus tests. **No** incluye el mapper (`mappers/twscrape_mapper.py`), el loop orquestador que recorre sub-ventanas (fase CLI), storage/CSV, service ni el troceado adaptivo.
**Documentos base:** `CLAUDE.md` (reglas innegociables), `docs/plan-extractor-tweets.md` (plan completo, §1 y ODQ), `docs/superpowers/specs/2026-06-04-compliance-gate-core-design.md` (contrato `TweetProvider`/`SearchQuery`/`Page` y `GatedProvider`, ya implementados) y `docs/superpowers/specs/2026-06-08-domain-model-design.md` (modelo de dominio). Este spec **complementa** el plan: documenta el paso 4 del orden de construcción de CLAUDE.md.

---

## 1. Objetivo

Implementar `TwscrapeProvider`: el primer `TweetProvider` concreto (capa 1, `providers/`), backend de scraping gratis sobre los endpoints GraphQL internos de x.com, vía `twscrape` (httpx, sin navegador). Entrega **dicts crudos de GraphQL** (`tweet_results.result`) por página, listos para que el mapper de la fase siguiente los interprete.

Conforma el contrato ya existente `TweetProvider.fetch_page(query, cursor) -> Page` (paso a paso de una página con cursor explícito), de modo que el `GatedProvider` lo envuelva sin cambios y **ningún acceso saltee el Compliance Gate**.

Resultado verificable de la sesión: `uv run pytest` verde (nuevos tests del provider, todos offline), `uv run mypy src` limpio, `uv run ruff check .` limpio.

---

## 2. Restricción vinculante: por qué la integración es a nivel de página

`GatedProvider.fetch_page` reserva la cota superior **antes** de llamar `inner.fetch_page` y reconcilia al conteo real después (regla #1 de CLAUDE.md: *reservar-antes-de-pedir, falla cerrado*). Por lo tanto `TwscrapeProvider.fetch_page` **debe** corresponder a exactamente **un** request de página, aceptando un cursor externo y devolviendo el cursor siguiente.

La API de alto nivel de twscrape (`api.search()` / `api.search_raw()`) **es dueña de su propio bucle de paginación** (`_gql_items`): maneja el cursor internamente y cada `yield` ya hizo el request. No expone "traeme una página dado un cursor". Reservar después de ese yield violaría el fail-closed por-request. Conclusión (decidida en brainstorming): integrarse con twscrape a **nivel de página** (transporte low-level), no con la API de alto nivel.

---

## 3. Decisiones de diseño

### D1 — twscrape como transporte low-level, acoplamiento aislado
Se usa `twscrape` para: pool de cuentas + auth por cookies, rotación, rate-limit awareness/backoff (`QueueClient`), y el `OP_SearchTimeline` que **embebe el `queryId` y lo auto-actualiza** vía codegen (CLAUDE.md: *no hardcodear queryId*; *usar librerías que se auto-actualicen*). Se NO usa su parser de alto nivel (`parse_tweets`) ni su modelo `Tweet`.

Todo el acoplamiento con internals de twscrape vive en **un solo archivo**, `providers/_twscrape_gql.py`. Si twscrape refactoriza esos nombres, se toca un único lugar. El resto del provider es lógica pura.

### D2 — Frontera explícita: el provider navega el *envelope*, el mapper interpreta el *tweet*
El provider hace **navegación de envelope/paginación**: de la respuesta extrae (a) la lista de objetos-tweet crudos de nivel-tope y (b) el cursor. **No interpreta** los tweets (quote vs RT, `__typename`/`TweetWithVisibilityResults`, `expanded_url`): eso es del mapper (CLAUDE.md: *parsing de GraphQL solo en `mappers/`*). Esta línea separa "transporte/paginación" de "interpretación de tweets" y mantiene ambas capas testeables en aislamiento.

### D3 — `Page.tweets` = dicts crudos de GraphQL
Coherente con `RawTweet = dict[str, Any]` del contrato (`providers/base.py`) y con el mapper descrito en CLAUDE.md (detecta `quoted_status_result`/`retweeted_status_result`, normaliza typenames, extrae `expanded_url`). El provider entrega cada `content.itemContent.tweet_results.result` tal cual; el mapper lo consume en la fase siguiente.

### D4 — Una cuenta desde `.env` (estructura pool-friendly)
`build_pool(settings)` carga una cuenta en el `AccountsPool` de twscrape desde `X_AUTH_TOKEN`/`X_CT0` (como ya tiene `config.py`). Suficiente para el MVP Fase 1; el `AccountsPool` admite N cuentas sin cambiar el provider, así que crecer a un pool (≤5, ODQ 11) es aditivo.

### D5 — Sub-ventanas: helper puro de paso fijo (adaptivo = futuro)
`subwindows(since, until, step_days) -> Iterator[(datetime, datetime)]`: trocea `[since, until)` en tramos de `step_days` (default 7), **sin solape** (solape = presupuesto del gate desperdiciado). Función **pura**, sin I/O, testeable exhaustivamente. El loop que la consume (un `SearchQuery` por tramo → `gated.fetch_tweets` por cada uno) es de la fase CLI: así el gating queda intacto (poner el slicing en `fetch_tweets` del inner sería inútil — el `GatedProvider` externo usa su propio `fetch_tweets` heredado y nunca llamaría al override). El troceado **adaptivo** (bisecar al acercarse al techo de ~3.200 por query) se difiere: necesita feedback del fetch, o sea el loop de orquestación.

### D6 — Replies: capturar todo en esta fase
`from:user since/until` sin filtro de replies → máxima cobertura, provider más simple. La política "self-thread sí / reply a tercero no" es semánticamente frágil a nivel query (`-filter:replies` saca *todas* las replies, también las self) y requiere la definición *por raíz de conversación* usando `conversationId`. Eso es interpretación de tweets → se decide e implementa en la **fase del mapper**, donde están los datos para resolverlo bien (incl. el caso del self-reply que cuelga de una conversación ajena).

### D7 — Operadores de fecha (`since:`/`until:`), ventanas alineadas a UTC
`build_query` usa `since:{%Y-%m-%d}`/`until:{%Y-%m-%d}` (granularidad por día), **no** `since_time:`/`until_time:` (epoch). Justificación: ambos pegan al mismo endpoint con idéntico costo por request (no hay diferencia de eficiencia); los segundos no se necesitan; y los operadores por fecha son los más battle-tested → menor riesgo de que el endpoint GraphQL los **ignore silenciosamente** (un no-op del filtro devolvería todo y drenaría el presupuesto del gate). `until:` es exclusivo y `since:` inclusivo → `[since:D0 until:D7)` ∪ `[since:D7 until:D14)` no solapa ni deja hueco. El paso default de 7 días deja las ventanas alineadas a medianoche UTC; el filtro fino por instante lo hace después el `created_at` ya parseado en storage.

### D8 — Inyección de dependencia del fetcher (tests offline)
`TwscrapeProvider.__init__(..., page_fetcher=fetch_search_page)`. El `page_fetcher` es el **único seam de red**; por default es `_twscrape_gql.fetch_search_page`. Los tests inyectan un fake que devuelve fixtures de SearchTimeline → se ejercita toda la lógica de `fetch_page` (extracción, conteo, cursor) **sin tocar la red**.

---

## 4. Componentes

### 4.1 `providers/_twscrape_gql.py` — superficie de acoplamiento (la ÚNICA que importa internals)

```python
from twscrape import AccountsPool
from twscrape.api import OP_SearchTimeline, GQL_URL, GQL_FEATURES  # OP_* lleva el queryId auto-actualizado
from twscrape.queue_client import QueueClient                       # rotación + rate-limit/backoff
from twscrape.utils import encode_params

from tweet_extractor.providers.base import ProviderError            # excepción de dominio (definida en base.py)

async def fetch_search_page(pool: AccountsPool, query_str: str, count: int,
                            cursor: str | None) -> dict:
    variables = {"rawQuery": query_str, "count": count,
                 "product": "Latest", "querySource": "typed_query"}
    if cursor is not None:
        variables["cursor"] = cursor
    params = {"variables": variables, "features": GQL_FEATURES,
              "fieldToggles": {"withArticleRichContentState": False}}
    async with QueueClient(pool, "SearchTimeline", False) as client:
        rep = await client.get(f"{GQL_URL}/{OP_SearchTimeline}", params=encode_params(params))
    if rep is None:
        raise ProviderError("twscrape no pudo completar el request (cuenta inválida o rate-limit agotado)")
    return rep.json()
```

- `product: "Latest"` → reverse-chrono (no relevancia): mejor cobertura dentro de la ventana (plan §1).
- `QueueClient` por-página (open→get→close): el estado de rate-limit (locks, reset) vive en el `AccountsPool` (persistido en `accounts.db`), no en el client, así que recrearlo respeta los locks. El backoff/espera ante 429 lo maneja twscrape.
- `rep is None` → `ProviderError` (no enmascarar como "fin de resultados", que sub-contaría; ver §6).

### 4.2 `providers/twscrape_provider.py` — el `TweetProvider` (lógica pura + DI)

```python
class TwscrapeProvider(TweetProvider):
    def __init__(self, settings, pool, *, page_fetcher=fetch_search_page):
        self.max_accessed_per_page = settings.max_accessed_per_page  # cota de reserva del gate (=60)
        self._count = settings.page_size
        self._pool = pool
        self._fetch = page_fetcher

    async def fetch_page(self, query: SearchQuery, cursor: str | None) -> Page:
        raw = await self._fetch(self._pool, build_query(query), self._count, cursor)
        return Page(
            tweets=extract_tweet_results(raw),       # pura (§5/D2)
            accessed_count=count_accessed(raw),      # pura, sobre-cuenta (§5)
            next_cursor=extract_bottom_cursor(raw),  # pura
        )
```

Helpers **puros, sin imports de twscrape** (módulo-nivel, testeables directo):

- `build_query(query) -> str`: `f"from:{query.username} since:{query.since:%Y-%m-%d} until:{query.until:%Y-%m-%d}"` (D7). `query.since/until` son UTC tz-aware (garantía de `SearchQuery`).
- `extract_tweet_results(raw) -> list[dict]`: navega defensivamente `data → ... → instructions[] → entries[]`, filtra `entryId` que empieza con `"tweet-"` y devuelve `content.itemContent.tweet_results.result`. Los quotes/RT embebidos **no** son entries (viven dentro de `result`) → no se cuentan como tweets de nivel-tope. Tolera ausencia de claves (página vacía → `[]`).
- `extract_bottom_cursor(raw) -> str | None`: busca recursivamente el primer objeto con `cursorType == "Bottom"` y devuelve su `value` (robusto a renombres de `entryId`; mismo criterio que twscrape). `None` si no hay → la paginación heredada (`fetch_tweets` de `base.py`) corta.
- `count_accessed(raw) -> int`: ver §5.

### 4.3 `providers/subwindows.py` — helper puro (D5)

```python
def subwindows(since: datetime, until: datetime, step_days: int = 7) -> Iterator[tuple[datetime, datetime]]:
    # [since, until) en tramos de step_days, sin solape; último tramo recortado a `until`.
```

Precondiciones (espejo de `SearchQuery`): `since`/`until` tz-aware, `since < until`, `step_days >= 1`. Casos cubiertos por tests: rango menor que un paso, rango exacto, no-alineado (último tramo parcial), step de 1 día.

### 4.4 `async def build_pool(settings) -> AccountsPool`

**Async** (las operaciones del pool de twscrape lo son). Crea `AccountsPool(db_file=str(settings.accounts_db_path))` y, si la cuenta no existe aún, `await pool.add_account(username="xport-session", password="", email="", email_password="", cookies=f"auth_token={settings.x_auth_token}; ct0={settings.x_ct0}")`. Con `ct0` presente la cuenta queda `active=True` sin login. Idempotente (twscrape persiste en `accounts.db` y avisa si ya existe). Si faltan cookies en el entorno → `ProviderError`/error claro de configuración (no intentar login). El caller (CLI/factory) hace `pool = await build_pool(settings); provider = TwscrapeProvider(settings, pool)`.

---

## 5. `accessed_count` — cumplimiento (sobre-contar es seguro)

`count_accessed(raw)` = cantidad de dicts con `__typename ∈ {"Tweet", "TweetWithVisibilityResults"}` en **cualquier nivel** del JSON de la página.

- Cuenta **automáticamente** citante + quote embebido + RT descartado (todos llevan ese `__typename`), sin "interpretar" tweets: es un conteo estructural.
- Un `TweetWithVisibilityResults` cuenta **2** (el wrapper + el `Tweet` interno bajo `.tweet`) → sobre-cuenta, que es la dirección **segura** del cap (CLAUDE.md: *el ledger NO deduplica; sobre-contar es seguro*).
- `GatedProvider` ya aplica el piso `max(accessed_count, len(tweets))`; como cada tweet entregado tiene `__typename`, naturalmente `count_accessed >= len(tweets)`.

**Invariante preservada:** `max_accessed_per_page` (= `page_size * ACCESS_FACTOR_PER_TWEET` = 20·3 = 60, en `config.py`) es la **cota superior de reserva** y debe seguir siendo cota superior real de objetos por página. Si alguna vez sube `count`/`page_size`, hay que subir el factor en la misma proporción. No se toca el gate.

---

## 6. Manejo de errores (ODQ 10)

- **Fetch incompleto** (`rep is None`: cuenta inválida/suspendida o rate-limit agotado tras los reintentos de twscrape) → `ProviderError`. **No** se devuelve `Page` vacía: eso se vería como "fin de resultados" y haría cortar la paginación enmascarando el fallo (y sub-contaría). La reserva ya hecha por el `GatedProvider` queda en la cota superior y no se reconcilia hacia abajo (fail-closed, coherente con el comentario de `gated_provider.py`).
- **Cuenta-objetivo borrada/protegida/suspendida** (distinta de la cuenta de scraping) → la búsqueda devuelve 0 resultados / sin `entries` → `extract_tweet_results` = `[]` y `extract_bottom_cursor` = `None` → fin natural de la paginación. La orquestación (fase CLI) reporta "0 tweets" para esa cuenta sin abortar el resto del job.
- `ProviderError` se define en `providers/base.py` (excepción de dominio de la capa de providers), para que mapper/orquestación la traten uniformemente con futuros providers.

---

## 7. Config y dependencias

- `uv add twscrape` (arrastra `httpx`).
- `Settings` (`config.py`) — campos nuevos (no son `ClassVar`; configurables por entorno, pero **no** tocan el gate):
  - `accounts_db_path: Path = Path("data/accounts.db")` — store de cuentas/cookies de twscrape. **Git-ignored** (ya cubierto por `*.db` y `data/`); separado del ledger de auditoría (`data/audit/ledger.db`) y del de datos (`data/tweets.db`).
  - `subwindow_days: int = 7` — paso default del troceado.
- `.env.example`: confirmar/asegurar `X_AUTH_TOKEN=` y `X_CT0=` (sin valores). Recordatorio de cuenta descartable.

---

## 8. Estrategia de tests (todo offline)

Fixtures de respuestas `SearchTimeline` **sintéticas pero realistas** (shape documentado en plan §1 y en `twscrape/models.py`): un `Tweet`, un `TweetWithVisibilityResults`, un quote (con `quoted_status_result` anidado), un RT (con `retweeted_status_result` anidado), entries de cursor (`cursor-bottom`/`cursor-top`), y una página vacía/final. Estas fixtures **siembran** también la fase del mapper.

- **Helpers puros con fixtures:**
  - `extract_tweet_results`: devuelve solo tweets de nivel-tope; ignora quotes/RT anidados y entries de cursor; página vacía → `[]`.
  - `extract_bottom_cursor`: devuelve el `value` del cursor Bottom; `None` al final.
  - `count_accessed`: **sobre-cuenta** quotes y RT embebidos (test de compliance: un quote ⇒ count ≥ 2; un `TweetWithVisibilityResults` ⇒ +2); `count_accessed >= len(extract_tweet_results)`.
  - `build_query`: formato exacto de fecha y operadores.
  - `subwindows`: bordes (rango < paso, exacto, no-alineado, step=1), sin solape ni hueco, tz preservada.
- **`fetch_page`** con `page_fetcher` fake → arma el `Page` correcto (tweets, accessed_count, next_cursor) sin red.
- **Wiring `GatedProvider(TwscrapeProvider)`** con fetcher fake de `accessed_count` conocido: el gate reserva `max_accessed_per_page` y reconcilia al valor real; la paginación heredada corta con `next_cursor=None` y con cursor repetido (guard ya existente en `base.py`).
- **`build_pool`**: con cookies de prueba arma el `AccountsPool` y la cuenta queda activa (usando una `accounts.db` temporal de pytest); sin cookies → error de config.

`mypy --strict` y `ruff` limpios.

---

## 9. Estructura a crear

```
src/tweet_extractor/providers/
├── base.py                  # (existe) + ProviderError
├── _twscrape_gql.py         # NUEVO — superficie de acoplamiento (fetch_search_page)
├── twscrape_provider.py     # NUEVO — TwscrapeProvider, build_query, extract_*, count_accessed, build_pool
└── subwindows.py            # NUEVO — subwindows()

tests/providers/
├── test_base.py             # (existe)
├── conftest.py o fixtures/  # NUEVO — fixtures SearchTimeline sintéticas
├── test_twscrape_extract.py # NUEVO — helpers puros (extract_*, count_accessed, build_query)
├── test_twscrape_provider.py# NUEVO — fetch_page + wiring GatedProvider (fetcher fake)
└── test_subwindows.py       # NUEVO — helper puro de sub-ventanas
```

`config.py` y `.env.example` se ajustan in-place.

---

## 10. Qué se difiere (fronteras de esta fase)

- **Mapper** (`mappers/twscrape_mapper.py`): interpretación tweet-a-tweet (quotes sí, RT no, links, typenames, política de replies por raíz). Consume los dicts crudos que entrega este provider.
- **Loop orquestador** de sub-ventanas (un `SearchQuery` por tramo) → fase CLI/service.
- **Troceado adaptivo** (bisección al topar ~3.200) → futuro; requiere feedback del fetch.
- **Pool de N cuentas** → aditivo cuando se necesite (ODQ 11).
- **Provider oficial** (`OfficialApiProvider`) y su mapper → fases posteriores.

---

## 11. Verificaciones contra comportamiento vivo (cuando haya cookies)

No bloquean esta fase (los tests son offline), pero quedan registradas como checks a correr con una cuenta descartable real antes de confiar el pipeline:

1. **El filtro temporal acota de verdad:** una query `from:user since: until:` de una ventana chica devuelve solo tweets dentro del rango (descartar que `since:`/`until:` sean no-op en el endpoint GraphQL).
2. **El cursor `Bottom` pagina y termina:** páginas sucesivas avanzan y la última repite/omite el cursor (el guard de `base.py` corta).
3. **Shape real vs fixtures:** confirmar que `tweet_results.result`, `quoted_status_result`, `retweeted_status_result` y `__typename` aparecen donde las fixtures asumen; ajustar la navegación defensiva si x.com cambió algo.
4. **`accessed_count` realista:** que la cota de reserva (60) siga siendo superior al conteo real por página observado.
