# Spec — Núcleo crítico: Compliance Gate + contrato `TweetProvider`

**Fecha:** 2026-06-04
**Alcance de la sesión:** scaffolding del proyecto + el Compliance Gate completo con sus tests. **No** incluye scraping real, mappers, storage/CSV, service ni CLI.
**Documentos base:** `CLAUDE.md` (reglas innegociables) y `docs/plan-extractor-tweets.md` (plan completo). Este spec **complementa** el plan: solo documenta lo específico del núcleo crítico y las desviaciones justificadas respecto del boceto del plan.

---

## 1. Objetivo

Construir la invariante de cumplimiento del proyecto **antes que cualquier camino de fetch**: garantizar por código que es imposible acceder a más de `hard_cap = 900_000` objetos-tweet en cualquier ventana móvil de 24 h (cláusula de daños liquidados de los ToS de X). El gate debe ser persistente, atómico, de ventana deslizante real, contar accesos (no lo guardado) y no deduplicar.

Resultado verificable de la sesión: `uv run pytest` verde, con los tests obligatorios del `CLAUDE.md` cubiertos.

---

## 2. Desviaciones respecto del boceto del plan (`docs/plan-extractor-tweets.md:124-183`)

El boceto del gate en el plan tiene tres defectos que, copiados literalmente, **violarían los propios innegociables del `CLAUDE.md`**. Las correcciones **fortalecen** el gate; ninguna toca el `hard_cap` (sigue 900.000) ni debilita el cumplimiento.

### C1 — Id de reserva autoincrement, no `ts`
El boceto usa el timestamp en segundos como identificador de reserva (`return now`; `UPDATE … WHERE ts=?`). Dos reservas dentro del mismo segundo comparten `ts`; el `reconcile` por `ts` actualizaría la fila equivocada o varias a la vez, corrompiendo el conteo **hacia abajo** → riesgo real de cruzar el cap.
**Fix:** PK `id INTEGER PRIMARY KEY AUTOINCREMENT`; `reserve` devuelve `lastrowid`; `reconcile` matchea por `id`.

### C2 — Reserva-*antes*-de-pedir de verdad
El boceto hace `async for page in inner.fetch_pages()` y reserva *después*. Cuando el `async for` entrega la página, el request HTTP **ya ocurrió** → el acceso pasó antes de la reserva, violando "reserva-antes-de-pedir, falla cerrado".
**Fix:** contrato *pull* `fetch_page(query, cursor) -> Page`. El gate hace `reserve → fetch → reconcile` en ese orden estricto.

### C3 — Reloj y sleep inyectables
El boceto hardcodea `time.time()` y `asyncio.sleep`. El test obligatorio (b) —"esperar hasta que el evento más viejo salga de la ventana"— sería lento y flaky con tiempo real.
**Fix:** `clock` y `sleep` inyectables vía constructor (defaults `time.time` / `asyncio.sleep` en producción). Habilita tests determinísticos y, a futuro, anclar el reloj a una fuente confiable (caveat del plan, `docs/plan-extractor-tweets.md:308`).

### Mejora menor — `sleep` fuera del lock
El boceto duerme con el `asyncio.Lock` tomado, bloqueando globalmente a las demás corrutinas durante la espera. La sección crítica (leer-decidir-insertar) va bajo lock; el `await sleep` va **fuera**. Trade-off conocido y aceptado: head-of-line blocking si el gate está lleno (una reserva grande esperando puede demorar a una chica). Es seguro (nunca cruza el cap) y, dado que el gate casi nunca se dispara en el uso real, no se optimiza con colas de prioridad (YAGNI).

---

## 3. Contrato `TweetProvider` (`providers/base.py`)

Es el único punto de intercambio scraping↔API oficial, así que se fija bien desde el día 1.

```python
RawTweet = dict[str, Any]   # JSON crudo del backend; el shape final lo define el mapper (fase futura)

@dataclass
class SearchQuery:
    username: str
    since: datetime
    until: datetime
    include_quotes: bool = True
    include_retweets: bool = False

@dataclass
class Page:
    tweets: list[RawTweet]
    accessed_count: int          # TODO objeto-tweet tocado: citante + quote embebido + RT descartado (≠ len(tweets))
    next_cursor: str | None = None

class TweetProvider(ABC):
    max_accessed_per_page: int   # cota superior de accesos por página (= 2 * page_size, por quote embebido de 1 nivel)

    @abstractmethod
    async def fetch_page(self, query: SearchQuery, cursor: str | None) -> Page: ...

    async def fetch_tweets(self, query: SearchQuery) -> AsyncIterator[RawTweet]:
        cursor: str | None = None
        while True:
            page = await self.fetch_page(query, cursor)
            for t in page.tweets:
                yield t
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
```

