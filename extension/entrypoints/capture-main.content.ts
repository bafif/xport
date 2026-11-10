import { CAPTURE_SOURCE, matchOp } from '../lib/capture';
import { RATE_SOURCE, type RateMsg } from '../lib/rate';

// Mundo MAIN (Chrome 95+/FF 128+): parchea fetch/XHR para clonar las respuestas
// GraphQL que x.com YA descarga y reenviarlas al mundo ISOLATED por postMessage.
// MAIN no tiene acceso a browser.*/chrome.* (de ahí el bridge). NUNCA debe romper
// el fetch de la página: todo lo de captura va en try/catch y sobre un clon.
// Aparte, de cada respuesta capturable pasa el status y los encabezados `x-rate-limit-*`
// (HTTP, no GraphQL) para que el crawl espere a X en vez de insistir (lib/rate.ts).

function urlOf(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input;
  if (input instanceof Request) return input.url;
  return String(input);
}

function emit(op: string, url: string, data: unknown): void {
  // targetOrigin acotado al propio origen (el bridge está en el mismo documento): no
  // hace falta difundir a '*'.
  window.postMessage({ source: CAPTURE_SOURCE, op, url, data }, location.origin);
}

function emitRate(op: string, status: number, header: (name: string) => string | null): void {
  const msg: RateMsg = {
    source: RATE_SOURCE,
    op,
    status,
    remaining: header('x-rate-limit-remaining'),
    reset: header('x-rate-limit-reset'),
  };
  window.postMessage(msg, location.origin);
}

interface TaggedXHR extends XMLHttpRequest {
  __xportUrl?: string;
}

export default defineContentScript({
  matches: ['*://x.com/*', '*://twitter.com/*'],
  world: 'MAIN',
  runAt: 'document_start',
  main() {
    const origFetch = window.fetch;
    window.fetch = async (...args: Parameters<typeof fetch>): Promise<Response> => {
      const res = await origFetch(...args);
      try {
        const url = urlOf(args[0]);
        const op = matchOp(url);
        if (op) {
          emitRate(op, res.status, (name) => res.headers.get(name));
          res
            .clone()
            .json()
            .then((data: unknown) => emit(op, url, data))
            .catch(() => {});
        }
      } catch {
        /* la captura nunca tumba el fetch de la página */
      }
      return res;
    };

    const origOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (
      this: TaggedXHR,
      method: string,
      url: string | URL,
      ...rest: unknown[]
    ): void {
      this.__xportUrl = String(url);
      (origOpen as (...a: unknown[]) => void).apply(this, [method, url, ...rest]);
    } as typeof XMLHttpRequest.prototype.open;

    const origSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function (this: TaggedXHR, ...args: unknown[]): void {
      const url = this.__xportUrl;
      const op = url ? matchOp(url) : null;
      if (url && op) {
        this.addEventListener('load', () => {
          try {
            emitRate(op, this.status, (name) => this.getResponseHeader(name));
          } catch {
            /* sin encabezados: el crawl sigue sin saber del cupo */
          }
          try {
            emit(op, url, JSON.parse(this.responseText) as unknown);
          } catch {
            /* respuesta no-JSON: ignorar */
          }
        });
      }
      (origSend as (...a: unknown[]) => void).apply(this, args);
    } as typeof XMLHttpRequest.prototype.send;
  },
});
