import { describe, expect, it } from 'vitest';

import {
  type AccountCheck,
  answersCheck,
  CHECK_TIMEOUT_MS,
  checkStep,
  checkUrl,
  onCheckPage,
  userPayload,
} from '../lib/account';

const check: AccountCheck = { captureId: 7, account: 'tayandyou', tabId: 3, startedAt: 1_000 };

describe('checkUrl', () => {
  it('va a la página de seguidos, que pide UserByScreenName sin cargar tweets', () => {
    expect(checkUrl('nasa')).toBe('https://x.com/nasa/following');
  });
});

describe('checkStep', () => {
  it('sin comprobación, o ya terminada, la cola sigue', () => {
    expect(checkStep(null, 5_000)).toBe('none');
    expect(checkStep({ ...check, finished: true }, 5_000)).toBe('none');
  });
  it('espera hasta el plazo y después termina sin respuesta', () => {
    expect(checkStep(check, check.startedAt)).toBe('waiting');
    expect(checkStep(check, check.startedAt + CHECK_TIMEOUT_MS - 1)).toBe('waiting');
    expect(checkStep(check, check.startedAt + CHECK_TIMEOUT_MS)).toBe('expired');
  });
});

describe('answersCheck', () => {
  it('solo la respuesta de la cuenta que se está mirando', () => {
    expect(answersCheck(check, 'tayandyou')).toBe(true);
    expect(answersCheck(check, 'nasa')).toBe(false); // una tarjeta de otra cuenta
    expect(answersCheck(check, null)).toBe(false);
    expect(answersCheck(null, 'tayandyou')).toBe(false);
    expect(answersCheck({ ...check, finished: true }, 'tayandyou')).toBe(false); // llegó tarde
  });
});

describe('userPayload', () => {
  it('pasa la respuesta tal cual si es un objeto, y nada si no', () => {
    const data = { data: {} };
    expect(userPayload(data)).toBe(data);
    expect(userPayload(null)).toBeUndefined();
    expect(userPayload([])).toBeUndefined();
    expect(userPayload('{"data":{}}')).toBeUndefined();
  });
});

describe('onCheckPage', () => {
  it('la pestaña sigue en la página de seguidos o en el perfil (la protegida redirige ahí)', () => {
    expect(onCheckPage('https://x.com/tayandyou/following', 'tayandyou')).toBe(true);
    expect(onCheckPage('https://x.com/TayandYou', 'tayandyou')).toBe(true);
    expect(onCheckPage('https://x.com/TayandYou/', 'tayandyou')).toBe(true);
  });
  it('si se fue a otra parte, o es otra pestaña, no', () => {
    expect(onCheckPage('https://x.com/home', 'tayandyou')).toBe(false);
    expect(onCheckPage('https://x.com/nasa/following', 'tayandyou')).toBe(false);
    expect(onCheckPage('https://x.com/tayandyou/status/1', 'tayandyou')).toBe(false);
    expect(onCheckPage('https://example.com/tayandyou', 'tayandyou')).toBe(false);
    expect(onCheckPage('https://x.com/', '')).toBe(false);
    expect(onCheckPage('no es una url', 'tayandyou')).toBe(false);
  });
});
