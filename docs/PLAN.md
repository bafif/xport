# xport: plan y estado del proyecto

[English](PLAN.en.md) · [Volver al README](../README.md)

xport baja los tweets de una o varias cuentas de X entre dos fechas y deja un CSV por cuenta con lo que importa: la
cuenta, la fecha, el texto, los links que el tweet trae y, si cita a otro tweet, a cuál. Los quote tweets entran; los
retweets no.

Este documento tiene las reglas del proyecto, las decisiones con su porqué, qué está hecho y probado, y qué falta. La
instalación y el uso están en el [README](../README.md) y en los documentos de `docs/`; las capas del código y cómo
desarrollar, en [Cómo está hecho](ARCHITECTURE.md).

## Qué sale en el CSV

Las columnas están en el [README](../README.md#qué-trae-el-csv). Lo decidido sobre lo que llevan:

- `content` tiene el texto completo, también el de los tweets largos, y `links` las URLs externas (`expanded_url`).
- Si un tweet se editó y se vuelve a capturar, queda la última versión.
- De las respuestas quedan los hilos propios de la cuenta, y quedan afuera las que la cuenta escribió dentro de
  conversaciones ajenas. El export avisa cuántas quedaron afuera, para que la diferencia entre lo capturado y lo
  exportado no parezca una pérdida de datos.
- Los defaults del archivo están cerrados: UTF-8 sin BOM, coma como separador, todos los campos entre comillas (el
  texto trae saltos de línea y comas), fechas en UTC y nombre `<account>_<since>_<until>.csv`, así un mismo rango se
  reescribe y rangos distintos conviven. El encoding y el separador se pueden cambiar para abrirlo en un Excel
  configurado en castellano (`utf-8-sig` y `;`).
- La media se guarda solo como URL, no se descarga, y de las citas se guarda un solo nivel.

## El contexto

- Leer X sin pagar quiere decir usar los mismos endpoints GraphQL que usa la web, con una sesión iniciada. La API
  oficial cobra por cada post leído.
- El timeline de un usuario se corta en unos 3.200 tweets. La búsqueda `from:usuario` no tiene ese techo.
- Los términos de X fijan una multa de 15.000 dólares por cada millón de posts accedidos en cualquier período de 24
  horas. Por eso lo primero es que sea imposible, por código, pasar ese tope.

## Reglas que no se negocian

Varias partes del código remiten a estas reglas por número.

1. **El tope de accesos.** Ningún camino de la app puede acceder a más de 900.000 posts en 24 horas.
   - El tope es 900.000 y no un millón para dejar margen ante cualquier duda sobre qué cuenta como acceso. Es uno solo
     para toda la app, porque el límite de los términos es por cliente y no por cuenta de X. Es una constante del
     código: no se cambia desde el `.env`.
   - La ventana es deslizante: el uso en un momento dado es la suma de todo lo registrado en los últimos 86.400
     segundos. Un contador por día calendario no sirve, porque permitiría casi dos millones entre las 23 y la 1.
   - Se cuentan accesos, no filas guardadas. Suma todo tweet que llega en una respuesta: el que cita, el citado que
     viene embebido y los retweets que después se descartan. El registro nunca deduplica; contar de más es seguro y
     contar de menos no.
   - En el scraping activo se reserva antes de pedir una cota superior de lo que puede traer la página (el tamaño de
     página por tres, por los citados embebidos). Si no entra, espera a que lo viejo salga de la ventana; cuando llega
     la respuesta, se ajusta la reserva al conteo real.
   - El registro vive en un SQLite de auditoría aparte del de los tweets, así sobrevive a que se borre la base de
     datos, y en la carpeta de datos por usuario, la misma para el servicio, la CLI y el native host. Si ya responde
     otro xport, el servicio no arranca. Cada ciclo de leer, decidir y escribir corre bajo un lock y dentro de una
     transacción `BEGIN IMMEDIATE`, así tampoco dos procesos con el mismo registro (la CLI y el servicio) pueden
     colarse entre el chequeo y la escritura.
   - Todo camino de acceso pasa por el mismo registro. Los proveedores del lado del servidor van envueltos en
     `GatedProvider`. La captura del navegador registra después lo que ya accedió, también lo que el servidor rechaza:
     rechazar no borra un acceso. Pasado el tope, el servidor responde 429 y la extensión corta el crawl.
   - La captura no ve lo que el registro no puede contar. La extensión no arranca una captura si el servicio no
     responde o si el tope está lleno, y la corta si el servicio rechaza un lote, avisa el tope o deja de responder con
     páginas sin registrar.
   - Los tests fuerzan las dos cosas que importan: que bloquee cuando no hay lugar y que espere hasta que el evento más
     viejo salga de la ventana.
2. **Secretos fuera del repo.** Las cookies (`auth_token`, `ct0`) y cualquier credencial van en el `.env`, que está
   ignorado. El repo solo tiene `.env.example` sin valores.
3. **Quotes sí, retweets no.** Un tweet con `retweeted_status_result` se descarta (pero su acceso se cuenta).
4. **El GraphQL se interpreta solo en `mappers/`.** Ni los proveedores ni la extensión parsean tweets; los `queryId`
   rotan con cada deploy de x.com, así que se busca la operación por nombre.
5. **Sin navegador en la imagen del servicio.** La imagen es Alpine y Chromium sobre musl no vale la pena. Si alguna vez
   hace falta un proveedor con navegador, va en otra imagen.
6. **CORS en un solo lugar.** Lo maneja FastAPI; nginx no agrega encabezados de CORS.

## Cómo se captura

El scraping del lado del servidor con twscrape está bloqueado: x.com firma cada pedido con un
`x-client-transaction-id` que twscrape ya no sabe calcular. Por eso la captura es en el navegador: la extensión copia
el GraphQL que x.com ya descarga en la búsqueda y lo manda a `POST /ingest`, que reusa `mappers/` y `storage/`. El
navegador calcula la firma solo y la forma de las respuestas es la misma, así que el resto del código sirve para los
dos caminos.

**Por qué un Chrome propio que maneja el servicio.** Con la búsqueda en una pestaña del navegador de todos los días, la
captura depende de que esa pestaña quede a la vista: con la pestaña oculta, el navegador deja de dibujar la página,
frena sus timers y x.com deja de cargar. Una ventana propia sin foco no alcanza, porque Chrome en Windows y macOS marca
como oculta una ventana que otra tapa entera, y «dejá esa ventana a la vista» es una regla técnica que nadie tiene por
qué entender. Así que el servicio arranca un Chrome (o Chromium, o Edge) con un perfil propio de xport, le carga la
extensión y le da el trabajo de la cola; la interfaz es una página local que se abre en el navegador de siempre.

- **X rechaza `HeadlessChrome` con un 403**, así que ese Chrome corre con ventana, pero adentro de cage, una pantalla
  virtual que nadie ve. No se disfraza el navegador. Sin cage (hoy, en Windows y macOS) no se usa, y la captura la hace
  la extensión del navegador de la persona, con la pausa que avisa cuando la pestaña queda oculta.
- **Chrome con marca ignora `--load-extension`**, así que la extensión se carga con `Extensions.loadUnpacked` por
  `--remote-debugging-pipe`, sin guardarla en el perfil, y recibe la marca `xport:runner` con
  `Extensions.setStorageItems`. `POST /captures/next` solo le da trabajo a quien manda `x-xport-runner`; si el runner
  está apagado o se rindió, a cualquiera.
- **Cuando hace falta una persona, el perfil se abre con ventana:** para iniciar sesión en X la primera vez, o cuando X
  cierra la sesión (falta la cookie `auth_token` o una pestaña quedó en el login). La ventana es una ventana común de
  Chrome, sin el pipe: una ventana chica en modo aplicación no deja terminar el inicio de sesión con una cuenta real.
- **Nada de esto choca con las reglas:** no es Selenium ni Playwright, nada entra en la imagen del servicio (regla 5) y
  todo lo que se ve pasa por `/ingest` y el tope (regla 1).

**Los límites de la búsqueda de X.**

- La búsqueda va al segundo y en UTC: `from:<cuenta> since_time:<s> until_time:<s>`. `since:` y `until:` toman días en
  la hora de quien busca y dejan adentro el día de `until:`: con ellos se perderían las primeras horas del día de
  inicio y se pediría un día de más.
- Cada búsqueda se asienta en unos mil resultados. Cuando se asienta antes de `since`, la siguiente arranca en el
  segundo del tweet más viejo cargado, más uno, para no perder otro del mismo segundo. Si una búsqueda no trae nada más
  viejo, el rango terminó.
- X deja hacer 50 búsquedas cada 15 minutos por cuenta. Ante un 429, o con el cupo en cero, la captura queda
  esperando (`waiting`, con `resume_at`) hasta el reset más 15 segundos, con la pestaña fuera de la búsqueda, y sigue
  sola. Con pocas búsquedas en el cupo, reparte las que quedan.
- Las cargas en blanco (la ruedita o «Something went wrong») se reintentan con backoff; no son el fin de los datos.

## Dónde está

Funciona contra x.com, de punta a punta, en Linux: la cola y la página local, la captura con el Chrome propio adentro de
cage, la espera ante el límite de búsquedas, la búsqueda al segundo (un día de 2.830 tweets sale entero), los cortes
(el servicio caído a mitad de una captura, el popup sin servicio y el tope lleno), la pausa con la pestaña oculta, el
aviso de por qué una captura no trajo tweets y el instalador con su primer inicio de sesión. Todo está en castellano y
en inglés.

Lo que no se probó a mano: abrir un CSV con la casilla de Excel en un Excel configurado en castellano, las
notificaciones del sistema, el instalador en Windows y en macOS, y el arranque automático con un navegador de Windows y
el servicio en WSL. Los tests corren sin red, mypy en modo estricto y ruff pasan, y la imagen Alpine, la extensión y el
paquete de los tres sistemas se construyen en CI.

El scraping del lado del servidor con twscrape está implementado y probado sin conexión, pero bloqueado por x.com:
falla cerrado con un error claro. La API oficial está implementada para quien tenga una clave paga, pero nunca se probó
contra la API real.

## Hecho

Lo que está en el código, dónde y hasta dónde está probado. «Real» quiere decir contra x.com, con una cuenta de verdad.

| Qué | Dónde | Probado |
|---|---|---|
| Tope de 900.000 accesos: ventana deslizante, cuenta accesos sin deduplicar, reserva antes de pedir, registro aparte y atómico entre procesos | `compliance/` | tests (bloqueo, espera, `BEGIN IMMEDIATE`); real: cuenta cada post visto |
| Modelo `Tweet` estricto (UTC, ids y URLs limpios, citado completo o nada) | `domain/models.py` | tests |
| Mapper: retweets fuera, quotes en sus tres lugares, texto completo de los largos, links sin destino o propios fuera, link del citado en x.com | `mappers/twscrape_mapper.py` | tests; real |
| Respuestas: hilos propios sí, conversaciones ajenas no, aplicado al exportar y contado en `excluded_replies` | `storage/`, `POST /export` | tests; real |
| Almacenamiento: dedupe por id con la última versión, handles en minúsculas, checkpoint por sub-ventana | `storage/sqlite_store.py` | tests |
| CSV en streaming, atómico, `<account>_<since>_<until>.csv` | `storage/csv_exporter.py` | tests; real |
| Captura in-page: parche de `fetch` y XHR, puente, relay en lotes de 20 páginas | `extension/entrypoints/` | real |
| Crawl: scroll, «Reintentar», recarga con backoff, cota al segundo del tweet más viejo, reanudación desde la base | `autoscroll.content.ts`, `lib/autoscroll.ts`, `lib/queue.ts`, `POST /progress` | vitest y tests; real: un día de 2.830 tweets sale entero |
| Cortes del crawl: sin servicio, tope lleno, 429, `over_cap`, rechazo, veinte páginas sin registrar | `lib/ingest.ts`, `background.ts`, popup | vitest de `decideIngest`; real: el servicio caído, el popup sin servicio y el tope lleno |
| Pausa del crawl con la pestaña oculta, con aviso | `lib/autoscroll.ts` (`decideStep`), `autoscroll.content.ts` | vitest; real en Chrome |
| Espera a X: con un 429 o sin cupo, espera al reset fuera de la búsqueda y sigue sola; con pocas búsquedas, reparte las que quedan | `lib/rate.ts`, `capture-main.content.ts`, `background.ts`, estado `waiting` de la cola | vitest de `decideRate` y `waitStep`; tests; real: tres esperas seguidas |
| Reinicio del navegador a mitad de una captura: retoma desde lo guardado y crawlea una sola pestaña | `background.ts` (`withFrontier`, `claim`), `lib/queue.ts` | vitest; real |
| Cambio de forma de x.com: `rejected` y `format_changed` en `/ingest`, ejemplo en el log, corte del crawl | `service/ingest.py`, `lib/ingest.ts` | tests con una página de forma desconocida; vitest |
| CSV para Excel en castellano desde `/export`, la página y el popup | `service/schemas.py` | tests (BOM y `;`); **sin abrirlo en Excel** |
| Un solo registro del tope: carpeta de datos por usuario, mudanza de `data/`, no arranca si ya corre otro xport | `config.py`, `datadir.py`, `service/app.py` | tests |
| Cola de capturas: una por cuenta, de a una, persistente; la página, el popup y `tweet-extractor capture` encolan | `storage/queue.py`, `service/captures.py`, `lib/queue.ts`, `background.ts` | tests; vitest; real: varias cuentas seguidas, cada una en su CSV |
| Página local en `/`, con chequeo de `Origin` | `service/page/`, `service/app.py` | tests; real en Chrome, en los dos idiomas |
| Export y notificación al terminar | `service/captures.py`, `service/notify.py` | tests con un notificador falso; **notificaciones sin probar a mano** |
| Por qué no hubo tweets: la cuenta no existe, está suspendida, está protegida, bloqueó a la de xport, X no la muestra, o existe y no escribió en esas fechas | `mappers/account_mapper.py`, `lib/account.ts`, `background.ts`, `service/captures.py` | tests con respuestas reales recortadas; vitest; real: cuatro casos |
| El navegador propio del servicio: Chrome con el perfil de xport adentro de cage, la extensión por el pipe, la cola solo para él, la ventana para iniciar sesión, cerrado con la cola vacía | `service/browser.py`, `service/captures.py`, `lib/api.ts`, `background.ts` | tests con un Chrome falso; real en Linux |
| Resumen diario del tope, aparte de la ventana | `compliance/gate.py` | tests |
| Borrar lo capturado de una cuenta sin tocar el tope | `POST /reset`, popup | tests; real |
| Arranque automático por native messaging: Firefox, Chrome, Chrome Beta y Chromium | `deploy/native-host/` | real en Chrome Beta; Windows con WSL sin probar |
| El servicio solo atiende pedidos a `localhost` o `127.0.0.1` | `service/app.py` | tests |
| twscrape del lado del servidor, con sub-ventanas y corte tras tres páginas vacías | `providers/twscrape_provider.py`, `providers/_twscrape_gql.py` | tests sin red; **bloqueado por x.com** |
| API oficial v2 paga, búsqueda de archivo completo | `providers/official_api.py`, `mappers/api_v2_mapper.py` | respuestas fabricadas; **nunca contra la API real** |
| CLI y servicio sobre el mismo orquestador; jobs en memoria | `cli.py`, `orchestrator.py`, `service/` | tests |
| Imagen Alpine con nginx, build de la extensión en Podman, CI | `deploy/`, `.github/workflows/ci.yml` | CI |
| Castellano e inglés en todo: servicio, CLI, página local, extensión, sitio y documentos | `locales/`, `i18n.py`, `extension/locales/`, `lib/i18n.ts`, `docs/en/` | tests (catálogos completos, cada documento con su par); vitest |
| El lanzador `xport`: arranca el servicio si no corre (o usa el que corre), abre la página, lo cierra y dice qué falta | `launcher.py`, `/healthz` | tests; real en Linux |
| El paquete con su propio Python y el instalador: copia, acceso directo, comando, desinstalación que deja los datos | `deploy/bundle/build.py`, `installer.py` | tests; real en Linux; Windows y macOS solo en CI, **sin probar en esos sistemas** |
| Primer arranque: la página ofrece iniciar sesión en X antes de la primera captura | `service/browser.py` (`login_now`), `POST /browser/login`, `service/page/` | tests con un Chrome falso; real en Linux |

## Los puntos del plan

Los números se mantienen porque el código los cita. Cada punto dice qué es y, si falta algo, qué.

### La captura y el registro del tope

1. **Pausar el crawl con la pestaña oculta.** Hecho y probado en Chrome.
2. **Que un cambio de forma de x.com se note.** Hecho. Si algún día cambia el sobre de la respuesta y no el tweet,
   `/ingest` ve cero tweets y no lo distingue de una página vacía: eso no lo cubre.
3. **Excel en castellano.** Hecho. Falta abrir un CSV exportado con la casilla en un Excel configurado en castellano.
4. **Un solo registro del tope.** Hecho: una carpeta de datos por usuario para el servicio, la CLI y el native host, y
   el servicio no arranca si ya responde otro xport.

### Comprobaciones en un navegador

5. **Los cortes en un navegador.** Andan los tres: el servicio caído a mitad de una captura (el crawl corta al juntar
   20 páginas sin registrar y la cola retoma al volver el servicio), el popup sin servicio y el tope lleno. Un borde:
   la extensión manda lo capturado cada segundo y medio; si el navegador se cierra en ese lapso, lo que no mandó se
   pierde y esos accesos no llegan al tope. Son a lo sumo unas páginas.
6. **El navegador sin ventana y sus supuestos.** La búsqueda carga y el scroll trae más en un Chrome manejado por el
   servicio; X rechaza `HeadlessChrome`, así que corre adentro de cage. `x-rate-limit-*` llega en `SearchTimeline` y la
   página lo puede leer. `since_time:` y `until_time:` andan al segundo en «Más reciente». Edge, Windows y macOS no se
   probaron.

### La cola y la página local

7. **Cola de capturas.** Hecha: `POST /captures`, `GET /captures`, `POST /captures/next` y
   `POST /captures/{id}/state`, en `queue.db`, y `tweet-extractor capture`. Con la extensión del navegador de siempre,
   si la persona usa la pestaña de la cola para otra cosa, la captura queda en curso sin avanzar hasta que la cancele.
8. **La página local.** Hecha (`service/page/`, sin build), con el chequeo de `Origin`, las fechas inclusivas con
   atajos, el avance por captura y el tope con su resumen diario.
9. **Avisar cuando termina y exportar solo.** Hecho (`service/notify.py`): en Linux con `notify-send`, donde el clic
   abre el CSV; en Windows con un toast de PowerShell, y en macOS con `osascript`, sin clic. Falta probarlas a mano.
10. **Cuenta inexistente, suspendida o protegida.** Hecho y probado. Una captura que termina sin un solo tweet en el
    rango lleva su pestaña a `x.com/<cuenta>/following`, que pide `UserByScreenName` sin cargar posts;
    `mappers/account_mapper.py` lo interpreta. La protegida que la cuenta de xport sigue y el bloqueo salen de
    respuestas reales tocadas a mano: no se pudieron medir.

### El crawl

11. **Esperar a X en vez de insistir.** Hecho y probado. El parche de `fetch` y XHR pasa aparte el status y los
    `x-rate-limit-*` de cada respuesta de `SearchTimeline` (HTTP, no GraphQL: la regla 4 se mantiene) y `decideRate`
    (`lib/rate.ts`) decide. Espera al reset más 15 s, y como mínimo un minuto después de un 429, por si el reloj local
    está adelantado. Con menos de 10 búsquedas en el cupo, reparte las que quedan hasta el reset.
12. **La búsqueda al segundo.** Hecho y probado: `since_time:`/`until_time:` en segundos UTC, la cota al segundo del
    tweet más viejo más uno, y `/progress` devuelve la hora del más viejo para retomar desde ahí.

### El navegador de xport

13. **El servicio maneja el perfil de xport.** Hecho en Linux (ver «Cómo se captura»). Cada minuto mira la sesión; si
    una pestaña queda en una página de error dos veces seguidas, reinicia el navegador, y a la tercera falla le
    devuelve la cola a la extensión del navegador de siempre. Falta: Windows y macOS, y sacar el native host y el popup
    cuando el instalador cubra los tres sistemas.

### El instalador

14. **Instaladores para Windows, macOS y Linux:** el servicio empaquetado sin uv ni Python, un acceso directo «xport» y
    el primer inicio de sesión guiado desde la página. Hecho en Linux: `deploy/bundle/build.py`, `launcher.py` e
    `installer.py`. Al publicar un release, `.github/workflows/release.yml` arma el paquete de cada sistema y los zip
    de la extensión y los adjunta. Falta: probar el paquete de Windows y el de macOS en esos sistemas (CI los arma y
    les hace la prueba de humo, nada más); firmar el de Windows y el `xport.app`, porque
    SmartScreen y Gatekeeper avisan con uno sin firma; y, en esos dos sistemas, el navegador sin ventana del punto 13.
    Hasta entonces, ahí la captura la hace la extensión del navegador de siempre.
15. **Publicar la extensión sin listar** en la Chrome Web Store (sirve para Chrome y Edge) y firmarla sin listar en AMO
    (`web-ext sign --channel=unlisted`), así ni Chrome pide el modo de desarrollador ni Firefox la borra al cerrarse.

### Lo que depende de otros

16. **Probar la API oficial contra la API real**, con una clave paga: que `tweet.fields` siga andando, que los posts
    largos traigan `note_tweet` y que el rate limit sea el documentado.
17. **Si twscrape se destraba**, comprobar contra datos reales que el filtro de fechas de la búsqueda acota de verdad,
    que el cursor pagina y termina, que la forma de las respuestas coincide con las de los tests y que los accesos por
    página no pasan la reserva. Si la forma real sorprende, revisar que el recorrido del sobre no tome como tweet de
    primer nivel uno anidado dentro de una cita.

### Preguntas decididas

- **Historia del registro:** un resumen por día, aparte de la ventana (punto 8).
- **Días saturados:** no hay: la búsqueda va al segundo y se achica al tweet más viejo cargado (punto 12).
- **Estado de los trabajos del servicio:** `/jobs` no se persiste mientras ningún proveedor del servidor ande; la cola
  sí (punto 7).
- **Qué cuenta como «link a un documento»:** todas las URLs externas. Filtrar después en la planilla es fácil, y filtrar
  antes pierde datos.
- **Cuenta borrada o protegida:** se informa y la cola sigue (punto 10).
- **Cuántas cuentas en paralelo:** una, en fila (punto 7).
- **Un navegador de Windows con el servicio en WSL:** queda como está y sin probar; con el instalador de Windows deja de
  tener sentido.
