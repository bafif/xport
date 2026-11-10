// La cola de capturas del lado de la extensión (punto 7 de docs/PLAN.md). Puro y testeable.
//
// El servicio guarda la cola; el background le pide la siguiente cada medio minuto (con
// `alarms`, que despiertan al service worker de MV3) o cuando el popup encola una, la
// crawlea como siempre y le avisa su estado. Va de a una: X frena la búsqueda por cuenta de
// X y en paralelo no rinde.

import type { CaptureOut, ProgressResult } from './api';
import {
  type CrawlState,
  dayStart,
  epochSeconds,
  reachedSince,
  showSeconds,
  untilTime,
} from './autoscroll';

export const QUEUE_ALARM = 'xport-queue';
export const POLL_MINUTES = 0.5; // el mínimo de Chrome para `alarms`

/** Popup → background: «mirá la cola ahora», sin esperar a la próxima alarma. */
export const QUEUE_MSG = 'xport-queue' as const;
export interface QueueMsg {
  type: typeof QUEUE_MSG;
}

/** Content script → background: qué pasó con el crawl de una captura de la cola. `waiting`:
 *  X frenó la búsqueda hasta `resumeAt` (ms); llega también sin captura, porque estacionar la
 *  pestaña y retomarla es del background. */
export const CRAWL_MSG = 'xport-crawl' as const;
export type CrawlEvent = 'done' | 'paused' | 'resumed' | 'waiting';
export interface CrawlMsg {
  type: typeof CRAWL_MSG;
  event: CrawlEvent;
  captureId?: number;
  resumeAt?: number;
}

/** El estado que se le informa al servicio por cada evento del crawl. */
export function reportedState(event: CrawlEvent): 'done' | 'paused' | 'running' | 'waiting' {
  if (event === 'resumed') return 'running';
  return event;
}

export type StartPlan =
  | { kind: 'done' } // el rango ya está entero: no hay nada que crawlear
  | { kind: 'crawl'; untilTime: number; message: string };

/** Desde dónde arranca (o retoma) el crawl de una captura, según lo que ya está guardado.
 *  La captura va del más nuevo al más viejo: el tweet más viejo guardado marca hasta dónde
 *  se llegó, al segundo, y retomar ahí no regasta el cupo de la búsqueda de X ni el tope. La
 *  búsqueda re-incluye ese tweet (el store deduplica); si no trae nada más viejo, el crawl
 *  termina solo. */
export function startPlan(prog: ProgressResult, since: string, until: string): StartPlan {
  const who = `@${prog.account}`;
  const oldest = prog.oldest === null ? null : epochSeconds(prog.oldest);
  if (oldest === null) {
    return {
      kind: 'crawl',
      untilTime: dayStart(until),
      message: `Capturando ${who}… (podés cerrar el popup)`,
    };
  }
  if (reachedSince(oldest, dayStart(since))) return { kind: 'done' };
  return {
    kind: 'crawl',
    untilTime: Math.min(oldest + 1, dayStart(until)),
    message:
      `Reanudando ${who} desde ${showSeconds(oldest)} (ya tenías ${prog.captured} tuits)… ` +
      '(podés cerrar el popup)',
  };
}

export type QueueStep = 'idle' | 'start' | 'keep' | 'stop-crawl';

/** Qué hace el background en cada vuelta, según el crawl guardado y lo que dice el servicio
 *  (`next`: la captura en curso o la siguiente; `null` si no hay ninguna).
 *  - Un crawl sin captura (uno viejo, de antes de la cola) no se toca.
 *  - El crawl de una captura sigue mientras el servicio diga que es la que va; si dice otra
 *    cosa (la cancelaron desde la página, o ya terminó), se corta.
 *  - Sin crawl, se arranca la que diga el servicio. */
export function decideQueue(crawl: CrawlState | null, next: CaptureOut | null): QueueStep {
  if (crawl) {
    if (crawl.captureId === undefined) return 'keep';
    return next?.id === crawl.captureId ? 'keep' : 'stop-crawl';
  }
  return next ? 'start' : 'idle';
}

/** La cota para volver a abrir la búsqueda de un crawl (la pestaña se cerró, el navegador se
 *  reinició o pasó la espera a X): la del crawl, o la del tweet más viejo guardado más un
 *  segundo si es más baja. Lo que el crawl avanzó scrolleando dentro de una búsqueda no queda
 *  en su estado, solo en lo guardado; sin esto, la búsqueda vuelve a pedirse desde arriba y
 *  regasta el cupo de X y el tope. Puro y testeable. */
export function resumeBound(crawl: CrawlState, oldest: string | null): number {
  const bound = untilTime(crawl);
  const s = oldest === null ? null : epochSeconds(oldest);
  return s === null ? bound : Math.min(bound, s + 1);
}

/** Pestaña → background: «¿crawleo yo?». Al reiniciarse el navegador puede haber dos pestañas
 *  en la búsqueda de la captura (la que restauró el navegador y la que reabrió el background):
 *  crawlea una sola. */
export const CLAIM_MSG = 'xport-claim' as const;

/** ¿Puede la pestaña `tabId` crawlear la captura? Sí, salvo que otra pestaña viva y en la
 *  búsqueda ya sea la de la captura. Puro y testeable. */
export function claimAllowed(stored: unknown, tabId: number, storedIsCrawling: boolean): boolean {
  return typeof stored !== 'number' || stored === tabId || !storedIsCrawling;
}

/** Un crawl que sigue: `waiting` mientras espera a X, `resume` cuando ya pasó la hora y hay
 *  que volver a la búsqueda, `none` si no estaba esperando. Puro y testeable. */
export function waitStep(crawl: CrawlState, now: number): 'none' | 'waiting' | 'resume' {
  if (crawl.waitUntil === undefined) return 'none';
  return now < crawl.waitUntil ? 'waiting' : 'resume';
}
