# xport: plan y estado del proyecto

Armé xport para bajar los tweets de una o varias cuentas de X entre dos fechas y quedarme con un
CSV por cuenta que tenga solo lo que me interesa: la cuenta, la fecha, el texto, los links que el
tweet trae y, si cita a otro tweet, a cuál. Los quote tweets entran; los retweets no.

Este documento junta en un solo lugar lo que antes estaba repartido entre el plan inicial, una
especificación por cada parte y una nota de estado. Cuenta qué decidí y por qué, en qué orden lo
fui construyendo, en qué punto está y qué me falta. El README tiene el uso y la configuración; acá
va el razonamiento.

## Qué sale en el CSV

| Columna | Qué tiene |
|---|---|
| `account` | el handle de la cuenta, en minúsculas |
| `created_at` | fecha y hora del tweet, ISO 8601 en UTC |
| `content` | el texto completo (en los tweets largos, el texto entero y no el recortado) |
| `links` | las URLs externas del tweet (`expanded_url`), separadas por espacio |
| `quoted_tweet_id`, `quoted_tweet_url` | el tweet citado, si lo hay |

Si un tweet se editó y lo vuelvo a capturar, queda la última versión.

De las respuestas me quedo con los hilos propios de la cuenta y dejo afuera las respuestas que la
cuenta escribió dentro de conversaciones ajenas. El export avisa cuántas quedaron afuera, para que
la diferencia entre lo capturado y lo exportado no parezca una pérdida de datos.

Los defaults del archivo los cerré el 13 de junio y no los pienso volver a discutir: UTF-8 sin BOM,
coma como separador, todos los campos entre comillas (el texto trae saltos de línea y comas), fechas
en UTC y nombre `<account>_<since>_<until>.csv`, así un mismo rango se reescribe y rangos distintos
conviven. El encoding y el separador se pueden cambiar por parámetro para abrirlo en un Excel
configurado en español (`utf-8-sig` y `;`). La media se guarda solo como URL, no se descarga, y de
las citas guardo un solo nivel.

## De dónde partí

Antes de escribir código revisé qué se podía usar en 2026 para leer X sin pagar:

- snscrape anda a medias y sin mantenimiento, y Nitter murió en 2024 cuando X eliminó las cuentas
  de invitado. Lo que sigue vivo (twscrape, twikit, Scweet) usa las cookies de una cuenta logueada
  contra los mismos endpoints GraphQL que usa la web.
- La API oficial no tiene un plan gratis útil: desde febrero de 2026 se paga por lectura, y los
  planes fijos con búsqueda histórica completa están cerrados para cuentas nuevas o cuestan miles
  de dólares por mes.
- El timeline de un usuario se corta en unos 3.200 tweets. La búsqueda `from:usuario since:… until:…`
  no tiene ese techo si el rango se parte en ventanas más chicas.
- Los términos de X fijan una multa de 15.000 dólares por cada millón de
  posts accedidos en cualquier período de 24 horas.

De ahí salió el plan: empezar con twscrape detrás de una interfaz propia, dejar preparado el lugar
para la API oficial, y antes que nada construir algo que haga imposible, por código, pasar el tope
de los términos.

## Reglas que no se negocian

Varias partes del código remiten a estas reglas por número.

1. **El tope de accesos.** Ningún camino de la app puede acceder a más de 900.000 posts en 24 horas.
   - El tope es 900.000 y no un millón para dejar margen ante cualquier duda sobre qué cuenta como
     acceso. Es uno solo para toda la app, porque el límite de los términos es por cliente y no por
     cuenta de X. Es una constante del código: no se cambia desde el `.env`.
   - La ventana es deslizante: el uso en un momento dado es la suma de todo lo registrado en los
     últimos 86.400 segundos. Un contador por día calendario no sirve, porque permitiría casi dos
     millones entre las 23 y la 1.
   - Se cuentan accesos, no filas guardadas. Suma todo tweet que llega en una respuesta: el que cita,
     el citado que viene embebido y los retweets que después descarto. El registro nunca deduplica;
     contar de más es seguro y contar de menos no.
   - En el scraping activo se reserva antes de pedir una cota superior de lo que puede traer la
     página (el tamaño de página por tres, por los citados embebidos). Si no entra, espera a que lo
     viejo salga de la ventana; cuando llega la respuesta, se ajusta la reserva al conteo real.
   - El registro vive en un SQLite de auditoría aparte del de los tweets, así sobrevive a que se borre
     la base de datos. Cada ciclo de leer, decidir y escribir corre bajo un lock y dentro de una
     transacción `BEGIN IMMEDIATE`, así tampoco dos procesos con el mismo registro (la CLI y el
     servicio) pueden colarse entre el chequeo y la escritura.
   - Todo camino de acceso pasa por el mismo registro. Los proveedores del lado del servidor van
     envueltos en `GatedProvider`. La captura del navegador registra después lo que ya accedió,
     también lo que el servidor rechaza: rechazar no borra un acceso. Pasado el tope, el servidor
     responde 429 y la extensión corta el crawl.
   - La captura no ve lo que el registro no puede contar. La extensión no arranca una captura si el
     servicio no responde o si el tope está lleno, y la corta si el servicio rechaza un lote, avisa
     el tope o deja de responder con páginas sin registrar.
   - Los tests fuerzan las dos cosas que importan: que bloquee cuando no hay lugar y que espere hasta
     que el evento más viejo salga de la ventana.
