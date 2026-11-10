import { describe, expect, it } from 'vitest';

import {
  blankBackoff,
  crawlUrl,
  dayStart,
  decideStep,
  epochSeconds,
  FRESH_COUNTERS,
  isValidDate,
  MAX_LOADING_WAITS,
  MAX_RETRIES,
  MAX_STALE,
  matchesSearch,
  nextBound,
  pausedNotice,
  reachedSince,
  searchQuery,
  searchUrl,
  untilTime,
} from '../lib/autoscroll';
import { render } from '../lib/i18n';

const JAN_1 = 1735689600; // 2025-01-01T00:00:00Z
const FEB_1 = 1738368000; // 2025-02-01T00:00:00Z

describe('isValidDate', () => {
  it('acepta fechas reales y rechaza las que Date normalizaría', () => {
    expect(isValidDate('2024-02-29')).toBe(true);
    expect(isValidDate('2026-02-31')).toBe(false);
    expect(isValidDate('2026-2-3')).toBe(false);
    expect(isValidDate('')).toBe(false);
  });
});

describe('segundos UTC', () => {
  it('el comienzo de un día y el datetime de x.com', () => {
    expect(dayStart('2025-01-01')).toBe(JAN_1);
    expect(dayStart('2025-02-01')).toBe(FEB_1);
    expect(epochSeconds('2025-01-01T00:00:59.000Z')).toBe(JAN_1 + 59);
    expect(epochSeconds('no es una fecha')).toBeNull();
  });
  it('un crawl viejo, sin cota en segundos, busca hasta el fin del rango', () => {
    expect(untilTime({ account: 'nasa', since: '2025-01-01', until: '2025-02-01' })).toBe(FEB_1);
    expect(
      untilTime({ account: 'nasa', since: '2025-01-01', until: '2025-02-01', untilTime: JAN_1 + 7 }),
    ).toBe(JAN_1 + 7);
  });
});

describe('nextBound', () => {
  it('baja la cota al segundo del tweet más viejo, re-incluyéndolo', () => {
    // Un día con más tweets de los que muestra una búsqueda: la pared cae a las 14:00 y la
    // búsqueda siguiente arranca ahí, no al otro día (punto 12).
    const wall = dayStart('2025-01-10') + 14 * 3600;
    expect(nextBound(wall, dayStart('2025-01-11'))).toBe(wall + 1);
  });
  it('si la búsqueda no trajo nada más viejo que el re-incluido, terminó', () => {
    expect(nextBound(JAN_1 + 99, JAN_1 + 100)).toBeNull();
    expect(nextBound(JAN_1 + 100, JAN_1 + 100)).toBeNull();
  });
  it('la cota baja siempre: el crawl termina', () => {
    let until: number | null = FEB_1;
    for (const oldest of [FEB_1 - 3600, FEB_1 - 7200, FEB_1 - 7201, FEB_1 - 7200]) {
      const next = nextBound(oldest, until as number);
      if (next === null) break;
      expect(next).toBeLessThan(until as number);
      until = next;
    }
    expect(until).toBe(FEB_1 - 7200);
  });
});

describe('reachedSince', () => {
  it('termina cuando lo más viejo cargado llega al inicio del rango', () => {
    expect(reachedSince(JAN_1, JAN_1)).toBe(true);
    expect(reachedSince(JAN_1 - 1, JAN_1)).toBe(true);
    expect(reachedSince(JAN_1 + 1, JAN_1)).toBe(false);
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
    expect(render('es', pausedNotice('nasa'))).toMatch(/@nasa está en pausa/);
    expect(render('en', pausedNotice('nasa'))).toMatch(/@nasa is paused/);
  });
});

describe('blankBackoff', () => {
  it('crece de 5 s en adelante y se queda en 60 s', () => {
    expect([1, 2, 3, 4, 5, 9].map(blankBackoff)).toEqual([5000, 10000, 20000, 40000, 60000, 60000]);
  });
});

describe('búsqueda', () => {
  it('arma la query al segundo y en UTC, y la URL de la pestaña "Latest"', () => {
    expect(searchQuery('nasa', JAN_1, FEB_1)).toBe(
      `from:nasa since_time:${JAN_1} until_time:${FEB_1}`,
    );
    const url = new URL(searchUrl('nasa', JAN_1, FEB_1));
    expect(url.searchParams.get('q')).toBe(`from:nasa since_time:${JAN_1} until_time:${FEB_1}`);
    expect(url.searchParams.get('f')).toBe('live');
  });
  it('solo arranca en la pestaña que es la búsqueda del crawl', () => {
    const state = { account: 'nasa', since: '2025-01-01', until: '2025-02-01', untilTime: FEB_1 - 60 };
    expect(crawlUrl(state)).toBe(searchUrl('nasa', JAN_1, FEB_1 - 60));
    expect(matchesSearch(searchUrl('nasa', JAN_1, FEB_1 - 60), state)).toBe(true);
    expect(matchesSearch(searchUrl('nasa', JAN_1, FEB_1), state)).toBe(false);
    expect(matchesSearch('https://x.com/home', state)).toBe(false);
    expect(matchesSearch('no es una url', state)).toBe(false);
  });
});
