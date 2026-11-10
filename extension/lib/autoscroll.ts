// Contrato del crawl adaptativo (patrón C). El background abre la búsqueda del rango
// COMPLETO `from:user since_time: until_time:` (segundos UTC); el content script scrollea
// hasta el fondo y, cuando x.com deja de entregar resultados ANTES de llegar a `since` (el
// tope de ~1000 del search en vivo, o un error de paginación), encoge la cota superior al
// SEGUNDO del tweet más viejo cargado y re-busca desde ahí hacia atrás, repitiendo hasta
// cubrir todo `[since, until)`. Como la cota es al segundo y no al día, un día con más tweets
// de los que muestra una búsqueda sale entero (punto 12 de docs/PLAN.md). Si una búsqueda no
// trae nada más viejo que el tweet que re-incluye, el rango terminó. Espera/recarga ante
// carga lenta o vacía, y ante el rate limit de X espera al reset (lib/rate.ts).

export const AUTOSCROLL_MSG = 'xport-autoscroll' as const;

// `ping`: el background pregunta si la pestaña sigue en una búsqueda de captura, para
// reusarla con la próxima de la cola en vez de abrir otra. `stale`: si quedó en una búsqueda
// vieja de la cuenta del crawl (ver `isStaleSearch`), para llevarla a la actual.
export type AutoscrollAction = 'start' | 'stop' | 'ping' | 'stale';

export interface AutoscrollMsg {
  type: typeof AUTOSCROLL_MSG;
  action: AutoscrollAction;
}

// Cadencia y cortes.
export const SCROLL_DELAY_MS = 1200; // espera por fetch+render entre scrolls
export const MAX_STALE = 4; // scrolls sin crecer (ni cargar ni error) → ventana asentada
export const MAX_STEPS = 1500; // tope duro por ventana: no scrollear para siempre
export const AUTOSTART_SETTLE_MS = 2500; // espera tras (re)cargar la búsqueda antes de scrollear
export const MAX_LOADING_WAITS = 10; // ~12 s tolerando el spinner (conexión lenta) antes de asentar
export const MAX_RETRIES = 3; // clicks al "Reintentar" de x.com ante un error de paginación
export const RETRY_WAIT_MS = 2500; // espera tras click en Reintentar
// Carga EN BLANCO (ni tuits, ni "sin resultados", ni botón de error) sin un 429 de por medio:
// error o red caída. Se recarga INDEFINIDAMENTE con backoff exponencial acotado — no nos
// rendimos solos (solo "Detener" corta): "no carga nada" es justo lo que hay que aguantar, no
// descartar. El rate limit de X no pasa por acá: se espera al reset (lib/rate.ts).
export const BLANK_BACKOFF_BASE_MS = 5_000;
export const BLANK_BACKOFF_MAX_MS = 60_000;

/** Lo que el crawl ve después de cada scroll. `hidden`: la pestaña estuvo oculta en algún
 *  momento del paso (otra pestaña adelante, la ventana minimizada o tapada). */
export interface StepObservation {
  hidden: boolean;
  grew: boolean; // la página creció: x.com trajo más
  loading: boolean; // hay spinner
  retry: boolean; // x.com muestra «Reintentar»
}

export interface StepCounters {
  stale: number; // scrolls seguidos sin crecer
  retries: number; // clicks a «Reintentar»
  loadingWaits: number; // esperas al spinner
}

export type StepAction = 'pause' | 'grew' | 'wait-loading' | 'retry' | 'stale' | 'settled';

export const FRESH_COUNTERS: StepCounters = { stale: 0, retries: 0, loadingWaits: 0 };

/** Qué hacer después de un scroll. Con la pestaña oculta, el navegador deja de dibujarla
 *  y x.com no carga más: un scroll quieto ahí no es la pared de los ~1000 resultados, y
 *  contarlo haría achicar el rango y perder el resto del día sin aviso. Por eso `pause` no
 *  suma scrolls quietos: el crawl espera a que la pestaña vuelva y retoma de cero. Puro y
 *  testeable. */