2. **Secretos fuera del repo.** Las cookies (`auth_token`, `ct0`) y cualquier credencial van en el
   `.env`, que está ignorado. El repo solo tiene `.env.example` sin valores.
3. **Quotes sí, retweets no.** Un tweet con `retweeted_status_result` se descarta (pero su acceso se
   cuenta).
4. **El GraphQL se interpreta solo en `mappers/`.** Ni los proveedores ni la extensión parsean
   tweets; los `queryId` rotan con cada deploy de x.com, así que se busca la operación por nombre.
5. **Sin navegador en la imagen del servicio.** La imagen es Alpine y Chromium sobre musl no vale la pena.
   Si alguna vez hace falta un proveedor con navegador, va en otra imagen.
6. **CORS en un solo lugar.** Lo maneja FastAPI; nginx no agrega encabezados de CORS.

## Cómo está armado

Son capas, y cada una solo conoce la de abajo:

```
providers/     de dónde salen los datos, detrás de TweetProvider (twscrape, API oficial)
mappers/       GraphQL crudo → modelo de dominio
domain/        Tweet y TweetLink (pydantic v2)
compliance/    el registro del tope (SlidingWindowGate) y GatedProvider
storage/       SQLite de datos (deduplicado, checkpoints) y export CSV en streaming
service/       FastAPI: jobs, ingesta de la captura, export, estado del tope
cli.py         la línea de comandos (Typer)
extension/     extensión para Chrome y Firefox (WXT, un solo código)
```

`providers/factory.py` es el único lugar donde se elige entre scraping y API oficial (variable
`PROVIDER_BACKEND`), y arma cada proveedor con su mapper. El orquestador (`orchestrator.run_job`)
recorre las sub-ventanas de cada cuenta y lo comparten la CLI y el servicio.

## Cómo lo fui construyendo

**4 de junio: el tope primero.** Armé el proyecto con uv y lo primero que escribí fue el registro del
tope y el contrato de los proveedores, antes de cualquier línea de scraping. Respecto del boceto
inicial cambié tres cosas: la reserva se identifica por un id autoincremental y no por la hora
(dos reservas en el mismo segundo se pisaban), la reserva ocurre de verdad antes del pedido, y el
reloj y la espera se pueden inyectar para probar la ventana sin esperar 24 horas. La espera, además,
corre fuera del lock.

**8 de junio: endurecer y publicar.** Ajusté el cierre de la conexión del registro, preparé el repo
para hacerlo público (README, licencia MIT) y armé el modelo de dominio. El `Tweet` tiene solo las
columnas del CSV más el `id`, que es la clave de la base. Valida la forma de manera estricta (fecha
con zona horaria y pasada a UTC, ids y URLs sin vacíos ni caracteres de control, el id y la URL del
citado juntos o ninguno, campos extra prohibidos), pero deja libre el texto: puede venir vacío y se
guarda tal cual, con sus saltos de línea. Lo revisé dos veces a fondo y de esas revisiones salieron
validaciones más finas, como rechazar fechas que se desbordan al pasarlas a UTC.

**9 de junio: el proveedor de twscrape.** Lo integré a nivel de página y no de tweet, porque el tope
necesita reservar antes de cada pedido. Toda la dependencia de los internos de twscrape quedó en un
solo archivo (`providers/_twscrape_gql.py`), y la frontera quedó clara: el proveedor recorre el
sobre de la respuesta (entradas, cursores, conteo de accesos) y entrega los tweets crudos; el mapper
es el que los interpreta. El rango se parte en sub-ventanas fijas alineadas a días UTC, y la red se
inyecta para que todos los tests corran sin conexión.

**13 de junio: las cuatro fases en código.** Ese día cerré lo que había planeado en cuatro etapas:

- El mapper: descarta retweets y tweets borrados devolviendo `None`, pero lanza un error si la forma
  del GraphQL no es la esperada, así una rotación de x.com se nota como error y no como un CSV vacío.
  Desenvuelve `TweetWithVisibilityResults`, busca el citado en sus tres lugares posibles, se queda
  con el texto completo de los tweets largos y filtra los links sin destino o que apuntan al propio
  tweet.
- La política de respuestas por raíz de conversación: se conservan las respuestas cuya conversación
  empezó en un tweet de la misma cuenta. Se aplica al exportar y sobre toda la colección, no por
  página. Tiene una limitación conocida: si la raíz cae fuera del rango pedido, sus respuestas dentro
  del rango se pierden.
- El almacenamiento: SQLite con `INSERT OR IGNORE` por id (las ventanas pueden solaparse) y un
  checkpoint por sub-ventana, para que un reintento no vuelva a pedir lo que ya terminó (cada pedido
  repetido gasta tope). El CSV se escribe en streaming y de forma atómica: si falla a mitad, queda el
  archivo anterior.
- La CLI y el orquestador. Después, la factory y un proveedor de la API oficial que falla cerrado:
  documenta el pedido a `/2/users/:id/tweets`, pero no implementé el mapeo porque no tengo con qué
  verificarlo contra respuestas reales.
- El servicio FastAPI sobre el mismo orquestador, con el registro del tope y la base como instancias
  únicas, nginx adelante y una imagen Alpine multi-stage con el registro en un volumen aparte.
