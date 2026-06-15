// Contrato del auto-scroll (patrón C). El popup manda start/stop al content script,
// que scrollea la búsqueda hasta el fondo en loop → x.com pide más páginas
// SearchTimeline → capture-main las intercepta → /ingest. Hands-free.

export const AUTOSCROLL_MSG = 'xport-autoscroll' as const;

export type AutoscrollAction = 'start' | 'stop';

export interface AutoscrollMsg {
  type: typeof AUTOSCROLL_MSG;
  action: AutoscrollAction;
}

// Cadencia y cortes del loop.
export const SCROLL_DELAY_MS = 1200; // espera por fetch+render entre scrolls
export const MAX_STALE = 4; // scrolls consecutivos sin crecer la altura → fin de resultados
export const MAX_STEPS = 1000; // tope duro (~20 min): no scrollear para siempre
export const AUTOSTART_SETTLE_MS = 2500; // espera tras abrir la búsqueda antes de scrollear

/** URL de la búsqueda `from:user since: until:` en modo "Latest" (cronológico, `f=live`).
 *  `account` debe venir ya sin `@`. Puro y testeable. */
export function searchUrl(account: string, since: string, until: string): string {
  const query = `from:${account} since:${since} until:${until}`;
  return `https://x.com/search?q=${encodeURIComponent(query)}&f=live`;
}

/** Decide si cortar el loop: se acabaron los resultados (altura estancada) o se llegó
 *  al tope de pasos. Puro y testeable. */
export function shouldStop(stale: number, steps: number): boolean {
  return stale >= MAX_STALE || steps >= MAX_STEPS;
}