export function decideStep(
  obs: StepObservation,
  c: StepCounters,
): { action: StepAction; counters: StepCounters } {
  if (obs.hidden) return { action: 'pause', counters: { ...c, stale: 0, loadingWaits: 0 } };
  if (obs.grew) return { action: 'grew', counters: FRESH_COUNTERS };
  if (obs.loading && c.loadingWaits < MAX_LOADING_WAITS) {
    return { action: 'wait-loading', counters: { ...c, loadingWaits: c.loadingWaits + 1 } };
  }
  if (obs.retry && c.retries < MAX_RETRIES) {
    return { action: 'retry', counters: { ...c, retries: c.retries + 1, loadingWaits: 0 } };
  }
  const stale = c.stale + 1;
  return {
    action: stale >= MAX_STALE ? 'settled' : 'stale',
    counters: { ...c, stale, loadingWaits: 0 },
  };
}

/** El aviso que el popup muestra mientras la captura está en pausa. */
export function pausedNotice(account: string): string {
  return (
    `La captura de @${account} está en pausa: su pestaña quedó oculta. Volvé a esa pestaña ` +
    'para que siga.'
  );
}

/** Backoff antes de recargar una búsqueda que cargó en blanco: 5s, 10s, 20s, 40s, …, tope 60s.
 *  Persistente (no se rinde) pero sin martillar. Puro y testeable. */
export function blankBackoff(attempt: number): number {
  return Math.min(BLANK_BACKOFF_BASE_MS * 2 ** Math.max(0, attempt - 1), BLANK_BACKOFF_MAX_MS);
}

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/** Estado del crawl: el rango de la captura (`since` y `until`, días UTC) y la cota
 *  superior `untilTime` (segundos UTC, exclusiva) que se ENCOGE en cada narrow. `attempt`
 *  cuenta recargas en blanco consecutivas para la cota actual (se descarta al narrowar o al
 *  cargar tuits). `waitUntil` (ms): el crawl espera a que X levante el rate limit; mientras,
 *  la pestaña no está en la búsqueda y la retoma el background. */
export interface CrawlState {
  account: string; // handle sin `@`
  since: string; // YYYY-MM-DD, fijo
  until: string; // YYYY-MM-DD, exclusivo, fijo: el fin del rango de la captura
  untilTime?: number; // cota de la búsqueda actual; sin ella, el fin del rango (un crawl viejo)
  attempt?: number;
  captureId?: number; // la captura de la cola que es este crawl (sin cola: un crawl viejo)
  waitUntil?: number;
}

/** Segundos UTC del comienzo de un día `YYYY-MM-DD`. Puro y testeable. */
export function dayStart(d: string): number {
  return Date.parse(`${d}T00:00:00Z`) / 1000;
}

/** La cota inferior de la búsqueda, en segundos UTC: fija, el comienzo de `since`. */
export function sinceTime(state: CrawlState): number {
  return dayStart(state.since);
}

/** La cota superior de la búsqueda actual, en segundos UTC (exclusiva). */
export function untilTime(state: CrawlState): number {
  return state.untilTime ?? dayStart(state.until);
}

/** El estado para la búsqueda siguiente al narrowar: otra cota superior, sin `attempt` ni
 *  espera. */
export function narrowed(state: CrawlState, untilTime: number): CrawlState {
  const next: CrawlState = { ...state, untilTime };
  delete next.attempt;
  delete next.waitUntil;
  return next;
}

/** Valida un `YYYY-MM-DD` real (rechaza fechas que `Date` normalizaría, p.ej.
 *  2026-02-31). Puro y testeable. */
export function isValidDate(s: string): boolean {
  if (!DATE_RE.test(s)) return false;
  const t = Date.parse(`${s}T00:00:00Z`);
  return !Number.isNaN(t) && new Date(t).toISOString().slice(0, 10) === s;
}

