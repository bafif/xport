import { describe, expect, it } from 'vitest';

import { decideIngest, STOP_AFTER_PENDING, stopNotice } from '../lib/ingest';

// Regla 1 de docs/PLAN.md: si el gate no puede contar lo que se ve, el crawl se corta.
describe('decideIngest', () => {
  it('OK sigue; OK con over_cap corta por el tope', () => {
    expect(decideIngest(200, false, 1)).toEqual({ requeue: false, stop: null });
    expect(decideIngest(200, true, 1)).toEqual({ requeue: false, stop: 'cap' });
  });
  it('429 no se reintenta (el backend ya lo registró) y corta por el tope', () => {
    expect(decideIngest(429, false, 1)).toEqual({ requeue: false, stop: 'cap' });
  });
  it('otro 4xx no se reintenta y corta', () => {
    expect(decideIngest(413, false, 1)).toEqual({ requeue: false, stop: 'rejected' });
    expect(decideIngest(422, false, 1)).toEqual({ requeue: false, stop: 'rejected' });
  });
  it('servicio caído o 5xx: re-encola, y corta cuando se acumulan páginas sin contar', () => {
    for (const status of [0, 500, 503]) {
      expect(decideIngest(status, false, STOP_AFTER_PENDING - 1)).toEqual({ requeue: true, stop: null });
      expect(decideIngest(status, false, STOP_AFTER_PENDING)).toEqual({ requeue: true, stop: 'offline' });
    }
  });
});

describe('stopNotice', () => {
  it('explica cada corte en palabras', () => {
    expect(stopNotice('cap', 'http://localhost:8000', 429)).toMatch(/tope de 24 h/);
    expect(stopNotice('offline', 'http://localhost:8000', 0)).toMatch(/localhost:8000/);
    expect(stopNotice('rejected', 'http://localhost:8000', 413)).toMatch(/413/);
  });
});
