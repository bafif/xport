import { describe, expect, it } from 'vitest';

import type { CaptureOut, ProgressResult } from '../lib/api';
import { isCaptureSearch, narrowed, searchUrl } from '../lib/autoscroll';
import { decideQueue, reportedState, startPlan } from '../lib/queue';

const prog = (over: Partial<ProgressResult> = {}): ProgressResult => ({
  account: 'nasa',
  oldest: null,
  captured: 0,
  oldest_count: 0,
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
});

describe('startPlan', () => {
  it('sin nada guardado, arranca desde el final del rango', () => {
    expect(startPlan(prog(), '2024-01-01', '2025-01-01')).toMatchObject({
      kind: 'crawl',
      until: '2025-01-01',
    });
  });

  it('retoma re-incluyendo el día más viejo guardado', () => {
    const plan = startPlan(prog({ oldest: '2024-06-10', captured: 300 }), '2024-01-01', '2025-01-01');
    expect(plan).toMatchObject({ kind: 'crawl', until: '2024-06-11' });
  });

  it('saltea un día saturado', () => {
    const plan = startPlan(prog({ oldest: '2024-06-10', oldest_count: 950 }), '2024-01-01', '2025-01-01');
    expect(plan).toMatchObject({ kind: 'crawl', until: '2024-06-10' });
  });

  it('con el rango entero, no hay nada que crawlear', () => {
    expect(startPlan(prog({ oldest: '2024-01-01' }), '2024-01-01', '2025-01-01')).toEqual({
      kind: 'done',
    });
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
  });

  it('al narrowar se conserva la captura y se descarta el reintento', () => {
    const state = { account: 'nasa', since: '2024-01-01', until: '2025-01-01', attempt: 3, captureId: 7 };
    expect(narrowed(state, '2024-06-11')).toEqual({
      account: 'nasa',
      since: '2024-01-01',
      until: '2024-06-11',
      captureId: 7,
    });
  });

  it('reconoce una pestaña de búsqueda de captura', () => {
    expect(isCaptureSearch(searchUrl('nasa', '2024-01-01', '2025-01-01'))).toBe(true);
    expect(isCaptureSearch('https://x.com/search?q=gatos&f=live')).toBe(false);
    expect(isCaptureSearch('https://x.com/home')).toBe(false);
    expect(isCaptureSearch('https://example.com/search?q=from%3Anasa')).toBe(false);
  });
});
