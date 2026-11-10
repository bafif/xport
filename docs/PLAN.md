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
     la base de datos, y en la carpeta de datos por usuario, la misma para el servicio, la CLI y el native
     host. Si ya responde otro xport, el servicio no arranca. Cada ciclo de leer, decidir y escribir corre bajo un lock y dentro de una
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
storage/       SQLite de datos (deduplicado, checkpoints), la cola de capturas y export CSV en streaming
datadir.py     la mudanza de los datos viejos (`data/`) a la carpeta por usuario
service/       FastAPI: jobs, ingesta de la captura, la cola, export, estado del tope, notificaciones
               y la página local (`service/page/`)
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

**7 de noviembre: la Fase 1, los cuatro errores.** Quedaron arreglados en el código y con tests; lo que pide un navegador
o un Excel de verdad sigue abierto en «Pendiente»:

- La captura se pone en pausa con la pestaña oculta. La decisión de cada scroll pasó a una función pura (`decideStep`):
  con la pestaña oculta, o si se ocultó en medio del paso, no cuenta el scroll quieto, así que nunca lo toma por la pared
  de los mil resultados. El crawl espera el `visibilitychange`, sin timers (Chrome los frena en una pestaña oculta), y el
  popup muestra «La captura de @cuenta está en pausa: su pestaña quedó oculta» mientras dure.
- Un cambio de forma de x.com se nota. `/ingest` cuenta los tweets que el mapper no entiende (`rejected`); si son más de
  la mitad del lote, marca `format_changed`, deja en el log un ejemplo recortado del tweet y la extensión corta el crawl
  con «x.com cambió el formato de los datos: hay que actualizar xport». Lo que sí se entiende se guarda y todo se cuenta
  en el tope.
- `/export` acepta `encoding` y `delimiter`, validados como en `/jobs` (un codec que exista, un solo carácter), y el
  popup tiene la casilla «Para Excel en castellano», que se recuerda.
- Un solo registro del tope. Los datos y el registro pasaron a la carpeta fija por usuario (`XPORT_DATA_DIR` la
  cambia, siempre absoluta), y el `.env` se lee del repo aunque la CLI corra desde otra carpeta. Las rutas relativas
  cuelgan de esa carpeta, y las del `.env.example` viejo (`data/…`) apuntan a lo mismo que los valores por defecto. La
  primera vez se muda lo que haya en el `data/` del repo y en el de la carpeta actual, solo si tiene un registro: se
  mueve, no se copia, y si en la carpeta nueva ya hay un registro, los accesos del viejo se le suman y el viejo se
  borra. Las bases de tweets no se mezclan: si ya hay una, la vieja queda donde estaba y se avisa. El servicio, antes de
  abrir nada, busca otro xport en 8000 y 8080 (`/healthz` ahora dice `service: xport`) y, si lo encuentra, no arranca y
  explica por qué. Uvicorn corre el lifespan antes de abrir su puerto, así que lo que responde es siempre otro proceso.
  El compose queda para CI y despliegue.

**10 de noviembre: la Fase 3, la cola y la página local.** Todo con tests; nada pasó todavía por un navegador:

- La cola vive en `queue.db`, en la carpeta de datos, y la escriben el servicio y la CLI, así que lo que lee y decide va
  en `BEGIN IMMEDIATE`. Una captura por cuenta; la misma cuenta y rango en cola no se repite. Una terminada o cortada no
  cambia más, para que un aviso tardío de la extensión no la reviva. `POST /captures/next` devuelve la que está en curso
  o toma la siguiente (por eso es POST y no GET), y con el tope lleno no empieza ninguna (regla 1).
- La extensión mira la cola con `alarms` cada medio minuto, al arrancar y cuando el popup encola. La decisión es una
  función pura (`decideQueue`): sin crawl arranca la que diga el servicio; con crawl sigue mientras sea la que va, y si
  la cancelaron se corta. Desde dónde arranca sale de `/progress`, como hacía el popup (`startPlan`). El content script
  avisa la pausa y el fin, y espera la respuesta antes de borrar el crawl: así la vuelta siguiente no arranca de nuevo
  la que acaba de terminar. Reusa la pestaña de la captura anterior si sigue en una búsqueda de captura y, si se cerró,
  la vuelve a abrir.