- La extensión, con WXT, en su primera forma: un popup que le pide trabajos al servicio local.

**14 de junio: twscrape deja de andar y cambio de camino.** Probé por primera vez con cookies reales y
el proveedor falló cerrado, como tenía que fallar. El problema no era de xport: x.com había migrado
su cliente web a Vite y twscrape ya no podía calcular el encabezado `x-client-transaction-id`, sin
el cual X rechaza los pedidos. No había versión nueva que lo arreglara. Evalué cuatro salidas:
esperar un arreglo, parchear el cálculo yo mismo, pasar a la API oficial paga o capturar desde el
navegador el GraphQL que x.com ya descarga. Parchear implicaba reconstruir el algoritmo sobre el
bundle nuevo y volver a hacerlo cada pocas semanas. La captura en el navegador es inmune a eso: el
navegador calcula el encabezado solo, la forma del JSON no cambió y los mappers se reusan tal cual.
Elegí la captura y la implementé ese mismo día:

- La extensión parchea `fetch` y `XMLHttpRequest` en la página, clona las respuestas de
  `SearchTimeline` sin tocar las que usa x.com y las pasa al servicio por `POST /ingest`, junto con
  el nombre de la operación. No interpreta nada.
- El servicio extrae, mapea, guarda deduplicado y registra los accesos con `record`: como el acceso
  ya pasó en el navegador, no hay nada que reservar. Contar todas las capturas, aunque alguna repita
  una página que el navegador sacó de su caché, sigue siendo lo seguro.
- El export pasó a ser a pedido (`POST /export`) y filtra por el rango de fechas.

**15 al 17 de junio: usarla de verdad.** Con el uso real contra x.com aparecieron los problemas que
los tests no podían mostrar, y de ahí salió casi todo lo que la extensión hace hoy:

- La extensión abre la búsqueda sola y la scrollea hasta el fondo. Si carga en blanco reintenta con
  espera creciente, si X muestra "Reintentar" lo aprieta, y si hay un spinner espera.
- La búsqueda en vivo se frena en unos mil resultados. Cuando el scroll se estanca antes de llegar a
  la fecha de inicio, la extensión achica el rango hasta el día del tweet más viejo cargado y busca
  de nuevo, hasta cubrir todo el período. El día se lee de lo que la página ya muestra, sin
  interpretar GraphQL.
- X también frena el endpoint de búsqueda por horas. Para no empezar de cero, `POST /progress` dice
  hasta dónde llegan los datos guardados y la captura sigue desde ahí. Como sale de la base del
  servicio y no del navegador, sobrevive aunque se borre el estado de la extensión.
- Si el día más viejo ya tiene muchísimos tweets (800 o más), seguir incluyéndolo dejaría la captura
  trabada ahí, así que se saltea y se pierde su cola. El umbral es alto a propósito: que no cargue
  nada es un problema de conexión y se reintenta, no se saltea.
- El 16 sumé un host de native messaging para que el navegador levante y baje el servicio solo, sin
  tener que dejarlo corriendo a mano. Supervisa el mismo servicio y usa el mismo registro del tope.

**19 de junio: terminaciones.** `POST /reset` borra lo capturado de una cuenta sin tocar el registro
del tope, la extensión tiene iconos y el instalador para Windows pasó a ASCII puro, con un modo que
mantiene vivo el service worker de Chrome.

**31 de octubre: puesta al día.** nginx aceptaba como mucho 1 MB y devolvía 413 a los lotes grandes
de `/ingest`, que la extensión descartaba sin registrarlos en el tope; ahora acepta lotes grandes.
Sumé el build de la extensión en un contenedor (no hace falta Node en la máquina), el README en inglés y la
integración continua con ruff, mypy, los tests y los dos builds.

**1 de noviembre.** Este documento reemplazó al plan inicial, a las especificaciones de cada parte y
a la nota de estado.

**6 de noviembre: auditoría del plan.** Revisé cada decisión contra el código. Las de fondo se
sostienen: el tope con ventana deslizante que cuenta accesos, la captura en el navegador, los mappers
como único lugar que interpreta GraphQL y los defaults del CSV. Lo que no se sostenía era cómo se
cumplía el tope en la captura del navegador, y lo arreglé junto con otros cuatro problemas:

- En la captura del navegador el tope no frenaba nada. Pasado el tope, `/ingest` respondía 429 sin
  registrar el lote, y la extensión lo descartaba y seguía scrolleando: accesos reales que el registro
  nunca contaba. Peor, si el servicio no respondía, el popup arrancaba la captura igual, y lo que
  juntaba el buffer (hasta mil páginas) se perdía sin contarse. Ahora el servidor registra también lo
  que rechaza, y la extensión no arranca sin servicio o sin lugar en el tope, y corta el crawl ante un
  429, un `over_cap`, cualquier otro rechazo o veinte páginas pendientes sin servicio. Además, los
  lotes van de a veinte páginas: uno solo con todo lo acumulado podía pasar los 32 MB de nginx y
  perderse con un 413.
- El lock del registro era de `asyncio`, o sea, de un solo proceso: la CLI y el servicio sobre el
  mismo registro podían leer el mismo uso y pasar los dos. Ahora el ciclo corre en una transacción
  `BEGIN IMMEDIATE`, con un test que falla sin ella.
