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
| `account` | el handle de la cuenta |
| `created_at` | fecha y hora del tweet, ISO 8601 en UTC |
| `content` | el texto completo (en los tweets largos, el texto entero y no el recortado) |
| `links` | las URLs externas del tweet (`expanded_url`), separadas por espacio |
| `quoted_tweet_id`, `quoted_tweet_url` | el tweet citado, si lo hay |

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
     la base de datos, y cada ciclo de leer, decidir y escribir corre bajo un lock.
   - Todo camino de acceso pasa por el mismo registro. Los proveedores del lado del servidor van
     envueltos en `GatedProvider`; la captura del navegador registra después lo que ya accedió y,
     pasado el tope, el servidor rechaza las capturas siguientes con 429.
   - Los tests fuerzan las dos cosas que importan: que bloquee cuando no hay lugar y que espere hasta
     que el evento más viejo salga de la ventana.
2. **Secretos fuera del repo.** Las cookies (`auth_token`, `ct0`) y cualquier credencial van en el
   `.env`, que está ignorado. El repo solo tiene `.env.example` sin valores.
3. **Quotes sí, retweets no.** Un tweet con `retweeted_status_result` se descarta (pero su acceso se
   cuenta).
4. **El GraphQL se interpreta solo en `mappers/`.** Ni los proveedores ni la extensión parsean
   tweets; los `queryId` rotan con cada deploy de x.com, así que se busca la operación por nombre.
5. **Sin navegador en la imagen Docker.** La imagen es Alpine y Chromium sobre musl no vale la pena.
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
Sumé el build de la extensión en Docker (no hace falta Node en la máquina), el README en inglés y la
integración continua con ruff, mypy, los tests y los dos builds.

**1 de noviembre.** Este documento reemplazó al plan inicial, a las especificaciones de cada parte y
a la nota de estado.

## Dónde está hoy

Funciona, y la usé contra x.com real: la captura desde el navegador, con el crawl adaptativo, la reanudación y el
export. Los tests corren sin red, mypy en modo estricto y ruff pasan, y la imagen Alpine y la
extensión se construyen en CI.

El scraping del lado del servidor con twscrape está implementado y probado sin conexión, pero sigue
bloqueado por el cambio de x.com: falla cerrado con un error claro. La API oficial sigue siendo un
esqueleto que falla cerrado.

## Qué falta

1. Una verificación documentada de punta a punta: cargar la extensión, levantar el servicio, capturar
   un rango, ver que suba el tope y bajar el CSV.
2. Probar el native messaging desde un navegador de Windows hacia WSL; el ciclo de vida del host está
   probado, el saludo real con el navegador no.
3. La API oficial, cuando tenga una clave con qué verificar el mapeo.
4. Decidir si el estado de los trabajos del servicio tiene que sobrevivir a un reinicio. Hoy los
   datos sí sobreviven y reenviar un trabajo retoma desde los checkpoints, pero la lista de trabajos
   vive en memoria.
5. Si twscrape se destraba, comprobar contra datos reales que el filtro de fechas de la búsqueda
   acota de verdad, que el cursor pagina y termina, que la forma de las respuestas coincide con las
   de los tests y que los accesos por página no pasan la reserva. Si la forma real sorprende, revisar
   que el recorrido del sobre no tome como tweet de primer nivel uno anidado dentro de una cita.

Preguntas que siguen abiertas: qué cuenta como "link a un documento" (hoy entran todas las URLs
externas), cómo reportar una cuenta borrada o protegida sin cortar el trabajo entero, cuántas
cuentas en paralelo admitir y qué hacer con los tweets editados.

## Cómo retomarlo en otra máquina

```bash
git clone https://github.com/bafif/xport.git
cd xport
uv sync                  # crea .venv desde uv.lock
cp .env.example .env     # solo para twscrape o la API oficial
uv run pytest -q
```

No viajan por git y se regeneran: el `.env` (con las cookies de una cuenta aparte), el `.venv`
(`uv sync`) y `extension/node_modules`, `extension/dist` y `extension/.wxt` (el build de Docker o
`npm ci && npm run build` dentro de `extension/`).