- El «Capturar ▶» del popup pasó a encolar: hay un solo camino, y todas las capturas exportan y avisan al terminar.
- Al terminar, el servicio exporta el CSV con el formato de la captura y manda la notificación; un corte avisa con su
  motivo, salvo que lo haya hecho la persona. Sin un solo tweet en el rango no hay CSV, y se dice por qué puede ser.
- La página se sirve en `/`, sin build. Los pedidos que cambian algo con un `Origin` que no es el de la página ni el de
  una extensión reciben 403; sin `Origin` (la CLI, curl) pasan. nginx pasa el `Host` con el puerto, para que la
  comparación ande detrás del compose.
- El tope ganó un resumen por día (`access_daily`, en la base de auditoría) que se escribe en la misma transacción que
  cada acceso, se corrige con cada `reconcile`, no se poda y el tope no lee nunca. Un registro viejo lo arma con lo que
  tenga en la ventana, y la mudanza de un `data/` viejo suma también su historia.
- La CLI tiene dos comandos: `extract` (el de antes, ahora con nombre) y `capture`.

**10 de noviembre, más tarde: la Fase 2, probada contra x.com sin ventana.** Con Chrome Beta 158 en Linux, un perfil
propio en la carpeta de datos de la cuenta que corre la prueba y una cuenta de X aparte. La
extensión se cargó por `--remote-debugging-pipe` y la red se miró por el mismo canal, sin tocar la página. Lo que salió
está en la Fase 2, más abajo. Lo más grave fue un error del crawl: X toma ADENTRO el día de `until:`, así que achicar a
`until:<día más viejo>` no avanzaba cuando ese día era el más viejo, y el crawl recargaba la misma búsqueda sin fin. Lo vi
pasar: gastó las 50 búsquedas de la ventana de X y siguió con un 429 cada cuatro segundos. `nextUntil` ahora baja la
cota siempre (si no baja, pasa al día anterior), y la reanudación de `startPlan` pide hasta el día más viejo, no hasta el
siguiente. El mismo mes que antes quedaba en bucle terminó entero en 11 búsquedas.

**10 de noviembre, a la noche: la Fase 4, el crawl.** En el código y con tests: vitest de `decideRate`, `nextBound`,
`startPlan` y `waitStep`, y pytest del estado `waiting`, de la columna nueva en una cola vieja y de `/progress` con la
hora. Lo que cambia está en los puntos 11 y 12, más abajo. Además:

- El «techo de unos mil resultados por búsqueda» de junio coincide justo con 50 búsquedas de 20 tweets, el cupo de X en
  15 minutos: puede que nunca haya sido un techo de la búsqueda sino el rate limit. El crawl no necesita saberlo: ante un
  429 espera, y si una búsqueda se asienta antes de `since` sin un 429, achica al segundo y sigue.
- La cola suma la columna `resume_at`; una `queue.db` de la Fase 3 la recibe al abrirse. Los `IN (…)` de la cola se arman
  con el largo de las tuplas de estados, que ahora tienen uno más.
- La prueba contra x.com quedó a medias. Para encontrar un día de más de mil tweets sondeé la primera página de varias
  cuentas, un sondeo por cuenta: @AmazonHelp hacía unos 170 tweets por hora a las 14 UTC del lunes 9, @RailwaySeva y
  @JioCare unos 70 de día y 10 de noche (justo mil por día), @ANI unos 600 por día, y los bots que contestan menciones no
  aparecen en la búsqueda. Lo sondeado entra por `/ingest` y mueve el frente de reanudación (el más viejo guardado), así
  que antes de capturar hay que borrar lo de esa cuenta. La captura de @AmazonHelp del 9 no llegó a scrollear: Chrome
  corrió el service worker de la prueba anterior, guardado en el perfil, porque la extensión tenía el mismo id y la misma
  versión del manifest (la búsqueda salió con `since:`/`until:`). Antes de volver a intentarlo, la máquina se quedó sin
  memoria y la prueba se cortó. Para la próxima: subir la versión del manifest de la copia que se prueba.

## Dónde está hoy

