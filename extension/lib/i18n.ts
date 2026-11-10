// Los textos de la extensión en castellano y en inglés (`locales/es.json` y `locales/en.json`).
// Lo que ve la persona sale de acá con `t()`; los tests revisan que los dos catálogos tengan
// las mismas claves y los mismos `{valores}`, y que no queden textos escritos a mano.
//
// El idioma: castellano si alguno de los idiomas del navegador lo es (`navigator.languages`),
// inglés en cualquier otro caso. El selector del popup lo cambia y la elección queda en el
// storage de la extensión, por encima de la detección. El nombre y la descripción de la
// extensión van aparte, en `public/_locales`, para la tienda y la lista de extensiones.
//
// Los avisos que se guardan (el motivo de un corte, la pausa) se guardan como `Msg`, una clave
// con sus valores, y se traducen al mostrarse: así salen en el idioma de quien los lee. Los
// `note.*` también los muestra el servicio: tienen el mismo texto en su catálogo.

import es from '../locales/es.json';
import en from '../locales/en.json';

export type Lang = 'es' | 'en';
export const LANGS: readonly Lang[] = ['es', 'en'];
export const STORAGE_LANG = 'xport:lang';

export type MsgKey = keyof typeof es;
export type NoteKey = Extract<MsgKey, `note.${string}`>;

// Si a `en.json` le falta una clave de `es.json`, esto no compila.
const CATALOGS: Record<Lang, Record<MsgKey, string>> = { es, en };

/** Un valor de un mensaje. `{ clock: ms }` es una hora, que se muestra como la escribe cada
 *  idioma («18:40», «6:40 PM»). */
export type MsgParam = string | number | { clock: number };

/** Un mensaje que se guarda y se muestra después, en el idioma de quien lo lee. */
export interface Msg {
  key: MsgKey;
  params?: Record<string, MsgParam>;
}

/** Un aviso que también va al servicio (el motivo de un corte): solo textos y números. */
export interface Note {
  key: NoteKey;
  params?: Record<string, string | number>;
}

/** Castellano si alguno de los idiomas preferidos lo es (`es`, `es-AR`, `es-419`…). */
export function detectLang(languages: readonly string[]): Lang {
  return languages.some((l) => /^es(-|_|$)/i.test(l)) ? 'es' : 'en';
}

export function isLang(value: unknown): value is Lang {
  return value === 'es' || value === 'en';
}

/** El idioma de la extensión: el elegido en el popup o, si no hay, el del navegador. */
export function chooseLang(saved: unknown, languages: readonly string[]): Lang {
  return isLang(saved) ? saved : detectLang(languages);
}

export function locale(lang: Lang): string {
  return lang === 'es' ? 'es-AR' : 'en-US';
}

/** Una hora: «18:40» en castellano, «6:40 PM» en inglés. */
export function clock(ms: number, lang: Lang): string {
  const options: Intl.DateTimeFormatOptions =
    lang === 'es'
      ? { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }
      : { hour: 'numeric', minute: '2-digit' };
  return new Date(ms).toLocaleTimeString(locale(lang), options);
}

/** El texto de `key` en `lang`, con sus `{valores}`. */
export function t(lang: Lang, key: MsgKey, params: Record<string, MsgParam> = {}): string {
  return CATALOGS[lang][key].replace(/\{(\w+)\}/g, (match, name: string) => {
    const value = params[name];
    if (value === undefined) return match;
    if (typeof value === 'object') return clock(value.clock, lang);
    return typeof value === 'number' ? value.toLocaleString(locale(lang)) : value;
  });
}

/** Un aviso guardado: un `Msg`, o un texto suelto de antes de los dos idiomas. */
export function render(lang: Lang, stored: unknown): string | null {
  if (typeof stored === 'string') return stored || null;
  if (stored && typeof stored === 'object' && 'key' in stored) {
    const msg = stored as Msg;
    if (msg.key in CATALOGS[lang]) return t(lang, msg.key, msg.params);
  }
  return null;
}
