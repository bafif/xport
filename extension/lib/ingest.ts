// Qué hace el background con cada respuesta de POST /ingest. Puro y testeable.
//
// Regla 1 de docs/PLAN.md: la captura no puede seguir accediendo a tweets que el ledger
// del gate no llega a contar. Por eso el crawl se corta cuando el backend avisa el tope
// (429 u `over_cap`), cuando rechaza un lote, o cuando no responde (o falla con 5xx) y
// ya hay `STOP_AFTER_PENDING` páginas sin registrar. Lo que falló por el servicio se
// re-encola; un 4xx no se reintenta (con 429 el backend ya registró el acceso).

// Páginas por POST: cada página de SearchTimeline pesa cientos de KB y nginx acepta
// 32 MB en /ingest; un lote entero tras un corte largo daba 413 y se perdía.
export const MAX_PAGES_PER_POST = 20;

// Páginas pendientes (servicio caído) a partir de las cuales se corta el crawl: unos
// 400 tweets vistos y todavía sin contar en el gate.
export const STOP_AFTER_PENDING = 20;

export type StopReason = 'cap' | 'offline' | 'rejected';

export interface IngestDecision {
  requeue: boolean; // volver a mandar el lote más tarde
  stop: StopReason | null; // cortar el crawl, y por qué
}

/** `status`: el código HTTP, o 0 si no hubo respuesta (servicio caído). `overCap`: lo
 *  que dijo el backend en una respuesta OK. `pending`: páginas de la cuenta que
 *  quedarían sin registrar si el lote se re-encola. */
export function decideIngest(status: number, overCap: boolean, pending: number): IngestDecision {
  if (status >= 200 && status < 300) return { requeue: false, stop: overCap ? 'cap' : null };
  if (status === 0 || status >= 500) {
    return { requeue: true, stop: pending >= STOP_AFTER_PENDING ? 'offline' : null };
  }
  return { requeue: false, stop: status === 429 ? 'cap' : 'rejected' };
}

/** El aviso que el popup muestra al reabrirse, según por qué se cortó el crawl. */
export function stopNotice(reason: StopReason, base: string, status: number): string {
  switch (reason) {
    case 'cap':
      return (
        'Se llenó el tope de 24 h del gate: corté la captura. Vas a poder seguir cuando ' +
        'salgan de la ventana los accesos más viejos (el popup muestra cuántos quedan).'
      );
    case 'offline':
      return (
        `El servicio local no responde en ${base}: corté la captura para no ver tweets ` +
        'que el tope de 24 h no puede contar. Levantá el servicio y volvé a capturar.'
      );
    case 'rejected':
      return `El servicio rechazó la captura (${status}): la corté. Mirá el log del servicio.`;
  }
}