- Los handles de X no distinguen mayúsculas, pero el almacenamiento sí: capturar «NASA» un día y
  «nasa» otro partía los datos en dos cuentas, y la reanudación y el export veían una parte. Ahora se
  guardan en minúsculas, y al abrir la base se pasa a minúsculas lo que ya estaba.
- El compose no fijaba `CAPTURES_DIR`, así que el export de capturas escribía dentro de `/app`, que
  es de root, con el servicio corriendo como otro usuario: fallaba, y lo escrito se habría perdido con
  el contenedor. Ahora va al volumen de datos.
- La paginación del lado del servidor solo cortaba si se repetía el cursor. Al final de una búsqueda
  X puede devolver páginas vacías con cursor nuevo, cada una gastando tope; ahora corta tras tres
  vacías seguidas, como twscrape, que corta en la primera.

El README pasó al castellano, con un espejo en inglés (`README.en.md`).

Después de revisar el informe decidí cuatro cosas más, y quedaron hechas el mismo día:

- **Tweets editados:** queda la última versión capturada, no la primera.
- **API oficial:** twscrape queda dormido, pero la API oficial paga pasó a estar implementada para quien
  tenga una clave. Usa la búsqueda de archivo completo (el timeline de usuario no pasa de los ~3.200
  posts más recientes), sin retweets y sin expansions, que X cobra desde el 22 de noviembre de 2026.
  Está armada con la documentación de X y probada con respuestas fabricadas: no la pude probar contra
  la API real porque no tengo una clave paga. Las docs de X hoy se contradicen en los nombres
  (`tweet.fields` en las guías, `post.fields` en las páginas generadas); uso los de siempre.
- **Servicio local:** solo atiende pedidos dirigidos a `localhost` o `127.0.0.1`, contra DNS rebinding.
- **Tests de la extensión:** vitest, con las funciones puras de `extension/lib/`, entre ellas la que
  decide cuándo cortar el crawl. Corren dentro del build de la extensión, así que también en CI.

**6 de noviembre, a la tarde: GitHub.** El repo ya era público. Revisé el historial entero buscando cookies,
tokens y claves, y no había ninguna. Lo único personal era la ruta de mi home en el instalador de Windows, que pasó a
pedirse por parámetro. Sumé imágenes al README (dos diagramas que salen de `docs/img/generar.py` y una captura del
popup real con datos de ejemplo) y una página del proyecto en `docs/index.html`, que GitHub Pages publica desde la
carpeta `docs/` de `main`.

**6 de noviembre: la estética.** Redibujé el logo en vector (`docs/img/logo.svg`, sale de `generar.py`) y de ahí
salen los íconos de la extensión. La página, el popup y los diagramas pasaron al negro de X («Lights out»), con lo
propio sacado del logo: la esquina-flecha azul en lo que sale, el logo en el encabezado, la letra monoespaciada en los
datos y la grilla de un CSV de fondo. La página suma un ejemplo del CSV y una tarjeta para compartir
(`docs/img/social.png`).

**6 de noviembre: Chrome.** El arranque automático no andaba en Chrome Beta ni en Chromium: el instalador escribía
solo en la carpeta de Chrome estable. Ahora escribe en la de cada variante que esté instalada. La extensión de Chrome
tiene un id fijo, que sale de una clave pública en el manifest, así que ya no hay que copiarlo a mano. Y el host
reconoce cuándo lo lanza Chrome y deja el servicio vivo aunque el service worker se duerma; antes eso dependía de una
variable que solo ponía el instalador de Windows.

**6 de noviembre: la primera captura en Chrome Beta.** Anduvo sin errores: el host arrancó el servicio, todos los
lotes entraron y el tope contó cada post visto. En los datos apareció un problema: el link al tweet citado era el
permalink tal cual lo daba X, casi siempre en twitter.com y a veces un t.co, que no lleva al tweet. Ahora es siempre
de x.com, y los ya guardados se corrigen solos al arrancar el servicio.

**6 de noviembre, a la noche: segunda revisión.** Volví a pasar cada decisión pensando en lo cómodo que es usarla y en
si se puede usar sin tener el navegador adelante. Las decisiones de fondo siguen en pie: el tope, la captura en el
navegador, los mappers como único lugar que interpreta GraphQL y los defaults del CSV. Lo que encontré quedó en
«Pendiente», con prioridad y lo que hace falta para cerrarlo. Dos cosas pueden perder datos sin aviso: la captura confía
en que la pestaña esté a la vista, y `/ingest` descarta en silencio los tweets que el mapper no entiende.

**7 de noviembre: decisiones.** Con esa revisión a la vista decidí tres cosas. La captura sin el navegador adelante va
con un Chrome invisible que maneja el servicio, y no con una ventana sin foco. Los cuatro errores que encontró la
revisión se arreglan todos. Y entran todas las mejoras propuestas. En cada decisión mandó el paso que sigue: un
instalador cómodo para Windows, macOS y Linux, y que usarla no pida saber nada técnico. El porqué está en «Capturar sin
el navegador adelante», y el orden de trabajo, en «Pendiente».

## Dónde está hoy

Funciona, y la usé contra x.com real: en junio y el 6 de noviembre en Chrome Beta, ya con los cortes de esa madrugada
en el código. Esa vez anduvo de punta a punta: el host levantó el servicio, los lotes entraron y el tope contó cada post
visto. Lo que no ejercité en un navegador son los cortes mismos: el servicio caído a mitad de una captura, el popup sin
servicio y el tope lleno. Los tests corren sin red, mypy en modo estricto y ruff pasan, y la imagen Alpine y la
extensión se construyen en CI.

