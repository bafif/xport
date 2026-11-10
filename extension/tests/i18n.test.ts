import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import ts from 'typescript';
import { describe, expect, it } from 'vitest';

import en from '../locales/en.json';
import es from '../locales/es.json';
import { chooseLang, detectLang, render, t } from '../lib/i18n';

// Castellano e inglés: los dos catálogos completos, la detección del idioma y que lo que se
// ve en la extensión salga del catálogo (nada escrito a mano en el popup ni en el código).

const ROOT = fileURLToPath(new URL('..', import.meta.url));
const read = (rel: string) => readFileSync(join(ROOT, rel), 'utf8');

// Un texto para leer: tres palabras seguidas, o una letra con tilde, eñe o signos de apertura.
const PROSE =
  /[A-Za-zÁÉÍÓÚáéíóúñÑ]{2,}\s+[A-Za-zÁÉÍÓÚáéíóúñÑ]{2,}\s+[A-Za-zÁÉÍÓÚáéíóúñÑ]{2,}|[áéíóúñÑ¿¡]/;

const fields = (text: string) => [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();

describe('los catálogos', () => {
  it('tienen las mismas claves en los dos idiomas', () => {
    expect(Object.keys(en).sort()).toEqual(Object.keys(es).sort());
  });

  it('cada texto tiene los mismos {valores} en los dos idiomas y ninguno está vacío', () => {
    const esMap = es as Record<string, string>;
    const enMap = en as Record<string, string>;
    for (const key of Object.keys(es)) {
      expect(esMap[key].trim(), key).not.toBe('');
      expect(enMap[key].trim(), key).not.toBe('');
      expect(fields(enMap[key]), key).toEqual(fields(esMap[key]));
    }
  });

  it('el nombre y la descripción de la extensión están en los dos idiomas', () => {
    const manifest = (lang: string) =>
      JSON.parse(read(`public/_locales/${lang}/messages.json`)) as Record<
        string,
        { message: string }
      >;
    const [mEs, mEn] = [manifest('es'), manifest('en')];
    expect(Object.keys(mEn).sort()).toEqual(Object.keys(mEs).sort());
    for (const key of Object.keys(mEs)) {
      expect(mEs[key].message.trim(), key).not.toBe('');
      expect(mEn[key].message.trim(), key).not.toBe('');
    }
    const config = read('wxt.config.ts');
    for (const key of Object.keys(mEs)) expect(config).toContain(`__MSG_${key}__`);
    expect(config).toMatch(/default_locale: 'en'/);
  });
});

describe('el idioma', () => {
  it('castellano si alguno de los idiomas del navegador lo es', () => {
    expect(detectLang(['es-AR', 'es', 'en'])).toBe('es');
    expect(detectLang(['en-US', 'es-419'])).toBe('es');
    expect(detectLang(['es'])).toBe('es');
  });

  it('inglés en cualquier otro caso', () => {
    expect(detectLang(['en-US', 'en'])).toBe('en');
    expect(detectLang(['pt-BR', 'fr'])).toBe('en');
    expect(detectLang(['est'])).toBe('en'); // estonio no es castellano
    expect(detectLang([])).toBe('en');
  });

  it('lo que eligió la persona manda sobre la detección', () => {
    expect(chooseLang('en', ['es-AR'])).toBe('en');
    expect(chooseLang('es', ['en-US'])).toBe('es');
    expect(chooseLang(undefined, ['es-AR'])).toBe('es');
    expect(chooseLang('fr', ['en-US'])).toBe('en');
  });
});

describe('los mensajes', () => {
  it('llenan sus valores con números y horas como los escribe cada idioma', () => {
    expect(t('es', 'popup.reset_done', { count: 12345, account: 'nasa' })).toBe(
      'Borrado: 12.345 tuits de @nasa. La próxima captura arranca de cero.',
    );
    expect(t('en', 'popup.reset_done', { count: 12345, account: 'nasa' })).toBe(
      'Deleted 12,345 tweets from @nasa. The next capture starts from scratch.',
    );
    const at = new Date(2026, 10, 10, 18, 40).getTime();
    expect(t('es', 'notice.waiting', { account: 'nasa', time: { clock: at } })).toContain('18:40');
    expect(t('en', 'notice.waiting', { account: 'nasa', time: { clock: at } })).toMatch(/ 6:40\sPM/);
  });

  it('un aviso guardado sale en el idioma de quien lo lee; uno viejo, tal cual', () => {
    const notice = { key: 'note.stop_offline', params: { base: 'http://localhost:8000' } };
    expect(render('es', notice)).toMatch(/^El servicio local no responde en http:\/\/localhost:8000/);
    expect(render('en', notice)).toMatch(/^The local service isn't answering at http:\/\/localhost/);
    expect(render('en', 'La captura de @nasa está en pausa')).toBe('La captura de @nasa está en pausa');
    expect(render('en', { key: 'nope' })).toBeNull();
    expect(render('en', undefined)).toBeNull();
  });
});

describe('nada escrito a mano fuera del catálogo', () => {
  it('el popup: cada texto visible tiene su clave, y las claves existen', () => {
    const html = read('entrypoints/popup/index.html')
      .replace(/<!--[\s\S]*?-->/g, '')
      .replace(/<!doctype[^>]*>/i, '');
    const keys = [...html.matchAll(/data-i18n(?:-placeholder|-aria-label)?="([^"]+)"/g)].map(
      (m) => m[1],
    );
    expect(keys.length).toBeGreaterThan(15);
    for (const key of keys) expect(Object.keys(es), key).toContain(key);

    const VOID = new Set(['meta', 'link', 'input', 'img', 'br']);
    const stack: boolean[] = [];
    const loose: string[] = [];
    for (const m of html.matchAll(/<(\/?)([a-zA-Z0-9]+)([^>]*)>|([^<]+)/g)) {
      const [, closing, tag, attrs, text] = m;
      if (text !== undefined) {
        const s = text.trim();
        if (s && s !== 'xport' && !stack.some(Boolean)) loose.push(s);
      } else if (VOID.has(tag.toLowerCase())) {
        continue;
      } else if (closing) {
        stack.pop();
      } else {
        stack.push(/\sdata-i18n=/.test(attrs) || ['script', 'style', 'title'].includes(tag));
      }
    }
    expect(loose).toEqual([]);
    expect(html).not.toMatch(/placeholder="[^"]*[A-Za-z]{2,}\s+[A-Za-z]{2,}/);
  });

  it('el código: ningún texto para leer fuera de los logs internos', () => {
    const files = ['entrypoints', 'lib'].flatMap((dir) =>
      readdirSync(join(ROOT, dir), { recursive: true, encoding: 'utf8' })
        .filter((f) => f.endsWith('.ts'))
        .map((f) => join(dir, f)),
    );
    expect(files.length).toBeGreaterThan(5);
    const found: string[] = [];
    for (const file of files) {
      const source = ts.createSourceFile(file, read(file), ts.ScriptTarget.Latest, true);
      const visit = (node: ts.Node): void => {
        if (isConsoleCall(node) || ts.isImportDeclaration(node)) return; // logs internos
        const text = literalText(node);
        if (text !== null && PROSE.test(text)) found.push(`${file}: ${text}`);
        ts.forEachChild(node, visit);
      };
      visit(source);
    }
    expect(found).toEqual([]);
  });
});

function isConsoleCall(node: ts.Node): boolean {
  return (
    ts.isCallExpression(node) &&
    ts.isPropertyAccessExpression(node.expression) &&
    ts.isIdentifier(node.expression.expression) &&
    node.expression.expression.text === 'console'
  );
}

function literalText(node: ts.Node): string | null {
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text;
  if (ts.isTemplateExpression(node)) {
    return [node.head.text, ...node.templateSpans.map((s) => s.literal.text)].join(' ');
  }
  return null;
}
