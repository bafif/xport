import { browser } from 'wxt/browser';

import { CAPTURE_SOURCE, type CaptureMsg } from '../lib/capture';

// Mundo ISOLATED: recibe los postMessage del patch MAIN (mismo `window`) y los
// reenvía al background por runtime.sendMessage (MAIN no puede; este sí).
export default defineContentScript({
  matches: ['*://x.com/*', '*://twitter.com/*'],
  main() {
    window.addEventListener('message', (event: MessageEvent) => {
      if (event.source !== window) return;
      const data = event.data as Partial<CaptureMsg> | null;
      if (!data || data.source !== CAPTURE_SOURCE) return;
      void browser.runtime.sendMessage(data).catch(() => {});
    });
  },
});