El scraping del lado del servidor con twscrape está implementado y probado sin conexión, pero sigue bloqueado por el
cambio de x.com: falla cerrado con un error claro. La API oficial está implementada para quien tenga una clave paga,
pero nunca se probó contra la API real.

## Hecho

Lo que está en el código, dónde y hasta dónde está probado. «Real» quiere decir contra x.com, con una cuenta de verdad.

| Qué | Dónde | Probado |
|---|---|---|
| Tope de 900.000 accesos: ventana deslizante, cuenta accesos sin deduplicar, reserva antes de pedir, registro aparte y atómico entre procesos | `compliance/` | tests (bloqueo, espera, `BEGIN IMMEDIATE`); real: contó cada post visto (6/11) |
| Modelo `Tweet` estricto (UTC, ids y URLs limpios, citado completo o nada) | `domain/models.py` | tests |
| Mapper: retweets fuera, quotes en sus tres lugares, texto completo de los largos, links sin destino o propios fuera, link del citado en x.com | `mappers/twscrape_mapper.py` | tests; real (6/11) |
| Respuestas: hilos propios sí, conversaciones ajenas no, aplicado al exportar y contado en `excluded_replies` | `storage/`, `POST /export` | tests; real |
| Almacenamiento: dedupe por id con la última versión, handles en minúsculas, checkpoint por sub-ventana | `storage/sqlite_store.py` | tests |
| CSV en streaming, atómico, `<account>_<since>_<until>.csv` | `storage/csv_exporter.py` | tests; real |
| Captura in-page: parche de `fetch` y XHR, puente, relay en lotes de 20 páginas | `extension/entrypoints/` | real (junio y 6/11) |
| Crawl adaptativo: scroll, «Reintentar», recarga con backoff, achique del rango, reanudación desde la base, salteo de días saturados | `autoscroll.content.ts`, `POST /progress` | real; funciones puras con vitest |
| Cortes del crawl: sin servicio, tope lleno, 429, `over_cap`, rechazo, veinte páginas sin registrar | `lib/ingest.ts`, `background.ts`, popup | vitest de `decideIngest`; **no ejercitados en un navegador** |
| Borrar lo capturado de una cuenta sin tocar el tope | `POST /reset`, popup | tests; real |
| Arranque automático por native messaging: Firefox, Chrome, Chrome Beta y Chromium, id fijo en Chrome | `deploy/native-host/` | real en Chrome Beta (6/11); Windows con WSL sin probar |
| El servicio solo atiende pedidos a `localhost` o `127.0.0.1` | `service/app.py` | tests |
| twscrape del lado del servidor, con sub-ventanas y corte tras tres páginas vacías | `providers/twscrape_provider.py`, `providers/_twscrape_gql.py` | tests sin red; **bloqueado por x.com** desde el 14/6 |
| API oficial v2 paga, búsqueda de archivo completo | `providers/official_api.py`, `mappers/api_v2_mapper.py` | respuestas fabricadas; **nunca contra la API real** |
| CLI y servicio sobre el mismo orquestador; jobs en memoria | `cli.py`, `orchestrator.py`, `service/` | tests |
| Imagen Alpine con nginx, build de la extensión en Podman, CI | `deploy/`, `.github/workflows/ci.yml` | CI |
| README en castellano e inglés, página de GitHub Pages, logo e íconos generados | `README.md`, `README.en.md`, `docs/` | a ojo |

## Capturar sin el navegador adelante

Hoy la captura necesita la pestaña de la búsqueda a la vista. El crawl (`autoscroll.content.ts`) scrollea con
`window.scrollTo`, espera 1,2 s con `setTimeout` y mira si la página creció; la que pide más tweets es la propia x.com al
ver el scroll. Con la pestaña oculta (otra pestaña adelante o la ventana minimizada) el navegador deja de dibujar la
página, y los eventos de scroll y los observadores que una página usa para cargar más dependen de ese dibujo. Además,
Chrome pasa los timers de una pestaña oculta a uno por segundo y, a los cinco minutos, a uno por minuto. Lo esperable es
que x.com deje de cargar.

Si pasa eso, el crawl no se queda quieto: se equivoca. Cuatro scrolls sin crecer (unos 5 s) le parecen la pared de los
mil resultados, así que achica el rango y recarga. Cada recarga trae una sola página, y en un día con más de una página
`nextUntil` no puede avanzar incluyéndolo, lo excluye y el resto de ese día se pierde sin aviso. Como «Capturar» abre la
búsqueda en una pestaña nueva de la ventana en uso, alcanza con volver a la pestaña de antes para que pase.

### Decisión del 7 de noviembre: un Chrome invisible que maneja el servicio

Había dos caminos: abrir la captura en una ventana propia sin foco, o un navegador aparte que corre sin ventana. La
ventana es lo más fácil de programar, pero no cumple lo que busco. Chrome en Windows y en macOS marca como oculta una
ventana que otra tapa entera, así que con cualquier programa maximizado encima la captura se frena; con el arreglo del
punto 1 se pone en pausa y avisa, pero no avanza. Para alguien que no sabe nada de esto, «dejá esa ventana a la vista»
es una regla técnica que tiene que entender y recordar. El navegador invisible no depende de lo que la persona haga con
sus ventanas y es igual en los tres sistemas. Lo que manda es el paso siguiente: un instalador cómodo para Windows,
macOS y Linux, y que usarla no pida saber nada técnico.

