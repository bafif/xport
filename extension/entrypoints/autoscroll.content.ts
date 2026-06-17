import { browser } from 'wxt/browser';

import {
  AUTOSCROLL_MSG,
  type AutoscrollMsg,
  AUTOSTART_SETTLE_MS,
  blankBackoff,
  type CrawlState,
  MAX_LOADING_WAITS,
  MAX_RETRIES,
  MAX_STALE,
  MAX_STEPS,
  matchesSearch,
  nextUntil,
  reachedSince,
  RETRY_WAIT_MS,
  SCROLL_DELAY_MS,
  searchUrl,
} from '../lib/autoscroll';
import { STORAGE_CRAWL, STORAGE_STATUS } from '../lib/capture';

// Mundo ISOLATED: conduce el crawl adaptativo (patrón C). Scrollea la búsqueda hasta
// el fondo disparando los fetches de SearchTimeline que captura-main intercepta y,
// cuando la página deja de crecer, DECIDE: ¿alcanzó `since`? → terminó; ¿se frenó
// antes (la pared de ~1000 / un error)? → encoge `until` al día del tweet más viejo
// cargado y re-busca más viejo. Tolera carga lenta (spinner) y errores (botón
// Reintentar), y recarga ante carga en blanco. NO captura ni interpreta tweets: solo
// lee del DOM señales de NAVEGACIÓN (el backend sigue siendo el único que mapea).
export default defineContentScript({
  matches: ['*://x.com/*', '*://twitter.com/*'],
  main() {
    let running = false;
    let userStopped = false;

    browser.runtime.onMessage.addListener((msg: unknown): void => {
      const m = msg as Partial<AutoscrollMsg> | null;
      if (!m || m.type !== AUTOSCROLL_MSG) return;
      if (m.action === 'stop') {
        userStopped = true;
        running = false;
        void browser.storage.local.remove(STORAGE_CRAWL); // detener = no narrowar más
      } else if (m.action === 'start' && !running) {
        void maybeStart();
      }
    });

    // Auto-arranque: si hay un crawl pendiente y ESTA página es su búsqueda, scrolleamos
    // solos tras dejar renderizar. Así el popup puede cerrarse: el crawl no depende de él.
    void maybeStart();

    const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

    async function readCrawl(): Promise<CrawlState | null> {
      return ((await browser.storage.local.get(STORAGE_CRAWL))[STORAGE_CRAWL] ??
        null) as CrawlState | null;
    }
    async function writeCrawl(s: CrawlState): Promise<void> {
      await browser.storage.local.set({ [STORAGE_CRAWL]: s });
    }
    async function endCrawl(notice?: string): Promise<void> {
      await browser.storage.local.remove(STORAGE_CRAWL);
      if (notice) await browser.storage.local.set({ [STORAGE_STATUS]: notice });
    }

    async function maybeStart(): Promise<void> {
      if (running) return;
      const state = await readCrawl();
      if (!state) return;
      // Solo arrancamos si esta pestaña ES la búsqueda en curso (no secuestramos otra).
      if (!matchesSearch(location.href, state)) return;
      await sleep(AUTOSTART_SETTLE_MS);
      if (!running) void run(state);
    }

    function height(): number {
      return document.documentElement.scrollHeight;
    }

    // --- Señales del DOM (solo para NAVEGAR; el backend interpreta el tweet) -----------
    const primary = (): Element =>
      document.querySelector('[data-testid="primaryColumn"]') ?? document.body;

    /** Día (YYYY-MM-DD) del tweet de NIVEL-TOPE más viejo renderizado = fondo del
     *  timeline (presente: scrolleamos al fondo). `null` si no hay ninguno. NO
     *  interpreta el tweet: lee el `datetime` que x.com ya renderizó, solo para fijar
     *  la próxima cota superior. Toma el PRIMER `<time>` de cada `article` (el del
     *  header del tweet va antes que el del quote embebido en el DOM): así un quote a
     *  un tweet viejo NO envenena el cursor con su fecha. */
    function oldestRenderedDate(): string | null {
      const tweets = document.querySelectorAll<HTMLElement>('article[data-testid="tweet"]');
      const articles = tweets.length ? tweets : document.querySelectorAll<HTMLElement>('article');
      let min: string | null = null;
      for (const a of articles) {
        const dt = a.querySelector<HTMLTimeElement>('time[datetime]')?.getAttribute('datetime');
        if (dt && (min === null || dt < min)) min = dt;
      }
      return min ? min.slice(0, 10) : null;
    }
    function isLoading(): boolean {
      return primary().querySelector('[role="progressbar"]') !== null;
    }
    function retryButton(): HTMLElement | null {
      for (const b of primary().querySelectorAll<HTMLElement>('[role="button"], button')) {
        if (/^(retry|reintentar|intentar de nuevo|try again)$/i.test((b.textContent ?? '').trim())) {
          return b;
        }
      }
      return null;
    }
    function hasEmptyState(): boolean {
      if (primary().querySelector('[data-testid="empty_state_header_text"]')) return true;
      return /no results for|no hay resultados/i.test(primary().textContent ?? '');
    }

    async function run(state: CrawlState): Promise<void> {
      running = true;
      userStopped = false;
      console.debug('[xport] crawl', state.since, '→', state.until);
      let lastHeight = -1;
      let stale = 0;
      let steps = 0;
      let retries = 0;
      let loadingWaits = 0;
      while (running && steps < MAX_STEPS) {
        window.scrollTo(0, height());
        await sleep(SCROLL_DELAY_MS);
        steps += 1;
        const h = height();
        if (h > lastHeight) {
          lastHeight = h; // creció: progreso real
          stale = retries = loadingWaits = 0;
          continue;
        }
        if (isLoading() && loadingWaits < MAX_LOADING_WAITS) {
          loadingWaits += 1; // spinner: x.com sigue trayendo (conexión lenta) → esperar
          continue;
        }
        loadingWaits = 0;
        const retry = retryButton();
        if (retry && retries < MAX_RETRIES) {
          retries += 1; // x.com mostró un error de paginación → reintentar (transitorio se recupera)
          retry.click();
          await sleep(RETRY_WAIT_MS);
          continue;
        }
        stale += 1;
        if (stale >= MAX_STALE) break; // asentado: ni crece, ni carga, ni hay reintento
      }
      running = false;
      if (!userStopped) await onSettled(state);
    }

    async function onSettled(state: CrawlState): Promise<void> {
      // Releemos el estado: si "Detener" lo borró (o ya cambió) entre el fin del loop y
      // acá, no decidimos nada (cierra la carrera stop ↔ fin-de-ventana).
      const cur = await readCrawl();
      if (!cur || cur.account !== state.account || cur.until !== state.until) return;

      const oldest = oldestRenderedDate();
      if (oldest === null) {
        // No se renderizó NINGÚN tweet.
        if (hasEmptyState()) {
          await endCrawl(); // x.com dice "sin resultados": rango realmente vacío → terminamos
          console.debug('[xport] sin resultados para', state.since, '→', state.until);
          return;
        }
        // Carga en blanco sin "sin resultados" → error / red caída / rate-limit de x.com.
        // Reintentamos INDEFINIDAMENTE con backoff (no nos rendimos: "no carga nada" es lo
        // que hay que aguantar). Esto NO es un día saturado (ese SÍ carga ~1000; se saltea
        // en la reanudación). Solo "Detener" corta.
        const attempt = (state.attempt ?? 0) + 1;
        await writeCrawl({ ...state, attempt });
        const wait = blankBackoff(attempt);
        console.warn('[xport] búsqueda en blanco (intento', attempt, ') → recarga en', wait, 'ms');
        await sleep(wait);
        // Re-chequeo tras la espera: si "Detener" borró/cambió el crawl mientras esperábamos,
        // no recargamos.
        const still = await readCrawl();
        if (still && still.account === state.account && still.until === state.until) {
          location.reload();
        }
        return;
      }
      if (reachedSince(oldest, state.since)) {
        await endCrawl(); // llegamos al inicio del rango: cubierto entero
        console.debug('[xport] crawl completo (alcanzó since)');
        return;
      }
      // Se frenó antes de `since` (pared/error duro): narrowamos `until` al día más viejo
      // cargado y seguimos hacia atrás. Persistimos ANTES de navegar (sin `attempt`: se
      // resetea). `until` decrece estrictamente → el crawl termina.
      const until = nextUntil(oldest, state.until);
      console.debug('[xport] pared a', oldest, '→ narrow until', until);
      await writeCrawl({ account: state.account, since: state.since, until });
      location.href = searchUrl(state.account, state.since, until);
    }
  },
});
