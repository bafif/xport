import { describe, expect, it } from 'vitest';

import { type Note, t } from '../lib/i18n';
import { decideIngest, STOP_AFTER_PENDING, stopNotice } from '../lib/ingest';

// Regla 1 de docs/PLAN.md: si el gate no puede contar lo que se ve, el crawl se corta.
describe('decideIngest', () => {
  const ok = { over_cap: false, format_changed: false };
  it('OK sigue; OK con over_cap corta por el tope', () => {
    expect(decideIngest(200, ok, 1)).toEqual({ requeue: false, stop: null });
    expect(decideIngest(200, { ...ok, over_cap: true }, 1)).toEqual({ requeue: false, stop: 'cap' });
  });
  it('OK con format_changed corta: x.com cambió la forma de los datos', () => {
    const changed = { ...ok, format_changed: true };
    expect(decideIngest(200, changed, 1)).toEqual({ requeue: false, stop: 'format' });
    expect(decideIngest(200, { ...changed, over_cap: true }, 1).stop).toBe('format');
  });
  it('429 no se reintenta (el backend ya lo registró) y corta por el tope', () => {
    expect(decideIngest(429, null, 1)).toEqual({ requeue: false, stop: 'cap' });
  });
  it('otro 4xx no se reintenta y corta', () => {
    expect(decideIngest(413, null, 1)).toEqual({ requeue: false, stop: 'rejected' });
    expect(decideIngest(422, null, 1)).toEqual({ requeue: false, stop: 'rejected' });
  });
  it('servicio caído o 5xx: re-encola, y corta cuando se acumulan páginas sin contar', () => {
    for (const status of [0, 500, 503]) {
      expect(decideIngest(status, null, STOP_AFTER_PENDING - 1)).toEqual({ requeue: true, stop: null });
      expect(decideIngest(status, null, STOP_AFTER_PENDING)).toEqual({ requeue: true, stop: 'offline' });
    }
  });
});

describe('stopNotice', () => {
  it('explica cada corte en palabras, en el idioma de quien lo lee', () => {
    const say = (lang: 'es' | 'en', n: Note) => t(lang, n.key, n.params);
    expect(say('es', stopNotice('cap', 'http://localhost:8000', 429))).toMatch(/tope de 24 h/);
    expect(say('en', stopNotice('cap', 'http://localhost:8000', 429))).toMatch(/24-hour cap/);
    expect(say('es', stopNotice('offline', 'http://localhost:8000', 0))).toMatch(/localhost:8000/);
    expect(say('en', stopNotice('rejected', 'http://localhost:8000', 413))).toMatch(/\(413\)/);
    expect(say('es', stopNotice('format', 'http://localhost:8000', 200))).toMatch(/actualizar xport/);
  });
});