/** Nueva cota superior tras asentarse una búsqueda, dado el segundo (UTC) del tweet más
 *  viejo cargado (`oldest`) y la cota actual (`until`, exclusiva). La búsqueda siguiente
 *  llega hasta `oldest + 1`: re-incluye ese tweet (el store deduplica) y no pierde otro del
 *  mismo segundo, sea `until_time:` exclusivo o no. Si eso no baja la cota, la búsqueda no
 *  trajo nada más viejo que el tweet que re-incluía: el rango está terminado y devuelve
 *  `null`. Como la cota baja siempre, el crawl termina. Puro y testeable. */
export function nextBound(oldest: number, until: number): number | null {
  const next = oldest + 1;
  return next < until ? next : null;
}

/** `true` si cargar hasta `oldest` (segundos) ya cubrió la cota inferior `since`: el rango
 *  está agotado, no hay nada más viejo que pedir. Puro y testeable. */
export function reachedSince(oldest: number, since: number): boolean {
  return oldest <= since;
}

/** Segundos UTC de un `datetime` ISO, como el que x.com pone en `<time>`; `null` si no es
 *  una fecha. Puro y testeable. */
export function epochSeconds(iso: string): number | null {
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : Math.floor(ms / 1000);
}

/** «2025-09-30 18:02 UTC», para los avisos. */
export function showSeconds(s: number): string {
  return `${new Date(s * 1000).toISOString().slice(0, 16).replace('T', ' ')} UTC`;
}

/** La query de una búsqueda. `account` ya sin `@`; las cotas en segundos UTC. X toma
 *  `since:`/`until:` como días en la hora de quien busca y deja adentro el de `until:` (lo
 *  midió la Fase 2): `since_time:`/`until_time:` van al segundo y en UTC. */
export function searchQuery(account: string, since: number, until: number): string {
  return `from:${account} since_time:${since} until_time:${until}`;
}

/** URL de la búsqueda en modo "Latest" (cronológico, `f=live`). Puro y testeable. */
export function searchUrl(account: string, since: number, until: number): string {
  return `https://x.com/search?q=${encodeURIComponent(searchQuery(account, since, until))}&f=live`;
}

/** La búsqueda que toca para un estado del crawl. */
export function crawlUrl(state: CrawlState): string {
  return searchUrl(state.account, sinceTime(state), untilTime(state));
}

/** `true` si `url` es una búsqueda de captura de xport (`from:x since_time:… until_time:…`
 *  en «Más reciente»), sea cual sea: así el background sabe que esa pestaña es suya y la
 *  reusa. */
export function isCaptureSearch(url: string): boolean {
  try {
    const u = new URL(url);
    return (
      /(^|\.)(x|twitter)\.com$/.test(u.hostname) &&
      u.pathname === '/search' &&
      /^from:\w+ since_time:\d+ until_time:\d+$/.test(u.searchParams.get('q') ?? '')
    );
  } catch {
    return false;
  }
}

/** `true` si `url` es una búsqueda de la cuenta del crawl que NO es la que toca: la que quedó
 *  de antes de actualizar la extensión (con `since:`/`until:`), o una cota vieja que el
 *  navegador restauró al reabrirse. Ahí el crawl no arranca y la captura quedaría trabada; el
 *  background la lleva a la búsqueda actual. Otra página de x.com (la persona usando esa
 *  pestaña) no cuenta. Puro y testeable. */
export function isStaleSearch(url: string, state: CrawlState): boolean {
  try {
    const u = new URL(url);
    const q = (u.searchParams.get('q') ?? '').toLowerCase();
    return (
      /(^|\.)(x|twitter)\.com$/.test(u.hostname) &&
      u.pathname === '/search' &&
      q.startsWith(`from:${state.account.toLowerCase()} `) &&
      !matchesSearch(url, state)
    );
  } catch {
    return false;
  }
}

/** `true` si `url` es exactamente la búsqueda del estado actual (compara el param `q`
 *  decodificado). Evita que el crawler arranque en una pestaña de x.com cualquiera. */
export function matchesSearch(url: string, state: CrawlState): boolean {
  try {
    return (
      new URL(url).searchParams.get('q') ===
      searchQuery(state.account, sinceTime(state), untilTime(state))
    );
  } catch {
    return false;
  }
}
