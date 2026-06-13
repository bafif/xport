# Plan de Implementación: Extractor de Tweets por Rango de Fechas → CSV, con Arquitectura por Capas, Tope ToS de 1M/24h y Extensiones de Navegador (2026)

## TL;DR
- **Construilo en Python con `twscrape` como backend de scraping inicial (gratis, basado en cookies `auth_token`+`ct0`), detrás de una interfaz abstracta `TweetProvider`**, de modo que mañana puedas enchufar el backend oficial de la X API sin tocar el resto del código. snscrape y Nitter están efectivamente muertos en 2026; twscrape, twikit y Scweet son los que siguen funcionando con sesión autenticada.
- **La extracción por rango de fechas viable a costo cero usa el operador de búsqueda `from:usuario since:YYYY-MM-DD until:YYYY-MM-DD`** (que esquiva el techo de ~3.200 tweets del timeline de usuario); los quote tweets se detectan por `quoted_status_result`/`referenced_tweets` con `type:"quoted"` y los retweets por `retweeted_status_result`, y los links salen de `entities.urls[].expanded_url`.
- **Para exponerlo en el navegador, la opción de menor fricción es un servicio FastAPI local (servido detrás de Nginx) que la extensión abre en una pestaña** (`localhost`); la alternativa más limpia para "abrir la app" es Native Messaging (MV3, soportado en Chrome y Firefox). Usás `uv` para Python, `fnm` para el toolchain JS/TS de la extensión, y compartís un único código entre Chrome (MV3) y Firefox con `webextension-polyfill`.
- **Cumplimiento estricto de los ToS por diseño: un *Compliance Gate* no evitable y transversal a todos los providers garantiza que nunca se accedan más de 1.000.000 de posts en ninguna ventana móvil de 24 h** (ventana deslizante real, contador persistente en SQLite que sobrevive reinicios, y patrón de *reserva-antes-de-pedir* que falla cerrado; cuenta accesos —incluidos quotes embebidos y retweets descartados—, no solo lo guardado, con `hard_cap` fijado en 900.000 global).

---

## Key Findings

1. **El ecosistema de scraping gratuito se redujo a herramientas con sesión autenticada.** snscrape funciona solo de forma intermitente y sin mantenimiento real (200+ issues abiertos, último commit significativo hace meses); Nitter fue declarado oficialmente muerto: su desarrollador "Zed" anunció "Nitter is dead" en GitHub el 29 de enero de 2024, después de que Twitter deshabilitara la creación de guest accounts el 26 de enero de 2024 (las instancias públicas colapsaron días después y el proyecto se discontinuó en febrero de 2024). Las que siguen vivas en 2026 son las que usan tus cookies de cuenta logueada contra los endpoints GraphQL internos: **twscrape**, **twikit**, **Scweet** y **tweety**. gallery-dl sirve para media pero su extractor de Twitter se rompe seguido.

2. **No existe tier gratuito útil de la X API en 2026.** Desde el 6 de febrero de 2026 el modelo por defecto es pay-per-use (lectura ~$0,005/post, escritura ~$0,01/post, con tope duro de 2 millones de lecturas/mes); los planes Basic ($200/mes) y Pro ($5.000/mes) quedaron **cerrados a nuevos registros** (solo legacy); full-archive search solo viene con Pro legacy (que incluye 1.000.000 lecturas/mes, filtered stream y full-archive) o con Enterprise (desde ~$42.000/mes, con cotizaciones reportadas de hasta $100K–250K/mes para alto volumen y un proceso de venta de varias semanas). Esto refuerza la decisión de empezar con scraping.

3. **La arquitectura inspirada en OSI funciona perfecto acá**: capa de proveedor de datos (intercambiable), capa de normalización, modelo de dominio (`Tweet` con pydantic), capa de persistencia (SQLite → CSV) y capa de servicio/presentación (FastAPI + extensión), más un *Compliance Gate* transversal que aplica el tope de los ToS. El contrato clave es una interfaz `TweetProvider` async.

4. **El riesgo legal y de baneo es real pero acotado si scrapeás poco volumen.** Los ToS de X (vigentes 15-ene-2026, cláusula originada en la versión del 15-nov-2024) prohíben el scraping sin permiso escrito y fijan daños liquidados de **$15.000 USD (€15.000 EUR en la UE) por cada 1.000.000 de posts** vistos/accedidos en 24 h. Tu caso de uso (una o pocas cuentas, nunca docenas) está muy por debajo de ese umbral, y el *Compliance Gate* lo hace una garantía dura por código; usar una cuenta secundaria descartable mitiga además el riesgo de baneo de tu cuenta principal.

---

## Details

### 1. Estado actual (2026) de la extracción sin pagar

**Herramientas y su estado real:**