Cómo queda:

- **El servicio maneja el navegador, y no al revés.** Hoy el navegador de todos los días levanta el servicio por native
  messaging. Pasa a ser al revés: el servicio arranca y para un Chrome (o Edge, que viene en todo Windows) con un perfil
  propio de xport, sin ventana mientras captura. Para capturar ya no hace falta el native host, ni los manifests por
  navegador que el instalador tendría que escribir.
- **La extensión vive solo en ese perfil.** Captura y crawlea como hoy, con `/ingest`, el tope y los mappers sin cambios
  de fondo, pero toma el trabajo de la cola del servicio en vez del popup.
- **La interfaz pasa a una página local** que sirve el mismo servicio en `http://localhost:8000/` y se abre en el
  navegador de siempre: una o varias cuentas, las fechas, la cola con su avance, el tope y los CSV para bajar. El popup
  sigue mientras la página no esté y después se va. Como cualquier sitio puede mandarle pedidos a `localhost`, los que
  cambian algo exigen que el `Origin` sea el de la propia página, además del chequeo de `Host` que ya está.
- **Cuando hace falta una persona, el perfil se abre con ventana.** La primera vez, para iniciar sesión en X (conviene
  una cuenta aparte), y cada vez que X pida una verificación o cierre la sesión: el servicio lo detecta, para el
  navegador invisible, abre ese mismo perfil en una ventana chica y la página explica qué hacer. Después vuelve a
  ocultarse.
- **Los datos van a una carpeta fija por usuario:** `~/.local/share/xport` en Linux, `%LOCALAPPDATA%\xport` en Windows y
  `~/Library/Application Support/xport` en macOS, con una variable para cambiarla. Hoy las rutas son relativas a la
  carpeta desde donde se arranca (`data/…`), lo que además parte el registro del tope (punto 4).
- **No choca con las reglas.** No es Selenium ni Playwright ni hay control remoto del navegador: es el Chrome de la
  persona con la extensión, y nada entra en la imagen del servicio (regla 5). Todo lo que se ve pasa por `/ingest` y el
  tope (regla 1).

Antes de construir encima hay que probarlo (punto 6), y si falla ya está decidido qué hacer:

- Que X ande normal con Chrome en `--headless`: que la búsqueda cargue, que el scroll traiga más y que las capturas
  entren. El modo headless se presenta como `HeadlessChrome` y X podría tratarlo distinto. No lo disfrazo de otro
  navegador: si X lo rechaza, en Linux el mismo Chrome corre con ventana dentro de una pantalla virtual (Xvfb), y en
  Windows y macOS queda la ventana propia sin foco, con la pausa que avisa. La arquitectura (el servicio que maneja el
  perfil, la cola y la página local) es la misma en los tres casos; cambia solo cómo se lanza el navegador.
- Que una extensión instalada en el perfil cargue en `--headless`, en Chrome y en Edge.

Lo que esto le deja resuelto al instalador, que es el paso siguiente y no entra en esta implementación:

- Chrome con marca no acepta `--load-extension` desde la versión 137, y cargar una extensión descomprimida pide el modo
  de desarrollador, que no sirve para alguien sin conocimientos técnicos. La extensión se publica sin listar en la Chrome
  Web Store, que sirve para Chrome y para Edge, y en AMO para Firefox. El instalador la deja instalada en el perfil de
  xport o abre su página para un solo clic. Si la Web Store la rechaza, la alternativa es que el instalador baje Chrome
  for Testing, que sí acepta `--load-extension`.
- El servicio se empaqueta para cada sistema sin pedir uv ni Python, con un acceso directo «xport» que lo arranca y abre
  la página.
- Hace falta Chrome o Edge; en Windows, Edge siempre está. Firefox como navegador de captura queda para después: se puede,
  con la extensión firmada y un perfil propio, pero no va primero.

## Pendiente

Todo lo de abajo está decidido desde el 7 de noviembre: los cuatro errores de la revisión se arreglan y entran todas las
mejoras. Va por fases, en este orden, y cada fase entra en una o dos sesiones. Cada punto dice por qué, cómo, cuándo está
listo y cuánto lleva (chico: una tarde; mediano: un par de días).

### Fase 1: los cuatro errores

1. **Pausar el crawl con la pestaña oculta.** Por qué: hoy volver a otra pestaña puede saltear el resto de cada día con
   más de una página (ver arriba). Sigue haciendo falta con el navegador invisible, para el plan B con ventana y para el
   popup mientras exista. Cómo: en `run()`, si `document.hidden`, esperar el `visibilitychange` sin contar scrolls
   quietos, y dejar un aviso («La captura está en pausa: su pestaña quedó oculta») que el popup muestre; el README avisa
   que la captura tiene que quedar a la vista. Listo cuando: test de la decisión y una prueba a mano cambiando de pestaña
   a mitad de captura, con el rango entero en `/progress`. Chico.
