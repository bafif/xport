// El rate limit de la búsqueda de X (punto 11 de docs/PLAN.md). Puro y testeable.
//
// X deja 50 búsquedas cada 15 minutos por cuenta (lo midió la Fase 2): cada página de
// `SearchTimeline` gasta una, y al agotarse responde 429 hasta el reset. El parche de `fetch`
// (capture-main) ya ve esas respuestas; manda aparte su status y los encabezados
// `x-rate-limit-*`, que son HTTP y no GraphQL (la regla 4 se mantiene: la extensión no
// interpreta el tweet). Con eso el crawl decide: seguir, aflojar el ritmo si quedan pocas, o
// esperar al reset en vez de recargar cada minuto y que x.com reintente cada pocos segundos.

import type { Msg } from './i18n';

/** Mensaje del mundo MAIN al ISOLATED con el estado del rate limit, aparte de las capturas. */
export const RATE_SOURCE = 'xport-rate' as const;

export interface RateMsg {
  source: typeof RATE_SOURCE;
  op: string;
  status: number;
  remaining: string | null; // los encabezados tal cual; se parsean del lado ISOLATED
  reset: string | null;
}

/** Lo que se sabe del cupo de búsquedas después de la última respuesta. */
export interface RateInfo {
  status: number; // HTTP de la última respuesta de SearchTimeline
  remaining: number | null; // `x-rate-limit-remaining`
  reset: number | null; // `x-rate-limit-reset`: epoch en segundos
  at: number; // cuándo llegó (ms)
}

// Con menos de estas búsquedas en el cupo, el crawl reparte las que quedan hasta el reset.
export const PACE_BELOW = 10;
// Ninguna pausa de ritmo pasa de esto: con pocas búsquedas y mucho tiempo, mejor seguir
// de a poco que quedarse quieto.
export const MAX_PACE_MS = 120_000;
// Después de un 429 se espera al menos esto, aunque el reset ya parezca pasado (un reloj
// local adelantado haría reintentar en el acto, que es lo que hay que evitar).
export const MIN_WAIT_MS = 60_000;
// Si el 429 no trae `x-rate-limit-reset`, la ventana entera de X.
export const FALLBACK_WAIT_MS = 15 * 60_000;
// Margen sobre el reset antes de volver a buscar.
export const RESUME_MARGIN_MS = 15_000;

export type RateDecision =
  | { kind: 'go' }
  | { kind: 'slow'; delayMs: number }
  | { kind: 'wait'; untilMs: number };

const GO: RateDecision = { kind: 'go' };

function int(raw: string | null): number | null {
  if (raw === null || !/^\d+$/.test(raw.trim())) return null;
  return Number(raw.trim());
}

/** El `RateInfo` de un mensaje del parche. `null` si no es de `SearchTimeline` (los otros
 *  endpoints tienen su propio cupo) o si la forma no es la esperada. */
export function rateFromMsg(msg: Partial<RateMsg> | null, now: number): RateInfo | null {
  if (!msg || msg.source !== RATE_SOURCE || msg.op !== 'SearchTimeline') return null;
  if (typeof msg.status !== 'number') return null;
  return {
    status: msg.status,
    remaining: int(msg.remaining ?? null),
    reset: int(msg.reset ?? null),
    at: now,
  };
}

/** Qué hacer antes de la próxima búsqueda, según lo último que dijo X:
 *  - 429, o el cupo en cero: esperar al reset (con margen), o al menos `MIN_WAIT_MS` desde el
 *    429. Si esa hora ya pasó, el dato es viejo y se sigue.
 *  - Pocas búsquedas en el cupo: repartir las que quedan hasta el reset.
 *  - Si no, seguir. */
export function decideRate(rate: RateInfo | null, now: number): RateDecision {
  if (!rate) return GO;
  const resetMs = rate.reset === null ? null : rate.reset * 1000;
  if (rate.status === 429 || rate.remaining === 0) {
    let untilMs: number;
    if (rate.status === 429) {
      const floor = rate.at + MIN_WAIT_MS;
      untilMs = resetMs === null ? rate.at + FALLBACK_WAIT_MS : Math.max(resetMs, floor);
    } else {
      if (resetMs === null) return GO; // sin reset no hay a qué esperar: el 429 lo dirá
      untilMs = resetMs;
    }
    untilMs += RESUME_MARGIN_MS;
    return untilMs > now ? { kind: 'wait', untilMs } : GO;
  }
  if (rate.remaining !== null && rate.remaining < PACE_BELOW && resetMs !== null && resetMs > now) {
    const delayMs = Math.min(MAX_PACE_MS, Math.ceil((resetMs - now) / (rate.remaining + 1)));
    return { kind: 'slow', delayMs };
  }
  return GO;
}

/** El aviso que el popup muestra mientras la captura espera a X (la hora, en la de quien
 *  mira y como la escribe su idioma). */
export function waitingNotice(account: string, untilMs: number): Msg {
  return { key: 'notice.waiting', params: { account, time: { clock: untilMs } } };
}
