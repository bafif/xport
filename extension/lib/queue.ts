// La cola de capturas del lado de la extensión (punto 7 de docs/PLAN.md). Puro y testeable.
//
// El servicio guarda la cola; el background le pide la siguiente cada medio minuto (con
// `alarms`, que despiertan al service worker de MV3) o cuando el popup encola una, la
// crawlea como siempre y le avisa su estado. Va de a una: X frena la búsqueda por cuenta de
// X y en paralelo no rinde.

import type { CaptureOut, ProgressResult } from './api';
import { type CrawlState, dayBefore, SATURATED_DAY } from './autoscroll';

export const QUEUE_ALARM = 'xport-queue';
export const POLL_MINUTES = 0.5; // el mínimo de Chrome para `alarms`

/** Popup → background: «mirá la cola ahora», sin esperar a la próxima alarma. */
export const QUEUE_MSG = 'xport-queue' as const;
export interface QueueMsg {
  type: typeof QUEUE_MSG;
}

/** Content script → background: qué pasó con el crawl de una captura de la cola. */
export const CRAWL_MSG = 'xport-crawl' as const;
export type CrawlEvent = 'done' | 'paused' | 'resumed';
export interface CrawlMsg {
  type: typeof CRAWL_MSG;
  event: CrawlEvent;
  captureId: number;
}

/** El estado que se le informa al servicio por cada evento del crawl. */
export function reportedState(event: CrawlEvent): 'done' | 'paused' | 'running' {
  return event === 'done' ? 'done' : event === 'paused' ? 'paused' : 'running';
}

export type StartPlan =
  | { kind: 'done' } // el rango ya está entero: no hay nada que crawlear
  | { kind: 'crawl'; until: string; message: string };

/** Desde dónde arranca (o retoma) el crawl de una captura, según lo que ya está guardado.
 *  La captura va del más nuevo al más viejo: el tweet más viejo guardado marca hasta dónde
 *  se llegó, y retomar ahí no regasta el cupo de la búsqueda de X ni el tope. */
export function startPlan(prog: ProgressResult, since: string, until: string): StartPlan {
  const who = `@${prog.account}`;
  if (!prog.oldest) {
    return { kind: 'crawl', until, message: `Capturando ${who}… (podés cerrar el popup)` };
  }
  if (prog.oldest <= since) return { kind: 'done' };
  if (prog.oldest_count >= SATURATED_DAY) {
    // El día más viejo ya llegó al techo de X: re-incluirlo dejaría el crawl trabado ahí.
    // Se excluye para avanzar (se pierde su cola, inevitable a granularidad de día). X toma
    // adentro el día de `until:`, así que excluirlo es pedir hasta el día anterior.
    return {
      kind: 'crawl',
      until: dayBefore(prog.oldest),
      message:
        `Día ${prog.oldest} saturado (${prog.oldest_count} tuits, ~tope de X): salteo su ` +
        `resto y sigo más atrás con ${who}… (podés cerrar el popup)`,
    };
  }
  return {
    kind: 'crawl',
    until: prog.oldest, // X incluye el día de `until:`: re-incluye el más viejo (el store deduplica)
    message:
      `Reanudando ${who} desde ${prog.oldest} (ya tenías ${prog.captured} tuits)… ` +
      '(podés cerrar el popup)',
  };
}

export type QueueStep = 'idle' | 'start' | 'keep' | 'stop-crawl';

/** Qué hace el background en cada vuelta, según el crawl guardado y lo que dice el servicio
 *  (`next`: la captura en curso o la siguiente; `null` si no hay ninguna).
 *  - Un crawl sin captura (uno viejo, de antes de la cola) sigue solo mientras la cola no
 *    tenga nada: si hay una captura, se corta y la próxima vuelta la arranca. Antes se
 *    dejaba para siempre, y uno trabado (sin pestaña) frenaba la cola sin aviso.
 *  - El crawl de una captura sigue mientras el servicio diga que es la que va; si dice otra
 *    cosa (la cancelaron desde la página, o ya terminó), se corta.
 *  - Sin crawl, se arranca la que diga el servicio. */
export function decideQueue(crawl: CrawlState | null, next: CaptureOut | null): QueueStep {
  if (crawl) {
    if (crawl.captureId === undefined) return next ? 'stop-crawl' : 'keep';
    return next?.id === crawl.captureId ? 'keep' : 'stop-crawl';
  }
  return next ? 'start' : 'idle';
}