| Herramienta | Estado 2026 | Mecanismo | Veredicto |
|---|---|---|---|
| **snscrape** | Semi-muerto; intermitente, sin mantenimiento, +200 issues abiertos | HTML/endpoints internos sin auth | Evitar para producción |
| **Nitter** | Discontinuado feb-2024 (guest accounts eliminadas el 26-ene-2024); instancias públicas colapsadas | Guest accounts (ya no existen) | No usar |
| **twscrape** | **Activo** (releases v0.18.x en mayo 2026), modelo de datos tipo snscrape | GraphQL con pool de cuentas + cookies, async, rate-limit aware | **Recomendado como backend principal** |
| **twikit** | Activo, async desde v2.0.0, módulo `guest` para operaciones sin login | API interna con login/cookies | Buena alternativa |
| **Scweet** | Activo (verificado mar-2026), self-heal de query IDs (`manifest_scrape_on_init`) | auth_token + proxies, multi-cuenta, date filter nativo | Alternativa con `since=` nativo |
| **tweety** | Activo pero advierte riesgo de cuenta read-only | Frontend API reverse-engineered | Secundario |
| **gallery-dl** | Activo para media, extractor twitter frágil (rompe seguido tras cambios de x.com) | cookies `auth_token` | Solo para descargar media |

**Cómo funciona el scraping basado en cookies/sesión:** las herramientas modernas no usan la API oficial; usan las cookies de una cuenta logueada (`auth_token` y `ct0`, el token CSRF) para llamar a los endpoints GraphQL internos de x.com (los mismos que usa la web). En twscrape agregás cuentas a un pool (`pool.add_account(user, pass, email, email_pass, cookies="abc=12; ct0=xyz")` — las cuentas con cookies tienen menos problemas de login que con user/pass), y la librería rota cuentas, refresca tokens y aplica backoff. El límite GraphQL ronda los **cientos de requests por cuenta por operación cada 15 minutos**; una sola cuenta maneja típicamente de cientos a unos pocos miles de tweets por día antes de topar.

**Filtrado por rango de fechas — dos caminos:**
- **Timeline de usuario** (operación GraphQL `UserTweets`): devuelve los posts más recientes en orden cronológico inverso, pero topea en **~3.200 tweets históricos** (límite de backend de X que no se puede saltar manipulando `count` o el cursor). Filtrás client-side por `created_at`.
- **Búsqueda** (recomendado para rangos amplios): usás el operador `from:usuario since:2023-01-01 until:2023-12-31`. La búsqueda evita el techo de 3.200 partiendo el rango en ventanas. Para "los últimos 3 años de una cuenta que postea mucho", el enfoque robusto es **trocear el rango en sub-ventanas** (p.ej. semanales) y paginar cada una. Nota: la búsqueda relega contenido viejo en el ranking, así que ventanas chicas con `Latest` dan mejor cobertura.

**Quote tweets vs retweets:**
- En el modelo GraphQL, cada tweet vive en `data.user.result.timeline_v2.timeline.instructions[]` → entrada de tipo `TimelineAddEntries` → `entry.content.itemContent.tweet_results.result` (que puede ser `__typename: "Tweet"` o `"TweetWithVisibilityResults"` — en este último el tweet real está bajo `.tweet`, hay que normalizar ambos).
- Un **quote tweet** trae el tweet citado embebido en `quoted_status_result.result` (al tope del result object) junto con `legacy.is_quote_status: true` y `legacy.quoted_status_id_str`. En la API v2 oficial aparece en `referenced_tweets` con `type: "quoted"`. **Codeá defensivamente para ambas ubicaciones** (`quoted_status_result.result` y, en algunas libs, `legacy.quoted_status_result`).
- Un **retweet** trae `legacy.retweeted_status_result.result` (v2: `type: "retweeted"`); el `full_text` externo es el truncado `"RT @user…"` y el contenido real está anidado. El requerimiento pide **incluir quotes y EXCLUIR retweets** → filtrás descartando los que tengan `retweeted_status_result`.
- Para el CSV: una columna `quoted_tweet_id`/`quoted_tweet_url` que linkea el tweet citante al citado.

**Links embebidos:** salen de `legacy.entities.urls[]`, donde `url` es el `t.co` y `expanded_url` es el destino real (el que querés); `display_url` es la versión legible. Media en `legacy.extended_entities.media[]` (`media_url_https`, `type` ∈ photo/video/animated_gif). Cuidado: en algunos posts autorreferenciales `expanded_url` apunta de vuelta a un status de x.com en lugar de a la URL externa — validá campos vacíos defensivamente.

### 2. El camino de la X API oficial (para la abstracción futura)

