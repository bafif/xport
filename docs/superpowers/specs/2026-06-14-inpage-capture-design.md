# Spec — Captura GraphQL in-page (patrón C): backend de ingesta desde la extensión

> Estado: **diseño** (2026-06-14). Decidido tras la investigación A vs C (ver `docs/ESTADO.md`):
> el scraping server-side vía twscrape quedó bloqueado porque x.com migró su web client a
> Vite y rompió el cálculo del `x-client-transaction-id`. C esquiva eso de raíz: **el
> navegador del usuario calcula el transaction-id**; nosotros solo observamos lo que ya pide.

---

## 1. Objetivo

Obtener tweets de una/varias cuentas por rango de fechas **sin que el backend pegue a
x.com**. Un content script de la extensión intercepta las respuestas GraphQL que x.com ya
descarga mientras el usuario navega una **búsqueda** `from:user since: until:`, y las
reenvía al FastAPI local, que las normaliza y persiste reusando el pipeline existente
(`mappers/` + `storage/`) y las contabiliza en el Compliance Gate. La exportación a CSV se
hace a pedido, reusando `export_account`.

## 2. Restricción vinculante: por qué este patrón

El acceso a los posts lo hace **el navegador del usuario**, con su sesión y su IP, calculando
el anti-bot nativamente. Tres consecuencias que ordenan todo el diseño:

1. **No reservamos antes de pedir**: el acceso ya ocurrió cuando lo capturamos. El gate pasa
   de *reservar-antes* (scraping activo) a *registrar-después* (captura pasiva). Ver §5.
2. **No parseamos anti-bot ni rotamos queryId**: inmune a la migración Vite y a la rotación
   de doc_id cada 2-4 semanas que rompe al scraping reverse-engineered.
3. **El flujo se invierte**: el backend pasa de *fetchear* a *recibir*. No usa `providers/`
   ni `orchestrator`; reusa `mappers/`, `domain/`, gate y `storage/`.

## 3. Decisiones de diseño

### D1 — La extensión NO parsea GraphQL; manda JSON crudo + nombre de operación
El content script captura `{op, url, json}` y lo reenvía tal cual. TODA la lógica de GraphQL
sigue en `mappers/` (regla de CLAUDE.md). La extensión queda tonta y estable.

### D2 — Match por NOMBRE de operación en la URL, nunca por `queryId`
Regex tipo `/(SearchTimeline|UserTweets|UserTweetsAndReplies)(\?|$)/` sobre la URL del
request. El `queryId` rota con cada deploy; el nombre es estable.

### D3 — MVP sobre `SearchTimeline` (rango de fechas vía la búsqueda)
El usuario abre `x.com/search?q=from%3Auser%20since%3A<d>%20until%3A<d>&f=live` y scrollea.
El content script captura las respuestas `SearchTimeline`, cuyo envelope **ya lo maneja
`extract_tweet_results`** (mismo que el provider actual). `UserTweets`/`UserTweetsAndReplies`
(timeline de perfil, otro envelope) quedan para una fase siguiente (ver §10).

### D4 — Bridge de dos mundos (MAIN → ISOLATED → background)
- **MAIN-world** (`world:"MAIN"`, `run_at:"document_start"`): parchea `window.fetch` y
  `XMLHttpRequest`. **Clona la `Response`** (`res.clone().json()`) para no consumir el body
  que la página necesita. `window.postMessage({source:"xport-capture", op, url, data})`.
- **ISOLATED-world**: escucha `window.message`, valida `event.source === window` y el
  `source`, y reenvía por `browser.runtime.sendMessage`.
- **background**: batchea por cuenta y `POST`ea al FastAPI. (MAIN no tiene `chrome.*`/`browser.*`;
  de ahí el bridge obligatorio. `webRequest`/`declarativeNetRequest` no leen response bodies,
  por eso el monkey-patch es la única vía.)