2. **Que un cambio de forma de x.com se note.** Por qué: `/ingest` saltea con `except MapperError: continue` cada tweet
   que el mapper no entiende. Si x.com rota la forma, fallan todos, se guarda cero, el crawl sigue y el CSV sale vacío
   sin error, que es lo que el mapper del 13 de junio quería evitar. Cómo: `IngestResult` suma `rejected`; si en un lote
   falla más de la mitad, el servicio deja un ejemplo en el log y la extensión corta el crawl con «x.com cambió el formato
   de los datos: hay que actualizar xport». Listo cuando: test con una página de forma desconocida. Chico.
3. **Excel en castellano.** Por qué: el encoding y el separador se cambian en la CLI y en `/jobs`, pero `/export`, el
   único camino que hoy anda, no los acepta. Cómo: `ExportRequest` suma `encoding` y `delimiter`, y el popup una casilla
   «Para Excel en castellano» (`utf-8-sig` y `;`) que se recuerda; la página local la hereda. Listo cuando: el CSV abre
   bien en un Excel en castellano. Chico.
4. **Un solo registro del tope.** Por qué: a mano y por native messaging se usa `data/audit/ledger.db`, relativo a la
   carpeta desde donde se arranca, así que la CLI corrida desde otra carpeta crea otro registro; y el compose usa un
   volumen de Podman. Lo único que hoy evita dos topes es que el README pida usar siempre la misma forma, y la regla 1
   dice que se garantiza por código. Cómo: los datos y el registro pasan a la carpeta fija por usuario, la misma para el
   servicio, la CLI y el native host; la primera vez se mueve ahí lo que haya en `data/` (se mueve, no se copia, para
   no dejar dos registros); el compose queda para CI y despliegue; y el servicio no arranca si en el otro puerto ya
   responde otro xport. Listo cuando: un test muestra que la CLI corrida desde otra carpeta usa el mismo registro, y
   levantar un segundo servicio da un error claro. Chico.

### Fase 2: lo que falta probar

5. **Los cortes en un navegador.** El camino feliz anduvo el 6 de noviembre; falta bajar el servicio a mitad de una
   captura y ver que el crawl se corte, ver que el popup no arranque sin servicio y llenar el tope con un registro
   precargado para ver el 429. Listo cuando: el resultado queda anotado acá. Chico.
6. **Prueba del navegador invisible y de dos supuestos.** Un perfil propio de Chrome Beta con la extensión y una sesión
   de X, lanzado con `--headless`: ¿carga la búsqueda, el scroll trae más, entran las capturas, queda entero un rango
   conocido? Lo mismo con Edge, y en Windows y macOS cuando haya dónde. Además, a mano en x.com: si `since_time:` y
   `until_time:` andan en «Más reciente» (punto 12) y si la web recibe `x-rate-limit-remaining` y `x-rate-limit-reset`
   en `SearchTimeline` (punto 11). Listo cuando: el resultado queda acá y elige entre headless, Xvfb o la ventana. Chico;
   necesita una sesión de X.

### Fase 3: la cola y la página local

7. **Cola de capturas.** Por qué: hoy cada captura es una sola cuenta y la arranca el popup, y la idea original era una o
   varias. Cómo: `POST /captures` con una o varias cuentas y un rango, y `GET /captures`, guardados en SQLite en la
   carpeta de datos para que sobrevivan a un reinicio. Cada captura tiene un estado: en cola, en curso, en pausa,
   esperando a X, terminada, o cortada con su motivo. La extensión pide la siguiente (`GET /captures/next`, con
   `alarms` para el service worker de MV3), la crawlea e informa el avance. La CLI la llena con `tweet-extractor capture
   --account a,b --since … --until …`. Va de a una, porque X frena la búsqueda por cuenta de X y en paralelo no rinde.
   Reemplaza la idea de persistir `/jobs`. Listo cuando: dos cuentas encoladas salen en dos CSV sin tocar nada en el
   medio. Mediano.
8. **La página local.** Cómo: el servicio la sirve en `/`, con la casilla de Excel del punto 3, el chequeo de `Origin`
   y lo siguiente:
   - **Avance de verdad:** por captura, lo que dice `/progress` («vas por el 10/03/2024, 63 % del rango, 4.210 tweets»),
     su estado y la hora de la última página que entró. Reemplaza al contador «Capturado» del popup, que suma de por
     vida y no coincide con el CSV.
   - **Fechas fáciles:** atajos (último mes, último año, un año entero), «Hasta» vacío quiere decir hoy y la aclaración
     «hasta el 31/12/2025 inclusive»; por dentro sigue siendo exclusivo. La dirección del servicio no se muestra.
   - **El tope:** el uso de las últimas 24 horas y un resumen por día de lo visto antes, que sale de una tabla diaria
     (fecha y cantidad) en la base de auditoría que la poda de la ventana no toca y que no cuenta para el tope.

   Listo cuando: se encola, se sigue y se baja una captura sin abrir el popup. Mediano.
9. **Avisar cuando termina y exportar solo.** Por qué: hoy el crawl termina en silencio y hay que volver a apretar
   «Exportar». Cómo: cuando una captura de la cola termina, el servicio exporta su CSV y manda una notificación del
   sistema con la cantidad de tweets; la manda el servicio y no la extensión, que va a estar en un navegador sin ventana.
   Un corte avisa igual, con su motivo. Hasta que esté la cola, el popup hace lo mismo con `notifications`. Listo
   cuando: una captura termina con todo cerrado y aparece la notificación, que lleva al CSV. Chico.
