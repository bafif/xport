import { describe, expect, it } from 'vitest';

import { render } from '../lib/i18n';

import {
  decideRate,
  FALLBACK_WAIT_MS,
  MAX_PACE_MS,
  MIN_WAIT_MS,
  PACE_BELOW,
  RATE_SOURCE,
  type RateInfo,
  rateFromMsg,
  RESUME_MARGIN_MS,
  waitingNotice,
} from '../lib/rate';

const NOW = 1_762_790_400_000; // ms
const RESET = NOW / 1000 + 600; // dentro de 10 minutos, en segundos

const rate = (over: Partial<RateInfo> = {}): RateInfo => ({
  status: 200,
  remaining: 40,
  reset: RESET,
  at: NOW,
  ...over,
});

describe('rateFromMsg', () => {
  it('lee el status y los encabezados de SearchTimeline', () => {
    const msg = { source: RATE_SOURCE, op: 'SearchTimeline', status: 429, remaining: '0', reset: `${RESET}` };
    expect(rateFromMsg(msg, NOW)).toEqual({ status: 429, remaining: 0, reset: RESET, at: NOW });
  });
  it('sin encabezados quedan en null', () => {
    const msg = { source: RATE_SOURCE, op: 'SearchTimeline', status: 200, remaining: null, reset: 'x' };
    expect(rateFromMsg(msg, NOW)).toMatchObject({ remaining: null, reset: null });
  });
  it('ignora otros endpoints (tienen su propio cupo) y mensajes ajenos', () => {
    expect(rateFromMsg({ source: RATE_SOURCE, op: 'UserTweets', status: 429 }, NOW)).toBeNull();
    expect(rateFromMsg({ op: 'SearchTimeline', status: 429 }, NOW)).toBeNull();
    expect(rateFromMsg(null, NOW)).toBeNull();
  });
});

describe('decideRate', () => {
  it('sin datos o con cupo de sobra, sigue', () => {
    expect(decideRate(null, NOW)).toEqual({ kind: 'go' });
    expect(decideRate(rate(), NOW)).toEqual({ kind: 'go' });
    expect(decideRate(rate({ remaining: PACE_BELOW }), NOW)).toEqual({ kind: 'go' });
  });

  it('ante un 429 espera al reset, con margen, en vez de insistir', () => {
    expect(decideRate(rate({ status: 429, remaining: 0 }), NOW)).toEqual({
      kind: 'wait',
      untilMs: RESET * 1000 + RESUME_MARGIN_MS,
    });
  });

  it('con el cupo en cero espera aunque la respuesta haya sido 200', () => {
    expect(decideRate(rate({ remaining: 0 }), NOW)).toEqual({
      kind: 'wait',
      untilMs: RESET * 1000 + RESUME_MARGIN_MS,
    });
  });

  it('un 429 sin reset espera la ventana entera de X', () => {
    expect(decideRate(rate({ status: 429, reset: null }), NOW)).toEqual({
      kind: 'wait',
      untilMs: NOW + FALLBACK_WAIT_MS + RESUME_MARGIN_MS,
    });
  });

  it('un 429 con el reset ya pasado (reloj adelantado) espera igual un minuto', () => {
    const r = rate({ status: 429, reset: NOW / 1000 - 30 });
    expect(decideRate(r, NOW)).toEqual({ kind: 'wait', untilMs: NOW + MIN_WAIT_MS + RESUME_MARGIN_MS });
  });

  it('pasada la espera, el 429 es viejo y sigue', () => {
    const r = rate({ status: 429, remaining: 0 });
    expect(decideRate(r, RESET * 1000 + RESUME_MARGIN_MS)).toEqual({ kind: 'go' });
  });

  it('con pocas búsquedas, reparte las que quedan hasta el reset', () => {
    // 600 s hasta el reset y 4 búsquedas: una cada 120 s (600 / 5)
    expect(decideRate(rate({ remaining: 4 }), NOW)).toEqual({ kind: 'slow', delayMs: 120_000 });
    expect(decideRate(rate({ remaining: 9 }), NOW)).toEqual({ kind: 'slow', delayMs: 60_000 });
    // nunca más que MAX_PACE_MS por búsqueda
    expect(decideRate(rate({ remaining: 1 }), NOW)).toEqual({ kind: 'slow', delayMs: MAX_PACE_MS });
  });

  it('con pocas búsquedas pero el reset pasado, el cupo ya volvió', () => {
    expect(decideRate(rate({ remaining: 2, reset: NOW / 1000 - 1 }), NOW)).toEqual({ kind: 'go' });
  });
});

describe('waitingNotice', () => {
  it('dice la cuenta y que sigue sola', () => {
    expect(render('es', waitingNotice('nasa', NOW))).toMatch(/@nasa espera hasta las .+ y sigue sola/);
    expect(render('en', waitingNotice('nasa', NOW))).toMatch(/@nasa waits until .+ and carries on/);
  });
});