Funciona, y la usé contra x.com real: en junio y el 6 de noviembre en Chrome Beta, ya con los cortes de esa madrugada
en el código. Esa vez anduvo de punta a punta: el host levantó el servicio, los lotes entraron y el tope contó cada post
visto. El 10 de noviembre, sin ventana y con la cola, probé los cortes (el servicio caído a mitad de una captura, el
popup sin servicio y el tope lleno) y anduvieron. Lo de la Fase 1 (7 de noviembre) sigue sin prueba a mano: la pausa
con la pestaña oculta, el corte por cambio
de formato y el CSV abierto en un Excel en castellano tienen tests, pero no una prueba a mano. Lo mismo la Fase 3
(10 de noviembre): la cola, la página local y las notificaciones tienen tests y una prueba de humo contra el servicio,
pero no pasaron por un navegador. La Fase 4 (10 de noviembre), la espera a X ante el rate limit y la búsqueda al
segundo, también tiene tests, pero la captura que la iba a probar contra x.com quedó a medias. Los tests corren sin red,
mypy en modo estricto y ruff pasan, y la imagen Alpine y la extensión se construyen en CI.

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
| Crawl adaptativo: scroll, «Reintentar», recarga con backoff, achique del rango al segundo del tweet más viejo, reanudación desde la base | `autoscroll.content.ts`, `POST /progress` | real; funciones puras con vitest |
| Cortes del crawl: sin servicio, tope lleno, 429, `over_cap`, rechazo, veinte páginas sin registrar | `lib/ingest.ts`, `background.ts`, popup | vitest de `decideIngest`; real sin ventana (10/11): el servicio caído, el popup sin servicio y el tope lleno |
| Búsqueda al segundo y en UTC (`since_time:`/`until_time:`): la cota baja al segundo del tweet más viejo y el crawl termina; ni días saturados ni primeras horas perdidas | `lib/autoscroll.ts` (`nextBound`), `lib/queue.ts` (`startPlan`), `POST /progress` | vitest y tests; los operadores, real (10/11); **una captura entera, sin probar** |
| Pausa del crawl con la pestaña oculta, con aviso en el popup | `lib/autoscroll.ts` (`decideStep`), `autoscroll.content.ts` | vitest de `decideStep`; **sin prueba a mano** |
| Espera a X: con un 429 o sin cupo, la captura espera al reset fuera de la búsqueda y sigue sola; con pocas búsquedas, reparte las que quedan | `lib/rate.ts`, `capture-main.content.ts`, `autoscroll.content.ts`, `background.ts`, estado `waiting` de la cola | vitest de `decideRate` y `waitStep`; tests de la cola; **sin probar contra x.com** |
| Cambio de forma de x.com: `rejected` y `format_changed` en `/ingest`, ejemplo en el log, corte del crawl | `service/ingest.py`, `lib/ingest.ts` | tests con una página de forma desconocida; vitest |
| CSV para Excel en castellano desde `/export` y el popup | `service/schemas.py`, popup | tests (BOM y `;`); **sin abrirlo en Excel** |
| Un solo registro del tope: carpeta de datos por usuario, `.env` del repo, mudanza de `data/`, no arranca si ya corre otro xport | `config.py`, `datadir.py`, `service/app.py` | tests (la CLI desde dos carpetas, la mudanza, la suma de dos registros, el segundo servicio) |
| Cola de capturas: una por cuenta, de a una, persistente; la extensión la toma con `alarms`; el popup y `tweet-extractor capture` encolan | `storage/queue.py`, `service/captures.py`, `lib/queue.ts`, `background.ts` | tests (dos cuentas en dos CSV, estados, reinicio); vitest; **no ejercitada en un navegador** |
| Página local en `/`, con chequeo de `Origin` | `service/page/`, `service/app.py` | tests y prueba de humo; **no abierta en un navegador** |
| Export y notificación al terminar; aviso si el rango no tiene tweets | `service/captures.py`, `service/notify.py` | tests con un notificador falso; **notificaciones sin probar a mano** |
| Resumen diario del tope, aparte de la ventana | `compliance/gate.py` | tests (reserva y reconcile, poda, registro viejo, mudanza) |
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

### Fase 1: los cuatro errores (hecha el 7 de noviembre)

Está en el código y con tests (ver «Cómo lo fui construyendo»). Los números siguen, porque el resto del plan los cita.
Falta lo que pide un navegador o un Excel de verdad:

1. **Pausar el crawl con la pestaña oculta.** Falta la prueba a mano: cambiar de pestaña a mitad de una captura, ver
   el aviso en el popup, volver y comprobar en `/progress` que el rango quedó entero.
2. **Que un cambio de forma de x.com se note.** Hecho; el test usa una página de forma desconocida. Si algún día cambia
   el sobre de la respuesta y no el tweet, `/ingest` ve cero tweets y no lo distingue de una página vacía: eso no lo
   cubre.