- Las subclases concretas (futuro `TwscrapeProvider`) implementan solo `fetch_page` y declaran `max_accessed_per_page`. La paginación (`fetch_tweets`) es gratis en la base.
- El troceado en sub-ventanas (`from:user since/until`) queda encapsulado dentro del provider concreto (el `cursor` opaco puede codificar sub-ventana + cursor de página). Fuera del alcance de esta sesión.

---

## 4. `GatedProvider` (`compliance/gated_provider.py`)

```python
class GatedProvider(TweetProvider):
    def __init__(self, inner: TweetProvider, gate: SlidingWindowGate):
        self._inner = inner
        self._gate = gate
        self.max_accessed_per_page = inner.max_accessed_per_page

    async def fetch_page(self, query, cursor):
        rid = await self._gate.reserve(self._inner.max_accessed_per_page)   # 1. reserva cota superior
        page = await self._inner.fetch_page(query, cursor)                  # 2. request real
        await self._gate.reconcile(rid, page.accessed_count)               # 3. concilia a lo real
        return page
```

- Hereda `fetch_tweets` de la base; como `fetch_tweets` llama `self.fetch_page` (gateado), **ningún acceso saltea el gate**.
- La factory (`get_provider`, fase futura) siempre devuelve `GatedProvider(inner, gate)`; nadie obtiene una referencia al `inner` pelado. En esta sesión se testea con un `FakeProvider`.

---

## 5. `SlidingWindowGate` (`compliance/gate.py`)

### Ledger (SQLite de auditoría separado del SQLite de datos)
```sql
CREATE TABLE IF NOT EXISTS access_ledger (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts    INTEGER NOT NULL,          -- epoch en segundos del acceso
    count INTEGER NOT NULL           -- objetos-tweet tocados (cota superior; luego reconciliado a real)
);
CREATE INDEX IF NOT EXISTS idx_ledger_ts ON access_ledger(ts);
```

### API
```python
class ComplianceError(RuntimeError): ...

class SlidingWindowGate:
    def __init__(self, db_path, hard_cap=900_000, window_s=86_400,
                 *, clock=time.time, sleep=asyncio.sleep): ...
    async def setup(self) -> None: ...                       # crea schema (idempotente)
    async def reserve(self, n: int) -> int: ...              # -> reservation_id; espera si no entra; ComplianceError si n>cap
    async def reconcile(self, reservation_id: int, actual: int) -> None: ...
    async def usage(self, now: int | None = None) -> int: ...    # Σ count WHERE ts > now - window_s
    async def remaining(self, now: int | None = None) -> int: ...# hard_cap - usage
```

### Semántica (innegociables)
- **Ventana deslizante real:** `usage(now) = Σ count WHERE ts > now − window_s`. Nunca bucket de día calendario.
- **Cuenta accesos, no lo guardado:** el `count` es `accessed_count` (citante + quote embebido + RT descartado).
- **NO deduplica:** cada acceso suma; sobre-contar es seguro.
- **Reserva-antes-de-pedir / falla cerrado:** `reserve` inserta la **cota superior**, que cuenta como usado hasta el `reconcile`. Si `usage + n > cap`, espera hasta que el evento más viejo salga de la ventana (`(oldest + window_s) − now`).
- **Atómico:** el ciclo leer-decidir-insertar va bajo `asyncio.Lock`. Conexión `aiosqlite` abierta/cerrada por operación dentro del lock (robusto ante crashes; el lock serializa, el throughput es irrelevante para este uso). `sleep` fuera del lock.
- **Persistente:** vive en SQLite; sobrevive reinicios y crashes. Path configurable, separado del SQLite de datos.
- `n > hard_cap` → `ComplianceError` (un único pedido nunca podría entrar).

---

## 6. Configuración (`config.py`, pydantic-settings)

