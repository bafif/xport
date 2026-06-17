// Contrato del crawl adaptativo (patrón C). El popup abre la búsqueda del rango
// COMPLETO `from:user since: until:`; el content script scrollea hasta el fondo y,
// cuando x.com deja de entregar resultados ANTES de llegar a `since` (el tope de
// ~1000 del search en vivo, o un error de paginación), encoge la cota superior al día
// del tweet más viejo cargado y re-busca desde ahí hacia atrás — repitiendo hasta
// cubrir todo `[since, until)`. Se adapta a la densidad real (sin ventanas fijas) y no
// choca la pared. Distingue "fin de resultados" de "pared/red caída" por si alcanzó
// `since`, y espera/recarga ante carga lenta o vacía.

export const AUTOSCROLL_MSG = 'xport-autoscroll' as const;

export type AutoscrollAction = 'start' | 'stop';

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
export const MAX_BLANK_RELOADS = 3; // recargas si la búsqueda carga en blanco (probable red caída)

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