- **Precios 2026:** pay-per-use por defecto (~$0,005/lectura de post, ~$0,01/escritura, tope 2M lecturas/mes); Basic $200/mes y Pro $5.000/mes solo legacy (cerrados a nuevos registros); Enterprise desde ~$42.000/mes. Importante: rate limits y costo son controles **independientes** — podés tener crédito y aún así topar con 429.
- **Endpoints clave:**
  - `GET /2/users/by/username/:username` → resuelve username a user_id.
  - `GET /2/users/:id/tweets` → timeline del usuario, soporta `start_time`/`end_time` (ISO 8601 / RFC 3339, granularidad de segundos, `end_time` exclusivo), `max_results` hasta 100, paginación por `pagination_token`, tope 3.200 tweets. Devuelve Posts, Retweets, replies y Quote Tweets. (No hay forma de excluir solo quotes vía `exclude`; se filtran client-side por `referenced_tweets`.)
  - `GET /2/tweets/search/all` (full-archive, solo Pro legacy/Enterprise) → `query=from:usuario`, `start_time`/`end_time` desde marzo 2006, hasta 500 por página, tope duro de 1 req/seg.
  - `GET /2/tweets/:id/quote_tweets` → quotes de un tweet.
- **Campos:** pedir explícitamente `tweet.fields=created_at,entities,referenced_tweets,attachments,public_metrics,author_id` y `expansions=referenced_tweets.id,author_id,attachments.media_keys`. Por defecto la v2 solo devuelve `id` y `text`.
- **Rate limits:** ventanas de 15 min, límites separados per-app (Bearer) y per-user (OAuth). Recent search: 450/15min per-app, 300/15min per-user (100 resultados/página, query ≤512 chars). Full-archive: 300/15min per-app con tope duro de 1 req/seg (500 resultados/página, query ≤1.024 chars). El header `x-rate-limit-reset` da el epoch UTC del reset.

### 3. Arquitectura modular por capas (inspirada en OSI)

**Capas (de abajo hacia arriba):**

1. **Capa de Proveedor de Datos (`providers/`)** — interfaz abstracta intercambiable:
```python
from typing import AsyncIterator
from datetime import datetime
from abc import ABC, abstractmethod

class TweetProvider(ABC):
    @abstractmethod
    async def fetch_tweets(
        self, username: str, since: datetime, until: datetime,
        include_quotes: bool = True, include_retweets: bool = False,
    ) -> AsyncIterator["RawTweet"]: ...
```
   Implementaciones: `TwscrapeProvider`, `TwikitProvider`, `OfficialApiProvider` (stub al inicio). Una factory (`get_provider(config)`) decide cuál instanciar según config. Este es el punto único donde se intercambia "scraping ↔ API oficial".

2. **Capa de Normalización/Extracción (`mappers/`)** — convierte el JSON crudo de cada backend al modelo de dominio. Cada provider tiene su mapper; aísla las diferencias de formato (GraphQL anidado vs API v2 plana). Acá vive toda la lógica de detectar `quoted_status_result` vs `retweeted_status_result` y extraer `expanded_url`.

3. **Capa de Modelo de Dominio (`domain/`)** — un `Tweet` normalizado con pydantic, al que mapean AMBOS backends:
```python
from pydantic import BaseModel
from datetime import datetime

class TweetLink(BaseModel):
    url: str          # t.co
    expanded_url: str # destino real
    display_url: str | None = None

class Tweet(BaseModel):
    id: str
    account: str
    created_at: datetime  # UTC
    content: str
    is_quote: bool = False
    is_retweet: bool = False
    quoted_tweet_id: str | None = None
    quoted_tweet_url: str | None = None
    links: list[TweetLink] = []
    media_urls: list[str] = []
```

4. **Capa de Persistencia/Export (`storage/`)** — SQLite intermedio (con `INSERT OR IGNORE` por `id` para dedupe y checkpointing) y exportador CSV por cuenta (streaming, una fila a la vez).

5. **Capa de Servicio/API (`service/`)** — FastAPI exponiendo `POST /jobs` (lanzar extracción), `GET /jobs/{id}` (estado), `GET /jobs/{id}/csv` (descargar). Orquesta providers + storage. Background tasks para jobs largos.

6. **Capa de Presentación (`presentation/`)** — CLI (Typer/Click) para Fase 1; extensión de navegador para Fase 4. Ambas consumen las capas inferiores sin conocer el provider concreto.

**Componente transversal: Compliance Gate (cumplimiento estricto del tope de 1M/24h)**

Esta es una **invariante de seguridad no evitable**, no un "feature": ninguna extracción puede acceder a más de 1.000.000 de posts en ninguna ventana móvil de 24 h (cláusula de daños liquidados de los ToS, $15.000/1M). Tres decisiones la hacen *estricta*:

- **Ventana deslizante, no día calendario.** "Any 24-hour period" exige una ventana móvil real: un bucket fijo medianoche-a-medianoche NO cumple (999k a las 23:00 + 999k a la 01:00 ≈ 2M dentro de una misma ventana de 24 h). Se implementa con un *ledger* de eventos `(ts, count)` y `uso(now) = Σ count donde ts > now − 86400`.
- **Se cuentan accesos, no lo guardado.** Los ToS hablan de "requesting, viewing, or accessing", que es un superset de "recuperar/guardar". Por eso el contador suma **todo objeto-tweet que la respuesta entrega**: el citante, el **quote embebido** (que se accede aunque no se guarde como fila propia), y hasta los **retweets que descartamos**. La deduplicación afecta el CSV, no el ledger: si dos sub-ventanas solapadas re-piden el mismo tweet, cada acceso cuenta (sobre-contar = seguro; minimizar el solape ahorra presupuesto). El `hard_cap` efectivo se fija en **900.000, global (por cliente, no por cuenta de X)** — por debajo de 1M para absorber la incertidumbre de qué cuenta como "acceso", y único para toda la app porque el límite de los ToS es por vos como cliente, no por cuenta de X. El ledger vive en un SQLite de **auditoría separado** del de datos.
- **Reserva-antes-de-pedir, falla cerrado, persistente y atómico.** Antes de cada página se *reserva* una **cota superior real** de lo que podría tocar (≈2× el tamaño de página, porque cada entrada puede traer un quote embebido); si `uso + reserva > cap` se espera hasta que eventos viejos salgan de la ventana; tras el fetch se *reconcilia* al conteo real de objetos accedidos. El ledger vive en SQLite (sobrevive reinicios y crashes) y el ciclo leer-decidir-insertar va bajo un `asyncio.Lock` para que dos corrutinas concurrentes no vean "hay lugar" y se pasen juntas.

Vive como **decorator transversal** que envuelve *cualquier* `TweetProvider` (scraping u oficial), de modo que el tope se aplica idéntico sea cual sea el backend — encaja con el diseño de fuente intercambiable. Exige una pequeña extensión al contrato del provider: exponer granularidad de página/lote y reportar el "conteo de accesos" (incluidos quotes embebidos y RTs descartados), no solo los tweets emitidos.

```python
import time, asyncio, aiosqlite

class ComplianceError(RuntimeError): ...

class SlidingWindowGate:
    """Tope duro ToS: nunca acceder a > hard_cap posts en cualquier
    ventana móvil de window_s (24h). Cuenta TODO objeto-tweet tocado
    (emitido, quote embebido, o RT descartado). Persistente en SQLite."""
    def __init__(self, db_path, hard_cap=900_000, window_s=86_400):
        self.db_path, self.hard_cap, self.window_s = db_path, hard_cap, window_s
        self._lock = asyncio.Lock()

    async def _usage(self, db, now):
        cur = await db.execute(
            "SELECT COALESCE(SUM(count),0) FROM access_ledger WHERE ts > ?",
            (now - self.window_s,))
        (used,) = await cur.fetchone()
        return used

    async def reserve(self, n: int) -> int:
        """Reserva capacidad para hasta n posts ANTES del fetch. Bloquea
        (espera a que eventos viejos salgan de la ventana) si hace falta."""
        if n > self.hard_cap:
            raise ComplianceError(f"pedido de {n} excede el cap {self.hard_cap}")
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                while True:
                    now = int(time.time())
                    if await self._usage(db, now) + n <= self.hard_cap:
                        await db.execute(
                            "INSERT INTO access_ledger(ts,count) VALUES(?,?)", (now, n))
                        await db.commit()
                        return now  # id de reserva == ts (para reconciliar)
                    # esperar a que el evento más viejo salga de la ventana de 24h
                    cur = await db.execute(
                        "SELECT MIN(ts) FROM access_ledger WHERE ts > ?",
                        (now - self.window_s,))
                    (oldest,) = await cur.fetchone()
                    await asyncio.sleep(max(1, (oldest + self.window_s) - now))

    async def reconcile(self, reservation_ts: int, actual: int):
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("UPDATE access_ledger SET count=? WHERE ts=?",
                                 (actual, reservation_ts))
                await db.commit()

class GatedProvider(TweetProvider):
    """Aplica el tope a CUALQUIER provider, sea scraping u oficial."""
    def __init__(self, inner: TweetProvider, gate: SlidingWindowGate):
        self._inner, self._gate = inner, gate
    async def fetch_tweets(self, *a, **kw):
        async for page in self._inner.fetch_pages(*a, **kw):    # granularidad de página
            rid = await self._gate.reserve(page.max_possible)    # reserva cota superior
            # ... fetch ya resuelto dentro de fetch_pages ...
            await self._gate.reconcile(rid, page.accessed_count) # concilia a lo real
            for tweet in page.tweets:
                yield tweet
```

