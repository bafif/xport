// Contrato del crawl adaptativo (patrón C). El popup abre la búsqueda del rango
// COMPLETO `from:user since: until:`; el content script scrollea hasta el fondo y,
// cuando x.com deja de entregar resultados ANTES de llegar a `since` (el tope de
// ~1000 del search en vivo, o un error de paginación), encoge la cota superior al día
// del tweet más viejo cargado y re-busca desde ahí hacia atrás — repitiendo hasta
// cubrir todo `[since, until)`. Se adapta a la densidad real (sin ventanas fijas) y no
// choca la pared. Distingue "fin de resultados" de "pared/red caída" por si alcanzó
// `since`, y espera/recarga ante carga lenta o vacía.

export const AUTOSCROLL_MSG = 'xport-autoscroll' as const;

// `ping`: el background pregunta si la pestaña sigue en una búsqueda de captura, para
// reusarla con la próxima de la cola en vez de abrir otra.
export type AutoscrollAction = 'start' | 'stop' | 'ping';

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
// Carga EN BLANCO (ni tuits, ni "sin resultados", ni botón de error): error/red caída/rate-limit.
// Se recarga INDEFINIDAMENTE con backoff exponencial acotado — no nos rendimos solos (solo
// "Detener" corta): "no carga nada" es justo lo que hay que aguantar, no descartar. Distinto de
// un día saturado (sí carga, los mismos ~1000), que se saltea en la reanudación (ver SATURATED_DAY).
export const BLANK_BACKOFF_BASE_MS = 5_000;
export const BLANK_BACKOFF_MAX_MS = 60_000;

// Día SATURADO: si en el día del tweet más viejo ya hay >= este nº de tuits, ese día llegó al
// tope de x.com (no rinde más). Reanudar re-incluyéndolo dejaría el crawl trabado ahí entre
// sesiones, así que se saltea (se pierde su cola sub-diaria: inevitable a granularidad de día).
// ALTO a propósito (cerca del tope ~1000): cargar pocos/ningún tuit NO es saturación — es
// error/conexión, y eso se reintenta, no se saltea.
export const SATURATED_DAY = 800;

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

const DAY_MS = 86_400_000;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/** Estado del crawl: cota inferior fija `since` y cota superior `until` que se ENCOGE
 *  en cada narrow. `attempt` cuenta recargas en blanco consecutivas para el `until`
 *  actual (se descarta al narrowar o al cargar tuits). */
export interface CrawlState {
  account: string; // handle sin `@`
  since: string; // YYYY-MM-DD, fijo
  until: string; // YYYY-MM-DD, exclusivo, se reduce al narrowar
  attempt?: number;
  captureId?: number; // la captura de la cola que es este crawl (sin cola: un crawl viejo)
}

/** El estado para la búsqueda siguiente al narrowar: otra cota superior, y `attempt` de cero. */
export function narrowed(state: CrawlState, until: string): CrawlState {
  const next: CrawlState = { ...state, until };
  delete next.attempt;
  return next;
}

/** Valida un `YYYY-MM-DD` real (rechaza fechas que `Date` normalizaría, p.ej.
 *  2026-02-31). Puro y testeable. */
export function isValidDate(s: string): boolean {
  if (!DATE_RE.test(s)) return false;
  const t = Date.parse(`${s}T00:00:00Z`);
  return !Number.isNaN(t) && new Date(t).toISOString().slice(0, 10) === s;
}

function iso(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

/** Día siguiente a `d` (YYYY-MM-DD, UTC). Puro y testeable. */
export function dayAfter(d: string): string {
  return iso(Date.parse(`${d}T00:00:00Z`) + DAY_MS);
}

/** Nueva cota superior tras toparse con la pared, dado el día del tweet más viejo
 *  cargado (`oldest`) y la cota actual (`until`). INCLUYE el día `oldest` (solapa ese
 *  día — el store deduplica) para no perder su cola sub-diaria. Si incluirlo no haría
 *  progreso (un único día con >~1000 tuits: `oldest+1 == until`), cae a `oldest`
 *  EXCLUSIVO para garantizar avance (acepta perder la cola de ese día; caso extremo).
 *  Puro y testeable. */
export function nextUntil(oldest: string, until: string): string {
  const inclusive = dayAfter(oldest);
  return inclusive < until ? inclusive : oldest;
}

/** `true` si cargar hasta `oldest` ya cubrió la cota inferior `since`: el rango está
 *  agotado, no hay nada más viejo que pedir dentro de lo pedido. Comparación
 *  lexicográfica de `YYYY-MM-DD` (= cronológica). Puro y testeable. */
export function reachedSince(oldest: string, since: string): boolean {
  return oldest <= since;
}

/** La query `from:user since: until:` de un rango. `account` ya sin `@`. */
export function searchQuery(account: string, since: string, until: string): string {
  return `from:${account} since:${since} until:${until}`;
}

/** URL de la búsqueda en modo "Latest" (cronológico, `f=live`). Puro y testeable. */
export function searchUrl(account: string, since: string, until: string): string {
  return `https://x.com/search?q=${encodeURIComponent(searchQuery(account, since, until))}&f=live`;
}

/** `true` si `url` es una búsqueda de captura de xport (`from:x since:… until:…` en «Más
 *  reciente»), sea cual sea: así el background sabe que esa pestaña es suya y la reusa. */
export function isCaptureSearch(url: string): boolean {
  try {
    const u = new URL(url);
    return (
      /(^|\.)(x|twitter)\.com$/.test(u.hostname) &&
      u.pathname === '/search' &&
      /^from:\w+ since:\d{4}-\d{2}-\d{2} until:\d{4}-\d{2}-\d{2}$/.test(u.searchParams.get('q') ?? '')
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
      new URL(url).searchParams.get('q') === searchQuery(state.account, state.since, state.until)
    );
  } catch {
    return false;
  }
}