3. **Excel en castellano.** Falta abrir un CSV exportado con la casilla en un Excel configurado en castellano.
4. **Un solo registro del tope.** Hecho. Un xport de antes de este cambio no dice `service` en `/healthz`, así que un
   servicio viejo que siga corriendo no se detecta: después de actualizar hay que bajarlo (con Chrome, el native host
   lo deja vivo hasta reiniciar).

### Fase 2: lo que falta probar (hecha el 10 de noviembre)

Probada en Linux con Chrome Beta 158, sin ventana, una cuenta de X aparte y el servicio de la Fase 3 (ver «Cómo lo
fui construyendo»). Los números siguen, porque el resto del plan los cita.

5. **Los cortes en un navegador.** Andan los tres:
   - Con el servicio caído a mitad de una captura, el crawl siguió 18 búsquedas más y cortó solo al juntar 20 páginas
     sin registrar. Al volver el servicio, la cola retomó la captura desde lo guardado.
   - El popup sin servicio no arranca y lo explica.
   - Con el registro precargado a 10 accesos del tope, la primera página lo cruzó (quedó en 900.016, lo previsto para
     lo que ya pasó en el navegador), el crawl se cortó con el aviso del tope y la cola no arrancó ninguna otra.

   Un borde: lo que la extensión tiene en memoria sin mandar se pierde si el navegador se cierra en ese segundo y medio,
   y esos accesos no llegan al tope. Son a lo sumo unas páginas; no lo arreglé.
6. **El navegador invisible y los dos supuestos.**
   - **Sin ventana anda.** La búsqueda carga, el scroll trae más y las capturas entran: septiembre de 2025 de @nasa, 54
     tweets, en 11 búsquedas. Con ventana dentro de una pantalla virtual (`cage` sin pantalla real, el plan B de Linux)
     también anda. Queda elegido headless. Edge no estaba instalado; Windows y macOS siguen sin probar.
   - **La extensión no se instala con `--load-extension`:** Chrome 158 lo ignora, como decía la decisión del 7 de
     noviembre. `Extensions.loadUnpacked` por `--remote-debugging-pipe` (con `--enable-unsafe-extension-debugging`) la
     carga para esa ejecución, sin guardarla en el perfil. Es una alternativa a publicar en la Web Store para el
     navegador que maneja el servicio, pero usa el canal de depuración: queda para decidir en el punto 13.
   - **`x-rate-limit-*` llega** en `SearchTimeline`, que es del mismo origen (x.com), así que la página lo puede leer:
     **50 búsquedas cada 15 minutos** por cuenta de X, y al agotarse, 429 con `remaining` en 0 (punto 11).
   - **`since_time:` y `until_time:` andan** en «Más reciente», al segundo y en UTC (punto 12).
   - **`since:` y `until:` no son lo que xport asumía.** X los toma como días en la hora de quien busca (UTC−3 en la
     prueba) y deja adentro el día de `until:`. Medido con @Reuters: `since:2026-11-06` no trae nada antes de las 03:00
     UTC del 6, y `until:2026-11-07` llega hasta las 02:59 UTC del 8. Tres consecuencias: las primeras horas (UTC) del
     día de inicio no se piden nunca y se pierden sin aviso; se pide un día de más arriba, que el export descarta pero
     gasta accesos; y el crawl podía quedar en bucle, ya arreglado (ver «Cómo lo fui construyendo»). Lo primero se
     arregla pasando la búsqueda a `since_time:`/`until_time:` (punto 12).
   - **Queda entero el rango:** el mes de @nasa salió igual las dos veces que se capturó (54 tweets) y llegó al primer
     tweet del rango. No lo comparé tweet por tweet contra una captura con ventana.

### Fase 3: la cola y la página local (hecha el 10 de noviembre)

Está en el código y con tests (ver «Cómo lo fui construyendo»). Los números siguen, porque el resto del plan los cita.
Falta pasarla por un navegador y lo que dice cada punto:

7. **Cola de capturas.** Hecha: `POST /captures`, `GET /captures`, `POST /captures/next` y `POST /captures/{id}/state`,
   en `queue.db`, y `tweet-extractor capture`; el «Capturar ▶» del popup también encola. Falta la prueba a mano: dos
   cuentas encoladas desde la página que salgan en dos CSV sin tocar nada en el medio. Si la pestaña de la cola se
   cierra, el background la vuelve a abrir; si la persona la usa para otra cosa, la captura queda en curso sin avanzar
   hasta que la cancele (lo resuelve el navegador invisible, punto 13). El estado «esperando a X» llegó con el punto 11.