**Estructura de proyecto con uv:**
```
tweet-extractor/
├── pyproject.toml
├── uv.lock
├── .python-version
├── src/tweet_extractor/
│   ├── domain/models.py
│   ├── providers/{base.py,twscrape_provider.py,official_api.py,factory.py}
│   ├── mappers/{twscrape_mapper.py,api_v2_mapper.py}
│   ├── compliance/{gate.py,gated_provider.py}
│   ├── storage/{sqlite_store.py,csv_exporter.py}
│   ├── service/{app.py,routers.py}
│   └── cli.py
└── tests/
```
Comandos: `uv init`, `uv add twscrape pydantic httpx aiosqlite`, `uv add fastapi[standard]`, `uv add --dev pytest ruff mypy`, `uv run python -m tweet_extractor.cli`, `uv run fastapi dev src/tweet_extractor/service/app.py`. Las dependencias se agrupan en `[dependency-groups]` (p.ej. `dev`, `service`) en pyproject.toml; `uv sync` crea el `.venv` y genera `uv.lock` para builds reproducibles (`uv sync --frozen` en CI/deploy).

### 4. Manejo de volumen

- **Paginación/cursores:** loop sobre `next_token`/cursor hasta que no haya más; en búsqueda, trocear por sub-ventanas de fecha. En GraphQL el cursor está en una entrada `TimelineTimelineCursor` con `cursorType: "Bottom"` (más viejos) y `value` opaco.
- **Async + throttling:** `asyncio` con un semáforo limitando concurrencia y un rate-limiter que respeta el header `x-rate-limit-reset` (backoff exponencial 1→2→4→8s ante 429). No compartas un solo token entre workers: el límite es per-app.
- **Tope duro ToS (1M/24h):** lo aplica el *Compliance Gate* (ver Arquitectura) vía ventana deslizante + ledger persistente en SQLite + reserva-antes-de-pedir. La reserva usa una **cota superior real** (≈2× el tamaño de página, para cubrir los quotes embebidos que también se acceden) y luego reconcilia al conteo real; el ciclo leer-decidir-insertar va bajo lock para ser correcto con concurrencia async. Es una invariante no evitable, anterior a cualquier corrida. (Es importante NO deduplicar el contador de accesos: la dedupe es solo del CSV.)
- **Extracción incremental/resumible:** guardar checkpoints cada 100-500 tweets en SQLite; al reanudar, arrancar desde el último `id`/fecha procesado.
- **Storage intermedio:** SQLite como buffer antes del CSV (permite dedupe por PK, reintentos y consultas). Usar `aiosqlite` para no bloquear el event loop.
- **Streaming a CSV:** escribir fila por fila con el módulo `csv` de la stdlib (evitá cargar todo en memoria con pandas si el volumen es alto). Un archivo CSV por cuenta.

### 5. Exponer como app local + extensión (Firefox + Chrome)

**Tres patrones evaluados:**

**(a) Servidor local FastAPI + Nginx, extensión como launcher (RECOMENDADO para empezar).** La extensión abre `localhost` (o un hostname local lindo vía Nginx) en una pestaña; el FastAPI sirve la UI y la API. Nginx hace reverse-proxy del FastAPI (`proxy_pass http://127.0.0.1:8000`), sirve en un hostname limpio, maneja CORS y opcionalmente TLS para localhost. Es lo más simple y desacoplado, y casa con tu uso habitual de Nginx.

**(b) Native Messaging (MV3, Chrome + Firefox).** La extensión lanza/se comunica con un proceso nativo (host) local vía stdin/stdout con mensajes JSON. Requiere `"nativeMessaging"` en el manifest y un manifest de host nativo registrado en el SO (en Windows vía clave de registro; en Linux/macOS ruta absoluta al ejecutable). Diferencias clave: **Chrome usa `allowed_origins`** (con `chrome-extension://ID`), **Firefox usa `allowed_extensions`** (con el add-on ID); la ubicación del manifest difiere por SO. Las llamadas a `chrome.runtime.connectNative()`/`sendNativeMessage()` solo funcionan desde el service worker/páginas de la extensión, no desde content scripts. Apropiado si querés que la extensión "abra la app" sin que el usuario arranque un server a mano, pero agrega complejidad de instalación (el host nativo se instala con las facilities del SO, no por la store).

