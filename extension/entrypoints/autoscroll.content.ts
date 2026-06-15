import { browser } from 'wxt/browser';

import {
  AUTOSCROLL_MSG,
  type AutoscrollMsg,
  AUTOSTART_SETTLE_MS,
  SCROLL_DELAY_MS,
  shouldStop,
} from '../lib/autoscroll';
import { STORAGE_AUTOSTART } from '../lib/capture';

// Mundo ISOLATED: scrollea la página hasta el fondo en loop para disparar los fetches
// de SearchTimeline que captura-main intercepta. Lo arranca/detiene el popup por
// runtime message. Corta solo cuando la altura deja de crecer (fin de resultados) o
// al llegar al tope de pasos. NO captura nada por sí mismo: solo conduce el scroll.
export default defineContentScript({
  matches: ['*://x.com/*', '*://twitter.com/*'],
  main() {
    let running = false;

    browser.runtime.onMessage.addListener((msg: unknown): void => {
      const m = msg as Partial<AutoscrollMsg> | null;
      if (!m || m.type !== AUTOSCROLL_MSG) return;
      if (m.action === 'stop') {
        running = false;
      } else if (m.action === 'start' && !running) {
        void run();
      }
    });

    // Auto-start: el popup abrió esta búsqueda con el flag prendido. Lo consumimos
    // (una sola vez) y arrancamos solos tras dejar que la página renderice los primeros
    // resultados. Así el popup puede cerrarse al abrir la pestaña: no depende de él.
    void maybeAutostart();

    async function maybeAutostart(): Promise<void> {
      const flag = (await browser.storage.local.get(STORAGE_AUTOSTART))[STORAGE_AUTOSTART];
      if (flag !== true) return;
      await browser.storage.local.set({ [STORAGE_AUTOSTART]: false }); // consumir
      await new Promise((r) => setTimeout(r, AUTOSTART_SETTLE_MS));
      if (!running) void run();
    }

    function height(): number {
      return document.documentElement.scrollHeight;
    }

    async function run(): Promise<void> {
      running = true;
      let lastHeight = -1;
      let stale = 0;
      let steps = 0;
      while (running && !shouldStop(stale, steps)) {
        window.scrollTo(0, height());
        await new Promise((r) => setTimeout(r, SCROLL_DELAY_MS));
        const h = height();
        stale = h <= lastHeight ? stale + 1 : 0;
        lastHeight = h;
        steps += 1;
      }
      running = false;
    }
  },
});
