import { CAPTURE_SOURCE, matchOp } from '../lib/capture';

// Mundo MAIN (Chrome 95+/FF 128+): parchea fetch/XHR para clonar las respuestas
// GraphQL que x.com YA descarga y reenviarlas al mundo ISOLATED por postMessage.
// MAIN no tiene acceso a browser.*/chrome.* (de ahí el bridge). NUNCA debe romper
// el fetch de la página: todo lo de captura va en try/catch y sobre un clon.

function urlOf(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input;
  if (input instanceof Request) return input.url;
  return String(input);
}

function emit(op: string, url: string, data: unknown): void {
  window.postMessage({ source: CAPTURE_SOURCE, op, url, data }, '*');
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
