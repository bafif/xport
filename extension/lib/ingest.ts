// Qué hace el background con cada respuesta de POST /ingest. Puro y testeable.
//
// Regla 1 de docs/PLAN.md: la captura no puede seguir accediendo a tweets que el ledger
// del gate no llega a contar. Por eso el crawl se corta cuando el backend avisa el tope
// (429 u `over_cap`), cuando rechaza un lote, o cuando no responde (o falla con 5xx) y
// ya hay `STOP_AFTER_PENDING` páginas sin registrar. Lo que falló por el servicio se
// re-encola; un 4xx no se reintenta (con 429 el backend ya registró el acceso).
//
// También se corta cuando el backend avisa `format_changed`: el mapper no entendió más
// de la mitad del lote porque x.com cambió la forma de los datos, y seguir daría un CSV
// vacío sin ningún error.

// Páginas por POST: cada página de SearchTimeline pesa cientos de KB y nginx acepta
// 32 MB en /ingest; un lote entero tras un corte largo daba 413 y se perdía.
export const MAX_PAGES_PER_POST = 20;

// Páginas pendientes (servicio caído) a partir de las cuales se corta el crawl: unos
// 400 tweets vistos y todavía sin contar en el gate.
export const STOP_AFTER_PENDING = 20;

export type StopReason = 'cap' | 'offline' | 'rejected' | 'format';

/** Lo que importa de una respuesta OK de POST /ingest (espejo de `IngestResult`). */
export interface IngestFlags {
  over_cap: boolean;
  format_changed: boolean;
}

export interface IngestDecision {
  requeue: boolean; // volver a mandar el lote más tarde
  stop: StopReason | null; // cortar el crawl, y por qué
}

/** `status`: el código HTTP, o 0 si no hubo respuesta (servicio caído). `reply`: lo que
 *  dijo el backend en una respuesta OK. `pending`: páginas de la cuenta que quedarían sin
 *  registrar si el lote se re-encola. */
export function decideIngest(
  status: number,
  reply: IngestFlags | null,
  pending: number,
): IngestDecision {
  if (status >= 200 && status < 300) {
    // Con el formato cambiado, esperar a que baje el tope no arregla nada: va primero.
    const stop = reply?.format_changed ? 'format' : reply?.over_cap ? 'cap' : null;
    return { requeue: false, stop };
  }
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
    case 'format':
      return (
        'x.com cambió el formato de los datos: hay que actualizar xport. Corté la captura ' +
        'para no seguir con un CSV vacío; el log del servicio tiene un ejemplo.'
      );
  }
}