### D5 — Endpoint de ingesta `POST /ingest` en el FastAPI
Recibe `{account, op, pages: [rawJson, ...]}`. Por cada página: `extract_tweet_results` →
`map_tweet(raw, account=...)` (None = RT/tombstone) → `store.save` (dedup por `id`) →
`gate.record(count_accessed(page))`. Reusa el pipeline server-side completo. Idempotente
(re-postear la misma página no duplica filas).

### D6 — Captura ≠ job: sesión de captura + export on-demand
La captura alimenta el store de forma continua (dedup por `id`); `orchestrator.run_job`
(subventanas, fetch loop) **no aplica**. La exportación es un paso aparte, a pedido, con
rango: `POST /export {account, since, until}` → `export_account` → ruta del CSV. Esto
desacopla la captura (continua, dirigida por el browsing) del export (puntual, con rango).
Opcional: un registro liviano de "sesión de captura" en el `JobRegistry` para que el popup
muestre cuántos tweets lleva capturados por cuenta.

### D7 — C reusa capas 2-5; no toca `providers/` ni `orchestrator`
`mappers/` (2), `domain/` (3), gate (4, +`record`), `storage/`+export (5). Superficie nueva:
`service/` (endpoint de ingesta) + `extension/` (content scripts). Los backends activos
(twscrape/oficial) y su `GatedProvider`/factory siguen intactos: C es un camino de ingesta
**paralelo**, no los reemplaza.

### D8 — Seguridad del endpoint local
`/ingest` es localhost y la data se parsea defensivamente (`MapperError` ante shape malo,
absorbido por página). Hardening opcional (ver §10): token compartido que la extensión
configura y manda, para que solo ella postee. CORS no aplica (el POST sale del background,
una extension page con `host_permissions`).

### D9 — Cross-browser vía WXT
`world:"MAIN"` está soportado en Chrome 95+ y Firefox 128+. WXT declara los content scripts
por target y aplica el polyfill en el background. Sin navegador en ninguna imagen Docker (el
browser es del usuario; regla del repo intacta).

## 4. Componentes

```
extension/entrypoints/
  x-capture.main.ts     # MAIN world: patch fetch/XHR, postMessage (D4). Sin chrome.*
  x-capture.bridge.ts   # ISOLATED world: window.message -> runtime.sendMessage (D4)
  background.ts         # + relay: batchea capturas -> POST /ingest (config base en storage)
  popup/                # + toggle "capturar" + contador por cuenta + botón exportar
extension/lib/
  capture.ts           # puro y testeable: matchOp(url) -> op|null (D2); shape del mensaje

src/tweet_extractor/
  service/ingest.py     # POST /ingest, POST /export (router nuevo) + schemas
  compliance/gate.py    # + async def record(n) -> int   (D6/§5)
```

Backend reusa, sin duplicar: `providers/twscrape_provider.extract_tweet_results` y
`count_accessed` (walkers de envelope SearchTimeline — genéricos pese al nombre; evaluar
moverlos a un módulo neutral), `mappers.twscrape_mapper.map_tweet`, `storage.sqlite_store`,
`storage.csv_exporter.export_account`.

## 5. Cumplimiento — contabilidad "record-after" (la decisión sensible, rule #1)

**Cambio respecto del `GatedProvider`:** en el scraping activo el gate **reserva** la cota
antes del fetch y **puede bloquear/esperar** si no entra en el presupuesto. En la captura
in-page eso no aplica: **el acceso ya ocurrió** en el navegador del usuario; no hay nada que
reservar. Entonces:

- Nuevo método `SlidingWindowGate.record(n)`: inserta `(ts, n)` en el ledger **bajo el lock,
  sin chequeo de presupuesto ni espera**. Mantiene el ledger global veraz across backends.
- `/ingest` registra `count_accessed` de cada página capturada. Si **tras** registrar el uso
  supera el `hard_cap`, el endpoint **rechaza ingestas siguientes** (HTTP 429) y la extensión
  avisa "tope global alcanzado". No se puede *des-acceder* lo ya accedido por el browser, pero
  se deja de aceptar/alentar más y el ledger queda verídico.