**(c) Extracción nativa en la extensión leyendo la sesión logueada (menor fricción, frágil).** Un content script en `world: "MAIN"` (Chrome 95+ y Firefox 128+ lo soportan en `content_scripts`) parchea `window.fetch` y `XMLHttpRequest` para interceptar las respuestas GraphQL que x.com ya descarga mientras el usuario navega un perfil (operaciones `UserTweets`/`UserTweetsAndReplies`, confirmadas en uso en 2025-2026), y reenvía el JSON capturado a un segundo content script en el mundo ISOLATED vía `window.postMessage`, que a su vez llama `chrome.runtime.sendMessage`. Esquema de patch (`run_at: "document_start"`):
```js
const OPS = /\/(UserTweets|UserTweetsAndReplies)(\?|$)/;
const origFetch = window.fetch;
window.fetch = async function (...args) {
  const res = await origFetch.apply(this, args);
  const url = (args[0] && args[0].url) || String(args[0]);
  if (OPS.test(url)) res.clone().json()
    .then(d => window.postMessage({source:"x-tweet-capture", url, data:d}, "*"))
    .catch(()=>{});
  return res;
};
```
**Clave:** matcheá por nombre de operación en la URL (no hardcodees el `queryId`, que rota con cada deploy de x.com); cloná la `Response` para no consumir el body que la página necesita; el mundo MAIN no tiene acceso a `chrome.*` (de ahí el bridge de dos scripts); ni `webRequest` ni `declarativeNetRequest` pueden leer response bodies, por eso el monkey-patch es obligatorio. Ventaja: usa la sesión y la IP del propio usuario (mínimo riesgo de detección). Desventaja: depende de que el usuario scrollee, el formato GraphQL puede cambiar, y no hay control fino del rango de fechas sin scroll dirigido. **Nota de cumplimiento:** este patrón también debe reportar sus accesos al *Compliance Gate* (vía `chrome.storage` o el backend) para mantener la invariante de 1M/24h global.

**manifest.json Chrome (MV3) vs Firefox:** Chrome MV3 usa `background.service_worker`; Firefox usa `background.scripts` (event pages no-persistentes). Firefox soporta `browser.*` nativo con Promises; Chrome usa `chrome.*` (con Promises desde Chrome 121). Solución cross-browser: **`webextension-polyfill`** (en Firefox es NO-OP, en Chrome envuelve la API con Promises). Compartir un solo código: bundler con campos de manifest prefijados o herramientas como **Extension.js/WXT** que generan `dist/chrome` y `dist/firefox` desde un único manifest, aplicando el polyfill solo en targets Chromium.

**fnm en el toolchain de la extensión:** `fnm` (Fast Node Manager) gestiona la versión de Node para el build JS/TS. Flujo: `fnm use` (lee `.nvmrc`/`.node-version`), luego un bundler (**Vite/esbuild/webpack**) compila TS + el polyfill a los artefactos por navegador. fnm no toca Python; convive limpiamente con uv (uv = Python/backend, fnm = Node/extensión).

**Nginx:** reverse-proxy del FastAPI (`location / { proxy_pass http://127.0.0.1:8000; proxy_set_header Host $http_host; }`), hostname local limpio, manejo de CORS. **Cuidado de no duplicar headers CORS** entre Nginx y el `CORSMiddleware` de FastAPI (causa conflictos); elegí uno solo. Si la extensión hace fetch a la API, configurá `allow_origins` con el origin exacto (no `*` si usás `allow_credentials=True`). TLS para localhost solo si la extensión lo requiere.

### 6. Consideraciones legales/ToS/éticas (factual y neutral)

- Los ToS de X (vigentes 15-ene-2026; la cláusula se origina en la versión del 15-nov-2024) prohíben crawling/scraping "en cualquier forma, para cualquier propósito" sin permiso escrito, y fijan textualmente: *"you will be jointly and severally liable to us for liquidated damages… for requesting, viewing, or accessing more than 1,000,000 posts… in any 24-hour period – $15,000 USD per 1,000,000 posts"* (€15.000 EUR en la UE).
- Scrapear datos públicos suele considerarse legal en EE.UU. bajo precedentes como **hiQ v. LinkedIn** (CFAA), pero **viola los ToS contractuales** de X, con riesgo de suspensión de cuenta y baneo de IP. La cláusula de daños liquidados fue criticada por el Knight First Amendment Institute (Columbia) como una medida con efecto inhibidor sobre la investigación independiente ("a disturbing move that the company should reverse").
- **Mitigación:** usar una cuenta secundaria dedicada (no la principal), bajo volumen, delays entre requests, no recolectar PII innecesaria, respetar GDPR en retención. Tu caso (1 a pocas cuentas) está muy lejos del umbral del millón de posts/24h, y el *Compliance Gate* lo hace una garantía dura por código. El patrón (c) —leer la sesión propia mientras navegás— es el de menor exposición técnica, aunque sigue sujeto a los ToS.

### 7. Roadmap de implementación por fases

**Fase 1 — MVP CLI con backend de scraping (gratis):**
1. `uv init tweet-extractor && cd tweet-extractor`
2. `uv add twscrape pydantic typer httpx aiosqlite`
3. Configurar cuenta en twscrape (cookies `auth_token`+`ct0`). **Implementar y activar el *Compliance Gate* (ventana deslizante 24h + ledger SQLite + reserva) y envolver el provider con `GatedProvider` ANTES de cualquier corrida real — es la invariante no evitable del tope de 1M/24h.** Incluí tests con el ledger pre-cargado que fuercen el bloqueo y la espera.
4. Implementar `TwscrapeProvider.fetch_tweets()` usando búsqueda `from:user since/until` troceada en sub-ventanas, con granularidad de página y reporte de `accessed_count`.
5. Mapper → modelo `Tweet`; exportar a CSV por cuenta. Detectar quotes (incluir, con `quoted_tweet_url`) y retweets (excluir).
6. `uv run python -m tweet_extractor.cli --account X --since 2023-01-01 --until 2026-01-01`