8. **La página local.** Hecha (`service/page/`, sin build), con el chequeo de `Origin`, las fechas inclusivas con
   atajos, el avance por captura y el tope con su resumen diario. Falta abrirla en un navegador de verdad. El contador
   «Capturado» del popup sigue como estaba, hasta que el popup se vaya.
9. **Avisar cuando termina y exportar solo.** Hecho en el servicio (`service/notify.py`): en Linux con `notify-send`,
   donde el clic abre el CSV; en Windows con un toast de PowerShell, y en macOS con `osascript`, sin clic. Ninguna se
   probó a mano, y las de Windows y macOS ni siquiera corrieron.
10. **Cuenta inexistente, suspendida o protegida.** Lo primero está: un rango entero sin tweets termina con «No encontré
    tweets: ¿la cuenta existe, es pública y escribió en esas fechas?». Falta lo segundo: capturar `UserByScreenName`
    (la búsqueda no lo pide; hay que abrir el perfil) y mapearlo en `mappers/` para distinguir los tres casos. Los tests
    necesitan respuestas reales de x.com, así que va junto con la prueba del punto 6. Mediano.

### Fase 4: el crawl (en el código el 10 de noviembre; falta probarla contra x.com)

Está en el código y con tests (ver «Cómo lo fui construyendo»). La captura que la iba a probar contra x.com quedó a
medias, así que siguen abiertos los dos «listo cuando». Los números siguen, porque el resto del plan los cita.

11. **Esperar a X en vez de insistir.** Hecho: el parche de `fetch` y XHR pasa aparte el status y los `x-rate-limit-*`
    de cada respuesta de `SearchTimeline` (HTTP, no GraphQL: la regla 4 se mantiene), y `decideRate` (`lib/rate.ts`)
    decide. Ante un 429, o con el cupo en cero, el crawl guarda hasta dónde llegó y hasta cuándo espera: el reset más
    15 s, y como mínimo un minuto después del 429, por si el reloj local está adelantado. El background saca la pestaña
    de la búsqueda a la página local, para que x.com no siga reintentando, y la vuelve a la búsqueda cuando pasa la hora
    (lo mira en la vuelta de la cola, cada medio minuto). La captura queda «esperando a X hasta las 18:40» (`waiting`,
    con `resume_at`) en la cola, la página y el popup, y no se notifica: sigue sola. Con menos de 10 búsquedas en el
    cupo, el crawl reparte las que quedan hasta el reset, con una espera de hasta dos minutos por respuesta. Falta: una
    captura que pase por un 429.
12. **Partir los días saturados por hora.** Hecho, y al segundo: la búsqueda pasa a `from:<cuenta> since_time:<s>
    until_time:<s>`, en segundos UTC, y cuando se asienta antes de `since` la cota baja al segundo del tweet más viejo
    cargado más uno: lo re-incluye, así no se pierde otro del mismo segundo, sea `until_time:` exclusivo o no. Si una
    búsqueda no trae nada más viejo que ese tweet, el rango terminó (con un «Reintentar» a la vista, en cambio, recarga).
    `/progress` devuelve la hora del más viejo y ya no `oldest_count`, y la reanudación arranca ahí. Se fueron el umbral
    de 800 y el salteo de días, y con ellos las primeras horas (UTC) del día de inicio que se perdían. Falta: un día con
    más de mil tweets que salga entero.

### Fase 5: el navegador invisible

13. **El servicio maneja el perfil de xport.** Cómo: arranca y para Chrome o Edge con `--user-data-dir=<datos>/browser`,
    sin ventana mientras haya capturas en la cola (el punto 6 eligió headless; la extensión se carga por el pipe de
    depuración o se instala desde la Web Store, a decidir). Lo abre en una ventana chica y
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
- **Días saturados:** ya no hay: la búsqueda va al segundo y se achica al tweet más viejo cargado (punto 12).
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

Lo capturado no viaja con el repo: está en la carpeta de datos (`~/.local/share/xport` en Linux). No viajan por git y se regeneran: el `.env` (con las cookies de una cuenta
aparte), el `.venv`
(`uv sync`) y `extension/node_modules`, `extension/dist` y `extension/.wxt` (el build con Podman o
`npm ci && npm run build` dentro de `extension/`).
