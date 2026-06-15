import { browser } from 'wxt/browser';

import { AUTOSCROLL_MSG, type AutoscrollMsg, SCROLL_DELAY_MS, shouldStop } from '../lib/autoscroll';

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
