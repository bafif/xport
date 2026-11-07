import { describe, expect, it } from 'vitest';

import {
  blankBackoff,
  dayAfter,
  decideStep,
  FRESH_COUNTERS,
  isValidDate,
  MAX_LOADING_WAITS,
  MAX_RETRIES,
  MAX_STALE,
  matchesSearch,
  nextUntil,
  pausedNotice,
  reachedSince,
  searchQuery,
  searchUrl,
} from '../lib/autoscroll';

describe('isValidDate', () => {
  it('acepta fechas reales y rechaza las que Date normalizaría', () => {
    expect(isValidDate('2024-02-29')).toBe(true);
    expect(isValidDate('2026-02-31')).toBe(false);
    expect(isValidDate('2026-2-3')).toBe(false);
    expect(isValidDate('')).toBe(false);
  });
});

describe('dayAfter', () => {
  it('cruza meses y años en UTC', () => {
    expect(dayAfter('2025-12-31')).toBe('2026-01-01');
    expect(dayAfter('2024-02-28')).toBe('2024-02-29');
  });
});

describe('nextUntil', () => {
  it('re-incluye el día más viejo cargado para no perder su cola', () => {
    expect(nextUntil('2025-03-10', '2025-04-01')).toBe('2025-03-11');
  });
  it('si incluirlo no avanza (un solo día saturado), lo excluye para garantizar progreso', () => {
    expect(nextUntil('2025-03-10', '2025-03-11')).toBe('2025-03-10');
  });
});

describe('reachedSince', () => {
  it('termina cuando lo más viejo cargado llega al inicio del rango', () => {
    expect(reachedSince('2025-01-01', '2025-01-01')).toBe(true);
    expect(reachedSince('2024-12-31', '2025-01-01')).toBe(true);
    expect(reachedSince('2025-01-02', '2025-01-01')).toBe(false);
  });
});

describe('decideStep', () => {
  const visible = { hidden: false, grew: false, loading: false, retry: false };
  it('con la pestaña oculta pausa y no cuenta el scroll quieto', () => {
    // Al borde de asentarse: un scroll quieto más sería 'settled' y achicaría el rango.
    const edge = { ...FRESH_COUNTERS, stale: MAX_STALE - 1 };
    expect(decideStep(visible, edge).action).toBe('settled');
    const paused = decideStep({ ...visible, hidden: true }, edge);
    expect(paused.action).toBe('pause');
    expect(paused.counters.stale).toBe(0); // vuelve a la vista y arranca de cero
    // Oculta, ni el crecimiento ni el spinner deciden nada.
    expect(decideStep({ hidden: true, grew: true, loading: true, retry: true }, edge).action).toBe(
      'pause',
    );
  });
  it('se asienta tras MAX_STALE scrolls quietos a la vista', () => {
    let counters = FRESH_COUNTERS;
    const actions = [];
    for (let i = 0; i < MAX_STALE; i++) {
      const step = decideStep(visible, counters);
      actions.push(step.action);
      counters = step.counters;
    }
    expect(actions).toEqual([...Array(MAX_STALE - 1).fill('stale'), 'settled']);
  });
  it('si crece, reinicia los contadores', () => {
    const step = decideStep({ ...visible, grew: true }, { stale: 3, retries: 2, loadingWaits: 5 });
    expect(step).toEqual({ action: 'grew', counters: FRESH_COUNTERS });
  });
  it('espera al spinner y aprieta «Reintentar» hasta sus topes', () => {
    expect(decideStep({ ...visible, loading: true }, FRESH_COUNTERS).action).toBe('wait-loading');
    const tired = { ...FRESH_COUNTERS, loadingWaits: MAX_LOADING_WAITS };
    expect(decideStep({ ...visible, loading: true }, tired).action).toBe('stale');
    expect(decideStep({ ...visible, retry: true }, FRESH_COUNTERS).action).toBe('retry');
    const retried = { ...FRESH_COUNTERS, retries: MAX_RETRIES };
    expect(decideStep({ ...visible, retry: true }, retried).action).toBe('stale');
  });
  it('el aviso de pausa nombra la cuenta', () => {
    expect(pausedNotice('nasa')).toMatch(/@nasa está en pausa/);
  });
});

describe('blankBackoff', () => {
  it('crece de 5 s en adelante y se queda en 60 s', () => {
    expect([1, 2, 3, 4, 5, 9].map(blankBackoff)).toEqual([5000, 10000, 20000, 40000, 60000, 60000]);
  });
});

describe('búsqueda', () => {
  it('arma la query y la URL de la pestaña "Latest"', () => {
    expect(searchQuery('nasa', '2025-01-01', '2025-02-01')).toBe(
      'from:nasa since:2025-01-01 until:2025-02-01',
    );
    const url = new URL(searchUrl('nasa', '2025-01-01', '2025-02-01'));
    expect(url.searchParams.get('q')).toBe('from:nasa since:2025-01-01 until:2025-02-01');
    expect(url.searchParams.get('f')).toBe('live');
  });
  it('solo arranca en la pestaña que es la búsqueda del crawl', () => {
    const state = { account: 'nasa', since: '2025-01-01', until: '2025-02-01' };
    expect(matchesSearch(searchUrl('nasa', '2025-01-01', '2025-02-01'), state)).toBe(true);
    expect(matchesSearch(searchUrl('nasa', '2025-01-01', '2025-01-15'), state)).toBe(false);
    expect(matchesSearch('https://x.com/home', state)).toBe(false);
    expect(matchesSearch('no es una url', state)).toBe(false);
  });
});