**Fase 2 — Modularizar + backend oficial stub + SQLite/volumen:**
1. Extraer la interfaz `TweetProvider` y la factory; confirmar que el `GatedProvider` envuelve indistintamente scraping y API oficial.
2. Agregar `OfficialApiProvider` (stub con los endpoints v2 documentados: `/2/users/:id/tweets` con `start_time`/`end_time`).
3. Storage intermedio con dedupe (`INSERT OR IGNORE`) y checkpointing.
4. Async con throttling y backoff (respetando `x-rate-limit-reset`); extracción resumible.

**Fase 3 — Servicio FastAPI local + Nginx:**
1. `uv add fastapi[standard]`
2. Endpoints `POST /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/csv`; exponer también el estado del *Compliance Gate* (uso actual / capacidad restante en la ventana).
3. `uv run fastapi dev` para desarrollo; `uv run fastapi run` para correrlo.
4. Configurar Nginx como reverse-proxy a `127.0.0.1:8000` y CORS (uno solo, no duplicado con FastAPI).

**Fase 4 — Extensiones Firefox + Chrome:**
1. `fnm use` (fijar versión Node) + bundler (Vite) + `webextension-polyfill`.
2. manifest MV3 (Chrome, `service_worker`) + ajustes Firefox (`background.scripts`); usar WXT/Extension.js para un solo código → `dist/chrome` + `dist/firefox`.
3. La extensión abre la pestaña del FastAPI local (patrón a) o usa Native Messaging (patrón b); opcionalmente, captura GraphQL in-page como modo de menor fricción (patrón c), reportando sus accesos al *Compliance Gate*.

---

## Recommendations

1. **Empezá YA con Fase 1 usando twscrape + búsqueda por fechas troceada.** Es el camino de menor costo y mayor probabilidad de funcionar hoy. Benchmark para avanzar: si lográs extraer 3 años de una cuenta de volumen medio (~5-10k tweets) sin baneo, pasá a Fase 2.
2. **Invertí desde el día 1 en la interfaz `TweetProvider` y el modelo `Tweet` de dominio.** Es barato hacerlo temprano y es lo que te da la propiedad de "swappable data source" que pediste. No metas lógica de GraphQL fuera de los mappers.
3. **Usá una cuenta de X secundaria y descartable** con sus cookies para el scraping, nunca tu cuenta personal. Guardá las cookies fuera del repo (keyring del SO o `.env` git-ignored).
4. **Para el navegador, arrancá con el patrón (a)** (FastAPI + Nginx + extensión-launcher): es el menos frágil y reusa tu stack Nginx. Reservá Native Messaging (b) para cuando quieras instalación "un clic", y considerá el patrón (c) solo si el scraping server-side empieza a recibir baneos frecuentes.
5. **Disparadores para migrar a la API oficial:** si tu volumen crece a niveles donde el baneo es constante, o si necesitás cobertura histórica garantizada >3.200 tweets sin huecos, evaluá el pay-per-use ($0,005/lectura). Con el `OfficialApiProvider` ya stubbeado, la migración es cambiar config, no reescribir.
6. **Resolvé las Open Design Questions antes de codear el CSV exporter**, especialmente encoding/delimitador (Excel-es argentino) y timezone, porque cambiarlos después rompe los CSV ya generados.
7. **El *Compliance Gate* es la primera invariante de seguridad, no un agregado opcional.** Codealo y testealo (tests con ledger pre-cargado que fuercen el bloqueo) en la Fase 1, antes del primer scraping real, y hacelo el único camino por el que pasan TODOS los providers. Para tu uso típico (1–5 cuentas, 3 años ≈ decenas de miles de tweets) estás 3–4 órdenes de magnitud por debajo del tope, así que en la práctica casi nunca se va a disparar — pero queda como garantía dura, persistente y no evitable de cumplimiento de los ToS.

## Open Design Questions (a decidir antes de codear)

> **Estado (2026-06-13):** las ODQ del CSV están RESUELTAS — #1 (UTF-8 sin BOM, delimitador `,`, `QUOTE_ALL`; encoding/delim configurables para Excel-AR), #2 (UTC, ISO 8601), #8 (`<account>_<since>_<until>.csv`). #4 (solo URLs), #5 (replies = raíz-de-conversación propia), #6 (quotes 1 nivel), #7 (dedup por `id`) se resolvieron al implementar mapper/storage. Ver CLAUDE.md "Defaults del CSV". El resto sigue abierto.

