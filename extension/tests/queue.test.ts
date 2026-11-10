import { describe, expect, it } from 'vitest';

import type { CaptureOut, ProgressResult } from '../lib/api';
import { dayStart, isCaptureSearch, isStaleSearch, narrowed, searchUrl } from '../lib/autoscroll';
import {
  claimAllowed,
  decideQueue,
  reportedState,
  resumeBound,
  startPlan,
  waitStep,
} from '../lib/queue';

const prog = (over: Partial<ProgressResult> = {}): ProgressResult => ({
  account: 'nasa',
  oldest: null,
  captured: 0,
  ...over,
});

const cap = (id: number): CaptureOut => ({
  id,
  account: 'nasa',
  since: '2024-01-01',
  until: '2025-01-01',
  state: 'running',
  note: null,
  csv: null,
  download_url: null,
  exported: null,
  excluded_replies: null,
  oldest: null,
  captured: 0,
  percent: 0,
  last_page_at: null,
  resume_at: null,
});

describe('startPlan', () => {
  it('sin nada guardado, arranca desde el final del rango', () => {
    expect(startPlan(prog(), '2024-01-01', '2025-01-01')).toMatchObject({
      kind: 'crawl',
      untilTime: dayStart('2025-01-01'),
    });
  });

  it('retoma al segundo del tweet más viejo guardado, re-incluyéndolo', () => {
    // 2024-06-10T15:30:00Z: retoma a media tarde, no al comienzo ni al final del día
    const plan = startPlan(
      prog({ oldest: '2024-06-10T15:30:00Z', captured: 300 }),
      '2024-01-01',
      '2025-01-01',
    );
    expect(plan).toMatchObject({ kind: 'crawl', untilTime: 1718033401 });
  });

  it('con el rango entero, no hay nada que crawlear', () => {
    expect(startPlan(prog({ oldest: '2024-01-01T00:00:00Z' }), '2024-01-01', '2025-01-01')).toEqual({
      kind: 'done',
    });
  });
});

describe('resumeBound', () => {
  const crawl = { account: 'amazonhelp', since: '2026-11-09', until: '2026-11-10', captureId: 5 };
  it('al reabrir, sigue desde el tweet más viejo guardado y no desde arriba', () => {
    // El crawl iba por las 12:00 dentro de la primera búsqueda; su cota sigue en el fin del día.
    expect(resumeBound(crawl, '2026-11-09T12:00:00Z')).toBe(dayStart('2026-11-09') + 12 * 3600 + 1);
  });
  it('sin nada guardado, o con lo guardado más arriba, la cota del crawl', () => {
    expect(resumeBound(crawl, null)).toBe(dayStart('2026-11-10'));
    const lower = { ...crawl, untilTime: dayStart('2026-11-09') + 3600 };
    expect(resumeBound(lower, '2026-11-09T12:00:00Z')).toBe(dayStart('2026-11-09') + 3600);
  });
});

describe('claimAllowed', () => {
  it('crawlea una sola pestaña por captura', () => {
    expect(claimAllowed(undefined, 7, false)).toBe(true); // nadie la tenía
    expect(claimAllowed(7, 7, true)).toBe(true); // ya era suya (p. ej., al narrowar)
    expect(claimAllowed(3, 7, true)).toBe(false); // otra viva y en la búsqueda
    expect(claimAllowed(3, 7, false)).toBe(true); // la otra se cerró o ya no está en la búsqueda
  });
});

describe('waitStep', () => {
  const crawl = { account: 'nasa', since: '2024-01-01', until: '2025-01-01', captureId: 7 };
  it('sin espera, nada; con espera, hasta la hora y después retoma', () => {
    expect(waitStep(crawl, 1000)).toBe('none');
    expect(waitStep({ ...crawl, waitUntil: 5000 }, 4999)).toBe('waiting');
    expect(waitStep({ ...crawl, waitUntil: 5000 }, 5000)).toBe('resume');
  });
});

describe('decideQueue', () => {
  const crawl = { account: 'nasa', since: '2024-01-01', until: '2025-01-01', captureId: 7 };

  it('sin crawl arranca la que diga el servicio, o nada', () => {
    expect(decideQueue(null, cap(7))).toBe('start');
    expect(decideQueue(null, null)).toBe('idle');
  });

  it('el crawl de una captura sigue mientras sea la que va', () => {
    expect(decideQueue(crawl, cap(7))).toBe('keep');
  });

  it('si la cancelaron o ya no va, se corta', () => {
    expect(decideQueue(crawl, null)).toBe('stop-crawl');
    expect(decideQueue(crawl, cap(8))).toBe('stop-crawl');
  });

  it('un crawl de antes de la cola no se toca', () => {
    const old = { account: 'nasa', since: '2024-01-01', until: '2025-01-01' };
    expect(decideQueue(old, cap(8))).toBe('keep');
  });
});

describe('eventos del crawl', () => {
  it('cada evento se informa como un estado', () => {
    expect(reportedState('done')).toBe('done');
    expect(reportedState('paused')).toBe('paused');
    expect(reportedState('resumed')).toBe('running');
    expect(reportedState('waiting')).toBe('waiting');
  });

  it('al narrowar se conserva la captura y se descarta el reintento', () => {
    const state = {
      account: 'nasa',
      since: '2024-01-01',
      until: '2025-01-01',
      attempt: 3,
      captureId: 7,
      waitUntil: 1,
    };
    expect(narrowed(state, 1718033401)).toEqual({
      account: 'nasa',
      since: '2024-01-01',
      until: '2025-01-01',
      untilTime: 1718033401,
      captureId: 7,
    });
  });

  it('reconoce una pestaña de búsqueda de captura', () => {
    expect(isCaptureSearch(searchUrl('nasa', 1704067200, 1735689600))).toBe(true);
    // las de antes de la Fase 4 (días en la hora de quien busca) ya no son de xport
    expect(
      isCaptureSearch('https://x.com/search?q=from%3Anasa%20since%3A2024-01-01%20until%3A2025-01-01&f=live'),
    ).toBe(false);
    expect(isCaptureSearch('https://x.com/search?q=gatos&f=live')).toBe(false);
  });

  it('reconoce la búsqueda vieja que dejó trabada una captura, y nada más', () => {
    const crawl = { account: 'amazonhelp', since: '2026-11-09', until: '2026-11-10', captureId: 1 };
    // la de antes de actualizar la extensión, que el navegador restauró
    const old = 'https://x.com/search?q=from%3Aamazonhelp%20since%3A2026-11-09%20until%3A2026-11-10&f=live';
    expect(isStaleSearch(old, crawl)).toBe(true);
    // una cota vieja del mismo crawl
    expect(isStaleSearch(searchUrl('amazonhelp', dayStart('2026-11-09'), 1794230000), crawl)).toBe(true);
    // la actual no; otra cuenta, otra página de x.com u otro sitio tampoco
    expect(isStaleSearch(searchUrl('amazonhelp', dayStart('2026-11-09'), dayStart('2026-11-10')), crawl)).toBe(false);
    expect(isStaleSearch('https://x.com/search?q=from%3Anasa%20since%3A2026-11-09&f=live', crawl)).toBe(false);
    expect(isStaleSearch('https://x.com/home', crawl)).toBe(false);
    expect(isStaleSearch('https://example.com/search?q=from%3Aamazonhelp%20x', crawl)).toBe(false);
    expect(isCaptureSearch('https://x.com/home')).toBe(false);
    expect(isCaptureSearch('https://example.com/search?q=from%3Anasa')).toBe(false);
  });
});