```python
class Settings(BaseSettings):
    audit_db_path: Path = Path("data/audit/ledger.db")
    data_db_path: Path = Path("data/tweets.db")
    hard_cap: int = 900_000          # NO subir sin instrucción explícita del usuario
    window_s: int = 86_400
    page_size: int = 20
    x_auth_token: str | None = None  # cookie de sesión (de .env)
    x_ct0: str | None = None
```

El gate recibe sus parámetros por constructor (no lee `Settings` directamente) para mantenerlo desacoplado y testeable; el factory/CLI inyecta desde `Settings`. `max_accessed_per_page = 2 * page_size`.

---

## 7. Estrategia de tests

`pytest` + `pytest-asyncio` (`asyncio_mode = auto`). DB temporal por test (`tmp_path`). `FakeClock` (valor mutable, `.time()`), `fake_sleep` (avanza el clock en vez de dormir) y `FakeProvider` (páginas configurables) en `tests/conftest.py`.

**`tests/compliance/test_gate.py`:**
1. reserve dentro del presupuesto inserta y devuelve un `id`.
2. `n > hard_cap` → `ComplianceError`.
3. **Bloqueo (obligatorio):** ledger pre-cargado cerca del cap; un `reserve` que excede espera (invoca `sleep`); con clock fake, al avanzar el tiempo para que el evento viejo salga de la ventana, procede.
4. **Espera hasta salir de la ventana (obligatorio):** evento viejo pre-cargado; verifica que la reserva se libera exactamente cuando `ts_viejo` cae fuera de `window_s`.
5. **Ventana deslizante real:** dos cargas de ~cap/2 a <24 h → la segunda bloquea; a >24 h → no (regresión del bug de día calendario).
6. `reconcile` baja el count → libera presupuesto.
7. `usage` refleja la cota superior reservada hasta el `reconcile` (falla cerrado).
8. **Persistencia:** cerrar y reabrir el gate (mismo `db_path`) preserva el uso.
9. **No-dedup:** dos reserves del mismo "acceso" cuentan doble.
10. **Concurrencia:** `asyncio.gather` de N reserves; la suma nunca cruza el cap.
11. **Regresión C1:** dos reserves con el mismo `ts` (clock fijo) → `id` distintos; `reconcile` de uno no afecta al otro.

**`tests/compliance/test_gated_provider.py`:**
1. `fetch_page` reserva `max_accessed_per_page` (cota superior) **antes** y reconcilia con `page.accessed_count` **después**.
2. `fetch_tweets` pagina y **cada** página pasa por el gate.
3. El conteo usa `accessed_count`, no `len(tweets)` (página con `accessed_count > len(tweets)`).
4. Si el gate bloquea, el fetch espera (integración con clock fake).

---

## 8. Estructura creada en esta sesión

```
pyproject.toml, uv.lock, .python-version, .node-version, .env.example, .gitignore, README.md
src/tweet_extractor/__init__.py
src/tweet_extractor/config.py
src/tweet_extractor/providers/{__init__.py, base.py}
src/tweet_extractor/compliance/{__init__.py, gate.py, gated_provider.py}
tests/{__init__.py, conftest.py}
tests/compliance/{__init__.py, test_gate.py, test_gated_provider.py}
```

**Fuera de alcance (próximas fases):** `domain/models.py` (el `Tweet`), `twscrape_provider.py`, `official_api.py`, `factory.py`, `mappers/`, `storage/`, `service/`, `cli.py`, extensión. Solo se crean los paquetes que se tocan ahora; el resto se crea en su fase para no sembrar archivos muertos.

---

## 9. Decisiones de implementación registradas

- **Conexión SQLite:** abrir/cerrar por operación bajo el lock (robusto ante crashes; serialización por lock; throughput irrelevante para este uso).
- **`max_accessed_per_page = 2 * page_size`:** cota superior por quote embebido de 1 nivel (consistente con el plan).
- **`RawTweet = dict[str, Any]`:** el shape final lo fija el mapper en su fase; el gate es agnóstico al contenido.
- **Head-of-line blocking** aceptado (ver Mejora menor §2).
- **Python 3.12** (alineado con el Docker Alpine del `CLAUDE.md`). `pytest-asyncio` agregado a dev deps (no estaba listado pero es necesario para tests async).