1. **Encoding/delimitador del CSV:** ¿UTF-8 con BOM (para que Excel-es lo abra bien) o sin BOM? ¿Coma o punto y coma (Excel en config regional Argentina suele esperar `;`)? ¿Quoting de todos los campos (`csv.QUOTE_ALL`) para texto con saltos de línea/comas?
2. **Zona horaria de la columna fecha:** ¿UTC (recomendado, consistente con la API y la deduplicación de X) o America/Argentina/Buenos_Aires? ¿Formato ISO 8601?
3. **¿Qué cuenta como "link a documento"?** ¿Solo `expanded_url` externos, o también links internos a x.com, media, y "cards"? ¿Filtrás dominios (p.ej. excluir `t.co` ya expandido, o links de media)?
4. **¿Descargar la media o solo guardar URLs?** Impacta storage, tiempo y riesgo. Por defecto, solo URLs.
5. **Threads/replies:** ¿Incluir auto-replies (hilos del propio autor)? ¿Replies a terceros? ¿Cómo marcarlos en el CSV? (En búsqueda, `from:user` incluye replies salvo que agregues `-filter:replies`.)
6. **¿La captura de quotes debe recursar?** Si el tweet citado es a su vez un quote, ¿hasta qué profundidad seguís la cadena? (Recomendado: 1 nivel, guardando solo `quoted_tweet_id`/`url`.)
7. **Política de deduplicación:** ¿Por `id` de tweet? ¿Re-correr sobreescribe el CSV o hace append incremental contra el SQLite existente? (Recordá: la dedupe NO aplica al contador del Compliance Gate.)
8. **Convención de nombres de archivo:** ¿`{cuenta}_{since}_{until}.csv`? ¿Agregar timestamp de extracción para versionar?
9. **Almacenamiento seguro de cookies de sesión:** ¿Variables de entorno, keyring del SO, archivo cifrado? Nunca en el repo. ¿Una cuenta o un pool?
10. **Cuentas borradas/protegidas/suspendidas:** ¿Cómo manejar y reportar el error sin abortar todo el job?
11. **¿Cuántas cuentas en paralelo?** El requerimiento dice "nunca docenas"; definí el máximo concreto (p.ej. ≤5) para dimensionar el rate-limiting.
12. **Manejo de tweets editados** (la v2 devuelve `edit_history_tweet_ids`) y **note tweets** (texto largo >280, en `note_tweet.note_tweet_results.result.text`): ¿guardás la última versión, el texto largo completo?
13. **Margen del tope de 1M (Compliance Gate) — RESUELTO:** `hard_cap` = **900.000**; ledger **global** (por cliente, no por cuenta de X); persistido en un **SQLite de auditoría separado** del de datos. La cota superior de reserva ≈2× por página sigue actuando como backstop adicional ante conteo difuso.

## Caveats

- **Los `queryId` de GraphQL rotan con cada deploy de x.com** (cada 2-4 semanas según ScrapFly); por eso conviene matchear por nombre de operación y usar librerías que se auto-actualicen (twscrape, o el self-heal de Scweet). Esperá roturas periódicas con cualquier enfoque de scraping.
- **Muchas fuentes de precios/estado son blogs de vendors** (ScrapFly, Sorsa, Blotato, etc.) con interés comercial; las cifras de la X API están corroboradas contra múltiples fuentes y el anuncio oficial de pay-per-use (6-feb-2026), pero los detalles de Enterprise son sales-driven y no públicos (cotizaciones de $42K–250K/mes son reportes de terceros, no precio de lista).
- **El tope de ~3.200 tweets del timeline de usuario es de backend y no se saltea**; la única vía a costo cero para ir más atrás es la búsqueda troceada por fechas, que a su vez puede tener huecos por el ranking de relevancia en contenido viejo.
- **El patrón (c) de intercepción in-page depende de detalles internos no documentados** y puede romperse sin aviso; trátalo como "best effort", no como fuente confiable.
- **El *Compliance Gate* asume que tu reloj de sistema es confiable** (usa `time.time()` para la ventana). Si te preocupa la manipulación de reloj, anclá los timestamps a una fuente monotónica/UTC verificada. La "cota superior" de la reserva (≈2×) cubre quotes embebidos de un nivel; si una respuesta llegara a anidar más niveles, el margen `hard_cap < 1M` es el backstop.
- **`webextension-polyfill` con Manifest V3 tiene fricciones conocidas** en algunos setups (issues abiertos en wxt y en el propio repo); validá tu toolchain temprano. Firefox históricamente fue más lento en soportar MV3 completo, aunque en 2026 la convergencia es alta.
- Nada de esto es asesoramiento legal; el scraping viola los ToS de X aunque los datos sean públicos. Para uso comercial o de alto riesgo, consultá un abogado.

