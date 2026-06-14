import { browser } from 'wxt/browser';

import { CAPTURE_SOURCE, type CaptureMsg, matchOp } from '../lib/capture';

// Mundo ISOLATED: recibe los postMessage del patch MAIN (mismo `window`) y los
// reenvía al background por runtime.sendMessage (MAIN no puede; este sí).
// Nota: en el mundo MAIN cualquier script de la página puede emitir un postMessage;
// validamos origen + forma (defensa en profundidad). No es garantía fuerte —el backend
// parsea defensivo y todo va al store LOCAL del usuario—, pero filtra lo accidental/malformado.
export default defineContentScript({
  matches: ['*://x.com/*', '*://twitter.com/*'],
  main() {
    window.addEventListener('message', (event: MessageEvent) => {
      if (event.source !== window || event.origin !== location.origin) return;
      const data = event.data as Partial<CaptureMsg> | null;
      if (!data || data.source !== CAPTURE_SOURCE) return;
      if (typeof data.url !== 'string' || !matchOp(data.url)) return; // forma esperada
      void browser.runtime.sendMessage(data).catch(() => {});
    });
  },
});