- **El ledger NO deduplica** (regla #1): cada página capturada cuenta. El dedup es solo del
  store/CSV. **Sobre-contar sigue siendo seguro**; sub-contar no.
- Invariante mantenida: el gate sigue siendo el único registro global del uso 1M/24h, y
  **todo** camino (twscrape, oficial, in-page) lo alimenta. La diferencia es solo *cuándo* y
  *si bloquea*: activo = reserva-antes-y-bloquea; pasivo = registra-después-y-avisa.

> ODQ de cumplimiento (decidir con el usuario): ¿contar TODA captura (conservador, default) o
> deduplicar capturas idénticas (mismo `op`+`cursor`+hash en ventana corta) para no inflar el
> ledger por re-renders desde la cache del browser que no pegaron a la red? Default propuesto:
> contar todo (alineado con "sobre-contar es seguro").

## 6. Flujo end-to-end

1. Usuario activa "capturar" en el popup y abre la búsqueda `from:user since: until:` en x.com.
2. Scrollea; x.com pide `SearchTimeline` con un transaction-id válido (lo calcula el browser).
3. MAIN patch clona cada respuesta `SearchTimeline` → postMessage → bridge → background.
4. Background batchea y `POST /ingest {account, op:"SearchTimeline", pages:[...]}`.
5. Backend: extract → map (RT fuera, quotes sí) → `store.save` (dedup) → `gate.record`.
6. Cuando el usuario terminó, pide export → `POST /export {account, since, until}` →
   `export_account` aplica la política de replies y escribe `<account>_<since>_<until>.csv`.

## 7. Manejo de errores

- Página con shape inesperado → `extract_tweet_results` no encuentra entries → 0 tweets (no es
  error). Tweet individual malformado → `MapperError` capturado por-tweet, se saltea.
- Servicio caído cuando el background postea → el background **buffer-ea** y reintenta (no se
  pierden capturas mientras el usuario navega).
- Over-cap → 429 desde `/ingest`; la extensión deja de postear y avisa.

## 8. Estrategia de tests (offline)

- **Backend** (`tests/service/test_ingest.py`, TestClient + store/gate tmp): `POST /ingest`
  con un fixture `search_response([...])` (ya existe en `tests/providers/_fixtures.py`) →
  tweets persistidos; **el gate registró el accessed_count** (incl. el RT descartado);
  re-postear NO duplica (dedup por id); over-cap pre-cargado → 429. `POST /export` → CSV con
  la política de replies. Todo sin red.
- **Gate** (`tests/compliance/test_gate.py`): `record(n)` suma al ledger sin reservar ni
  esperar; `usage()` lo refleja; no deduplica.
- **Extensión**: `lib/capture.ts` puro (`matchOp(url)`) unit-testeable; el patch MAIN se
  valida con `tsc` + `wxt build` (la lógica DOM/red no se unitea sin navegador → verificación
  manual, igual que el resto de la extensión).

## 9. Qué se difiere

- Captura de timeline de **perfil** (`UserTweets`/`UserTweetsAndReplies`): otro envelope →
  variante del extractor. MVP = solo `SearchTimeline` (rango por la búsqueda).
- ~~**Auto-scroll dirigido**~~: **IMPLEMENTADO (2026-06-15)** — `autoscroll.content.ts` +
  `lib/autoscroll.ts`, botón en el popup; scrollea hasta el fin de resultados (corte por
  altura estancada) o tope de pasos. El usuario ya no scrollea a mano.
- **Dedup de capturas** por fingerprint (ODQ §5).
- **Token de auth** del `/ingest` (hardening §8 / D8).
- Verificación cargando la extensión en un navegador real (no automatizable acá).

## 10. Verificación viva (cuando se implemente)

1. Activar captura, abrir `from:nasa since:.. until:..`, scrollear: los tweets aparecen en el
   store y el gate sube por `record`.
2. El rango del CSV exportado coincide con lo capturado; replies/quotes/RT según la política.
3. `accessed_count` registrado ≈ objetos que el browser efectivamente trajo (over-count OK).
4. Cortar el servicio a mitad: el background reintenta sin perder capturas.
