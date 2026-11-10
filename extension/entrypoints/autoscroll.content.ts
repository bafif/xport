import { browser } from 'wxt/browser';

import {
  AUTOSCROLL_MSG,
  type AutoscrollMsg,
  AUTOSTART_SETTLE_MS,
  blankBackoff,
  type CrawlState,
  crawlUrl,
  decideStep,
  epochSeconds,
  FRESH_COUNTERS,
  isCaptureSearch,
  isStaleSearch,
  MAX_STEPS,
  matchesSearch,
  narrowed,
  nextBound,
  pausedNotice,
  reachedSince,
  RETRY_WAIT_MS,
  SCROLL_DELAY_MS,
  showSeconds,
  sinceTime,
  untilTime,
} from '../lib/autoscroll';
import { STORAGE_CRAWL, STORAGE_PAUSED, STORAGE_STATUS } from '../lib/capture';
import { CLAIM_MSG, CRAWL_MSG, type CrawlEvent, type CrawlMsg } from '../lib/queue';
import { decideRate, type RateInfo, rateFromMsg, type RateMsg, waitingNotice } from '../lib/rate';

// Mundo ISOLATED: conduce el crawl adaptativo (patrón C). Scrollea la búsqueda hasta
// el fondo disparando los fetches de SearchTimeline que captura-main intercepta y,
// cuando la página deja de crecer, DECIDE: ¿alcanzó `since`? → terminó; ¿se frenó
// antes (la pared de ~1000 / un error)? → encoge la cota al segundo del tweet más viejo
// cargado y re-busca más viejo; ¿no trajo nada más viejo que ese tweet? → terminó. Tolera
// carga lenta (spinner) y errores (botón Reintentar), y recarga ante carga en blanco. Con la
// pestaña oculta se pone en pausa (ver `decideStep`). Ante el rate limit de X afloja el
// ritmo o espera al reset (lib/rate.ts). NO captura ni interpreta tweets: solo lee del DOM
// señales de NAVEGACIÓN y del parche de `fetch` el status y los encabezados de la búsqueda
// (el backend sigue siendo el único que mapea).
export default defineContentScript({
  matches: ['*://x.com/*', '*://twitter.com/*'],
  main() {
    let running = false;
    let userStopped = false;
    // Pausa por pestaña oculta y esperas de ritmo: `wake` despierta la espera (al volver a
    // la vista o al detener) y `visibilityChanges` delata si la pestaña se ocultó en medio
    // de un paso.
    let wake: (() => void) | null = null;
    let visibilityChanges = 0;
    document.addEventListener('visibilitychange', () => {
      visibilityChanges += 1;
      if (!document.hidden) wake?.();
    });

    // Lo último que dijo X del cupo de búsquedas (status y `x-rate-limit-*`), que manda el
    // parche de `fetch` del mundo MAIN.
    let lastRate: RateInfo | null = null;
    window.addEventListener('message', (event: MessageEvent) => {
      if (event.source !== window || event.origin !== location.origin) return;
      const info = rateFromMsg(event.data as Partial<RateMsg> | null, Date.now());
      if (info) lastRate = info;
    });

    browser.runtime.onMessage.addListener((msg: unknown): Promise<boolean> | undefined => {
      const m = msg as Partial<AutoscrollMsg> | null;
      if (!m || m.type !== AUTOSCROLL_MSG) return undefined;
      if (m.action === 'ping') {
        // ¿Esta pestaña sigue en una búsqueda de captura? Si sí, el background la reusa.
        return Promise.resolve(isCaptureSearch(location.href));
      }
      if (m.action === 'stale') {
        // ¿Esta pestaña quedó en una búsqueda vieja del crawl? Si sí, el background la
        // lleva a la actual (si no, el crawl no arranca acá y la captura queda trabada).
        return readCrawl().then((s) => s !== null && isStaleSearch(location.href, s));
      }
      if (m.action === 'stop') {
        userStopped = true;
        running = false;
        wake?.();
        void browser.storage.local.remove(STORAGE_CRAWL); // detener = no narrowar más
      } else if (m.action === 'start' && !running) {
        void maybeStart();
      }
      return undefined;
    });

    // Corte desde afuera: el background borra el crawl cuando el gate se llenó o el
    // servicio no registra lo capturado (regla 1 de docs/PLAN.md), y el popup lo borra
    // con «Detener» aunque la pestaña activa sea otra. Sin crawl no se scrollea más.
    browser.storage.onChanged.addListener((changes, area): void => {
      if (area !== 'local' || !(STORAGE_CRAWL in changes)) return;
      if (changes[STORAGE_CRAWL].newValue === undefined && running) {
        userStopped = true; // no decidir nada al asentarse: no narrowar ni navegar
        running = false;
        wake?.();
      }
    });

    // Auto-arranque: si hay un crawl pendiente y ESTA página es su búsqueda, scrolleamos
    // solos tras dejar renderizar. Así el popup puede cerrarse: el crawl no depende de él.
    void maybeStart();

    const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

    /** Como `sleep`, pero se corta al detener el crawl o al volver la pestaña a la vista. */
    function nap(ms: number): Promise<void> {
      return new Promise((resolve) => {
        const done = (): void => {
          clearTimeout(timer);
          wake = null;
          resolve();
        };
        const timer = setTimeout(done, ms);
        wake = done;
      });
    }

    async function readCrawl(): Promise<CrawlState | null> {
      return ((await browser.storage.local.get(STORAGE_CRAWL))[STORAGE_CRAWL] ??
        null) as CrawlState | null;
    }
    async function writeCrawl(s: CrawlState): Promise<void> {
      await browser.storage.local.set({ [STORAGE_CRAWL]: s });
    }
    async function endCrawl(notice?: string): Promise<void> {
      await browser.storage.local.remove([STORAGE_CRAWL, STORAGE_PAUSED]);
      if (notice) await browser.storage.local.set({ [STORAGE_STATUS]: notice });
    }
    /** ¿Sigue siendo el mismo crawl, en la misma búsqueda? Si «Detener» lo borró o ya
     *  cambió, no se decide nada (cierra la carrera stop ↔ fin de la búsqueda). */
    function sameCrawl(cur: CrawlState | null, state: CrawlState): cur is CrawlState {
      return cur !== null && cur.account === state.account && untilTime(cur) === untilTime(state);
    }

    /** Le avisa al background qué pasó con el crawl. Espera la respuesta: así el servicio
     *  sabe que terminó antes de que se borre el crawl y la próxima vuelta de la cola no la
     *  vuelva a arrancar. Sin cola solo importa la espera a X: la pestaña la estaciona y la
     *  retoma el background. */
    async function tell(state: CrawlState, event: CrawlEvent, resumeAt?: number): Promise<void> {
      if (state.captureId === undefined && event !== 'waiting') return;
      const msg: CrawlMsg = { type: CRAWL_MSG, event, captureId: state.captureId, resumeAt };
      await browser.runtime.sendMessage(msg).catch(() => {});
    }

    /** El rango quedó cubierto: la captura terminó. */
    async function finish(state: CrawlState): Promise<void> {
      await tell(state, 'done');
      await endCrawl();
    }

    async function maybeStart(): Promise<void> {
      if (running) return;
      const state = await readCrawl();
      if (!state) return;
      // Esperando a X: la búsqueda la retoma el background cuando pase el reset.
      if (state.waitUntil !== undefined && state.waitUntil > Date.now()) return;
      // Solo arrancamos si esta pestaña ES la búsqueda en curso (no secuestramos otra).
      if (!matchesSearch(location.href, state)) return;
      // Y si no hay otra pestaña crawleando la misma captura (el navegador restauró una y el
      // background reabrió otra). Sin respuesta del background, arranca igual.
      const mine: unknown = await browser.runtime.sendMessage({ type: CLAIM_MSG }).catch(() => true);
      if (mine === false) return;
      await sleep(AUTOSTART_SETTLE_MS);
      if (!running) void run(state);
    }

    function height(): number {
      return document.documentElement.scrollHeight;
    }

    // --- Señales del DOM (solo para NAVEGAR; el backend interpreta el tweet) -----------
    const primary = (): Element =>
      document.querySelector('[data-testid="primaryColumn"]') ?? document.body;

    /** Segundo (UTC) del tweet de NIVEL-TOPE más viejo renderizado = fondo del timeline
     *  (presente: scrolleamos al fondo). `null` si no hay ninguno. NO interpreta el tweet:
     *  lee el `datetime` que x.com ya renderizó, solo para fijar la próxima cota superior.
     *  Toma el PRIMER `<time>` de cada `article` (el del header del tweet va antes que el del
     *  quote embebido en el DOM): así un quote a un tweet viejo NO envenena el cursor con su
     *  fecha. */
    function oldestRenderedTime(): number | null {
      const tweets = document.querySelectorAll<HTMLElement>('article[data-testid="tweet"]');
      const articles = tweets.length ? tweets : document.querySelectorAll<HTMLElement>('article');
      let min: number | null = null;
      for (const a of articles) {
        const dt = a.querySelector<HTMLTimeElement>('time[datetime]')?.getAttribute('datetime');
        const s = dt ? epochSeconds(dt) : null;
        if (s !== null && (min === null || s < min)) min = s;
      }
      return min;
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

    /** Espera, sin timers (Chrome los frena en una pestaña oculta), a que la pestaña
     *  vuelva a la vista o a que se detenga el crawl. Mientras, el popup muestra el aviso. */
    async function pauseWhileHidden(state: CrawlState): Promise<void> {
      console.debug('[xport] pestaña oculta: crawl en pausa');
      await browser.storage.local.set({ [STORAGE_PAUSED]: pausedNotice(state.account) });
      await tell(state, 'paused');
      await new Promise<void>((resolve) => {
        wake = resolve;
        if (!document.hidden || !running) resolve();
      });
      wake = null;
      await browser.storage.local.remove(STORAGE_PAUSED);
      if (running) await tell(state, 'resumed');
      console.debug('[xport] pestaña a la vista: sigue el crawl');
    }

    async function run(state: CrawlState): Promise<void> {
      running = true;
      userStopped = false;
      console.debug('[xport] crawl', state.since, '→', showSeconds(untilTime(state)));
      let lastHeight = -1;
      let steps = 0;
      let counters = FRESH_COUNTERS;
      let pacedFor = -1; // la respuesta de X por la que ya se aflojó el ritmo
      while (running && steps < MAX_STEPS) {
        if (document.hidden) {
          await pauseWhileHidden(state);
          continue;
        }
        // Antes de pedir otra página: si X ya dijo que no hay cupo, se deja de scrollear (lo
        // resuelve `onSettled`); si quedan pocas búsquedas, se reparten hasta el reset, con
        // una espera por cada respuesta de X (no por scroll: muchos no piden nada).
        const rate = decideRate(lastRate, Date.now());
        if (rate.kind === 'wait') break;
        if (rate.kind === 'slow' && lastRate && lastRate.at !== pacedFor) {
          pacedFor = lastRate.at;
          console.debug('[xport] quedan', lastRate.remaining, 'búsquedas: espero', rate.delayMs, 'ms');
          await nap(rate.delayMs);
          if (!running) break;
          if (document.hidden) continue;
        }
        const seen = visibilityChanges;
        window.scrollTo(0, height());
        await sleep(SCROLL_DELAY_MS);
        steps += 1;
        const h = height();
        const retry = retryButton();
        const step = decideStep(
          {
            hidden: document.hidden || visibilityChanges !== seen,
            grew: h > lastHeight,
            loading: isLoading(),
            retry: retry !== null,
          },
          counters,
        );
        counters = step.counters;
        if (step.action === 'grew') {
          lastHeight = h; // creció: progreso real
        } else if (step.action === 'retry') {
          if (decideRate(lastRate, Date.now()).kind === 'wait') break; // reintentar sería otro 429
          retry?.click(); // x.com mostró un error de paginación → reintentar
          await sleep(RETRY_WAIT_MS);
        } else if (step.action === 'settled') {
          break; // asentado: ni crece, ni carga, ni hay reintento
        }
        // 'pause': la próxima vuelta espera a que la pestaña vuelva; 'wait-loading' y
        // 'stale': scrollear de nuevo.
      }
      running = false;
      if (!userStopped) await onSettled(state);
    }

    /** X frenó la búsqueda: se guarda hasta dónde se llegó y hasta cuándo esperar, y el
     *  background estaciona la pestaña (x.com deja de reintentar) y la retoma después. */
    async function waitForX(cur: CrawlState, untilMs: number): Promise<void> {
      const oldest = oldestRenderedTime();
      if (oldest !== null && reachedSince(oldest, sinceTime(cur))) {
        await finish(cur); // justo llegó al inicio del rango
        return;
      }
      // Lo cargado hasta acá no se vuelve a pedir: la búsqueda siguiente arranca en el tweet
      // más viejo que se vio (re-incluido). Sin tweets, la misma búsqueda.
      const bound = oldest === null ? untilTime(cur) : Math.min(untilTime(cur), oldest + 1);
      const next: CrawlState = { ...narrowed(cur, bound), waitUntil: untilMs };
      console.warn('[xport] X frenó la búsqueda: espero hasta', new Date(untilMs).toISOString());
      await writeCrawl(next);
      await browser.storage.local.set({ [STORAGE_PAUSED]: waitingNotice(cur.account, untilMs) });
      await tell(next, 'waiting', untilMs);
    }

    async function onSettled(state: CrawlState): Promise<void> {
      const cur = await readCrawl();
      if (!sameCrawl(cur, state)) return;

      const rate = decideRate(lastRate, Date.now());
      if (rate.kind === 'wait') {
        await waitForX(cur, rate.untilMs);
        return;
      }

      const oldest = oldestRenderedTime();
      if (oldest === null) {
        // No se renderizó NINGÚN tweet.
        if (hasEmptyState()) {
          await finish(cur); // x.com dice "sin resultados": rango realmente vacío → terminamos
          console.debug('[xport] sin resultados hasta', showSeconds(untilTime(state)));
          return;
        }
        await reloadBlank(cur);
        return;
      }
      if (reachedSince(oldest, sinceTime(cur))) {
        await finish(cur); // llegamos al inicio del rango: cubierto entero
        console.debug('[xport] crawl completo (alcanzó since)');
        return;
      }
      const bound = nextBound(oldest, untilTime(cur));
      if (bound === null) {
        if (retryButton() !== null) {
          // No trajo nada más viejo, pero con x.com mostrando un error: no es el fin.
          await reloadBlank(cur);
          return;
        }
        // La búsqueda no trajo nada más viejo que el tweet que re-incluía: no hay más.
        await finish(cur);
        console.debug('[xport] crawl completo (nada antes de', showSeconds(oldest), ')');
        return;
      }
      // Se frenó antes de `since` (pared/error duro): la cota baja al segundo del tweet más
      // viejo cargado y seguimos hacia atrás. Persistimos ANTES de navegar (sin `attempt`:
      // se resetea). La cota decrece estrictamente → el crawl termina.
      const next = narrowed(cur, bound);
      console.debug('[xport] pared en', showSeconds(oldest), '→ sigo hasta', showSeconds(bound));
      await writeCrawl(next);
      location.href = crawlUrl(next);
    }

    /** Carga en blanco (o con error) sin un 429: error o red caída. Reintentamos
     *  INDEFINIDAMENTE con backoff (no nos rendimos: "no carga nada" es lo que hay que
     *  aguantar). Solo "Detener" corta. */
    async function reloadBlank(cur: CrawlState): Promise<void> {
      const attempt = (cur.attempt ?? 0) + 1;
      await writeCrawl({ ...cur, attempt });
      const wait = blankBackoff(attempt);
      console.warn('[xport] búsqueda en blanco (intento', attempt, ') → recarga en', wait, 'ms');
      await sleep(wait);
      // Re-chequeo tras la espera: si "Detener" borró/cambió el crawl mientras esperábamos,
      // no recargamos.
      if (sameCrawl(await readCrawl(), cur)) location.reload();
    }
  },
});