10. **Cuenta inexistente, suspendida o protegida.** Cómo: primero, si el rango entero termina sin un solo tweet, decirlo
    («No encontré tweets: ¿la cuenta existe, es pública y escribió en esas fechas?»). Después, capturar
    `UserByScreenName` y mapearlo en `mappers/` para distinguir inexistente, suspendida y protegida; esa captura queda
    cortada con el motivo y la cola sigue con la próxima, sin frenar el resto. Listo cuando: cada caso da su mensaje.
    Chico lo primero, mediano lo segundo.

### Fase 4: el crawl

11. **Esperar a X en vez de insistir.** Por qué: cuando X frena la búsqueda por horas, el crawl recarga cada 60 s sin fin,
    y hoy hay que volver más tarde y apretar «Capturar». Cómo: el parche de `fetch` ya ve la respuesta de
    `SearchTimeline`; que pase también el status y los encabezados de rate limit, que no es interpretar GraphQL (la regla
    4 se mantiene). Ante un 429, la captura queda «esperando a X hasta las 18:40» y retoma sola; con
    `x-rate-limit-remaining` bajo, afloja el ritmo antes. Listo cuando: test de la decisión y una captura que pase por un
    429. Mediano.
12. **Partir los días saturados por hora.** Por qué: hoy, de un día con más tweets de los que muestra la búsqueda, se
    pierde la cola sin aviso. Cómo: el crawl achica el límite a la hora del tweet más viejo cargado (el `datetime` que el
    DOM ya muestra) con `until_time:`, en vez de a su día; no queda cola que perder, se solapa menos (menos accesos
    gastados) y sobra el umbral de 800. `/progress` devuelve la hora y no el día. Si la prueba del punto 6 dice que esos
    operadores no andan, el plan B es que el export diga qué días pueden estar incompletos. Listo cuando: un día con más
    de mil tweets sale entero. Mediano.

### Fase 5: el navegador invisible

13. **El servicio maneja el perfil de xport.** Cómo: arranca y para Chrome o Edge con `--user-data-dir=<datos>/browser`,
    sin ventana mientras haya capturas en la cola y con lo que haya elegido el punto 6. Lo abre en una ventana chica y
    sin foco (`windows.create({ focused: false })`) para iniciar sesión, cuando X pide una verificación o cierra la
    sesión (lo nota porque la búsqueda redirige al login o porque no entran páginas), y como plan B donde el modo sin
    ventana no ande. El native host y el popup quedan mientras dure la transición y después se sacan. Listo cuando: una
    captura pedida desde la página termina sin ninguna ventana a la vista, y si X cierra la sesión aparece la ventana
    para iniciarla. Cuando esté, el `CLAUDE.md` del repo y el README pasan a contar esta arquitectura. Mediano a
    grande.

### Fase 6: el instalador (después de esta implementación)

14. **Instaladores para Windows, macOS y Linux**, con lo que deja resuelto la decisión de arriba: el servicio empaquetado
    sin uv ni Python, un acceso directo «xport» y el perfil de xport armado en el primer arranque.
15. **Publicar la extensión sin listar** en la Chrome Web Store (sirve para Chrome y Edge) y firmarla sin listar en AMO
    (`web-ext sign --channel=unlisted`), así ni Chrome pide el modo de desarrollador ni Firefox la borra al cerrarse. Las
    cuentas de las dos tiendas las saco yo.

### Lo que depende de otros

16. **Probar la API oficial contra la API real**, con una clave paga (no tengo una): que `tweet.fields` siga andando,
    que los posts largos traigan `note_tweet` y que el rate limit sea el documentado.
17. **Si twscrape se destraba**, comprobar contra datos reales que el filtro de fechas de la búsqueda acota de verdad,
    que el cursor pagina y termina, que la forma de las respuestas coincide con las de los tests y que los accesos por
    página no pasan la reserva. Si la forma real sorprende, revisar que el recorrido del sobre no tome como tweet de
    primer nivel uno anidado dentro de una cita. Cada tanto, ver si salió una versión que vuelva a calcular el
    `x-client-transaction-id`.

### Preguntas que estaban abiertas, ya decididas

- **Historia del registro:** un resumen por día, aparte de la ventana (punto 8).
- **Días saturados:** se parten por hora (punto 12); el aviso en el export, solo si eso no anda.
- **Estado de los trabajos del servicio:** `/jobs` no se persiste mientras ningún proveedor del servidor ande; la cola
  sí (punto 7).
- **Qué cuenta como «link a un documento»:** todas las URLs externas. Filtrar después en la planilla es fácil, y filtrar
  antes pierde datos.
- **Cuenta borrada o protegida:** se informa y la cola sigue (punto 10).
- **Cuántas cuentas en paralelo:** una, en fila (punto 7).
- **Un navegador de Windows con el servicio en WSL:** queda como está y sin probar. Con un instalador para Windows deja
  de tener sentido.

## Cómo retomarlo en otra máquina

```bash
git clone https://github.com/bafif/xport.git
cd xport
uv sync                  # crea .venv desde uv.lock
cp .env.example .env     # solo para twscrape o la API oficial
uv run pytest -q
```

No viajan por git y se regeneran: el `.env` (con las cookies de una cuenta aparte), el `.venv`
(`uv sync`) y `extension/node_modules`, `extension/dist` y `extension/.wxt` (el build con Podman o
`npm ci && npm run build` dentro de `extension/`).
