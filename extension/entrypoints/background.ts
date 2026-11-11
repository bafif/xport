import { browser } from 'wxt/browser';

import {
  type AccountCheck,
  answersCheck,
  checkStep,
  checkUrl,
  STORAGE_CHECK,
  userPayload,
} from '../lib/account';
import { type CaptureOut, type CaptureReport, XportClient } from '../lib/api';
import { AUTOSCROLL_MSG, crawlUrl, type CrawlState, narrowed, untilTime } from '../lib/autoscroll';
import {
  accountFromUrl,
  CAPTURE_SOURCE,
  type CaptureMsg,
  DEFAULT_BASE,
  STORAGE_BASE,
  STORAGE_CAPTURE,
  STORAGE_COUNTS,
  STORAGE_CRAWL,
  STORAGE_PAUSED,
  STORAGE_QUEUE_TAB,
  STORAGE_RUNNER,
  STORAGE_STATUS,
  screenNameFromUrl,
} from '../lib/capture';
import type { Note } from '../lib/i18n';
import { decideIngest, type IngestFlags, MAX_PAGES_PER_POST, stopNotice } from '../lib/ingest';
import {
  CLAIM_MSG,
  claimAllowed,
  CRAWL_MSG,
  type CrawlMsg,
  decideQueue,
  POLL_MINUTES,
  QUEUE_ALARM,
  QUEUE_MSG,
  reportedState,
  resumeBound,
  startPlan,
  type StartPlan,
  waitStep,
} from '../lib/queue';

// Relay del patrón C: recibe las capturas del bridge, las batchea por cuenta y las
// POSTea al FastAPI local (/ingest). El backend hace extract+map+store+gate. Si el
// servicio está caído o devuelve 5xx, re-encola (no pierde capturas); si /ingest
// rechaza con 4xx (429 over-cap, 400), NO reintenta (el backend ya registró el acceso o
// el lote no sirve). El buffer está acotado. El service worker queda mínimo.
//
// Regla 1 de docs/PLAN.md: la captura no puede seguir accediendo a tweets que el ledger
// del gate no llega a contar. Cuándo se corta el crawl lo decide `decideIngest`
// (lib/ingest.ts); cortarlo es borrar su estado, y el content script deja de scrollear.

const FLUSH_MS = 1500;
const MAX_PAGES_PER_ACCOUNT = 1000; // cota del buffer ante outage prolongado

interface IngestReply extends IngestFlags {
  account: string;
  saved: number;
  rejected: number;
}

export default defineBackground(() => {
  // Auto-arranque del backend: al cargar, el background abre un puerto de native
  // messaging que hace que el navegador lance el supervisor local (deploy/native-host),
  // que a su vez levanta el FastAPI y lo baja cuando se cierra el puerto. Mantener el
  // puerto abierto = mantener vivo el backend durante la sesión. Si el host no está
  // instalado, falla silencioso y se cae al server manual: los fetch a /ingest
  // reintentan igual (ver flush). Detalle: deploy/native-host/README.md.
  const NATIVE_HOST = 'com.xport.host';
  const MAX_NATIVE_RETRIES = 5;
  let nativePort: ReturnType<typeof browser.runtime.connectNative> | null = null;
  let nativeRetries = 0;

  function ensureBackend(): void {
    if (nativePort) return;
    let port: ReturnType<typeof browser.runtime.connectNative>;
    try {
      port = browser.runtime.connectNative(NATIVE_HOST);
    } catch (err) {
      console.warn('[xport] nativeMessaging no disponible (¿host instalado?)', err);
      return;
    }
    nativePort = port;
    const openedAt = Date.now();
    port.onDisconnect.addListener(() => {
      const msg = browser.runtime.lastError?.message ?? '';
      nativePort = null;
      // Conectado un rato → fue una caída real: reseteamos el backoff. Desconexión
      // instantánea → host ausente/mal configurado: backoff exponencial con tope.
      if (Date.now() - openedAt > 10_000) nativeRetries = 0;
      if (nativeRetries >= MAX_NATIVE_RETRIES) {
        console.warn('[xport] native host no disponible; instalá deploy/native-host o levantá el server a mano', msg);
        return;
      }
      const delay = 2_000 * 2 ** nativeRetries;
      nativeRetries += 1;
      console.warn('[xport] native host desconectado, reintento en', delay, 'ms', msg);
      setTimeout(ensureBackend, delay);
    });
    console.debug('[xport] native host conectado: backend local auto-arrancando');
  }

  ensureBackend();

  const buffers = new Map<string, unknown[]>(); // account -> páginas crudas
  let timer: ReturnType<typeof setTimeout> | null = null;
  let flushing = false; // evita flushes solapados (POSTs concurrentes + races de storage)

  browser.runtime.onMessage.addListener((msg: unknown): void => {
    const m = msg as Partial<CaptureMsg> | null;
    if (!m || m.source !== CAPTURE_SOURCE || typeof m.url !== 'string') return;
    if (m.op === 'UserByScreenName') {
      // No trae tweets: solo sirve si una comprobación de cuenta la espera (punto 10).
      const screenName = screenNameFromUrl(m.url);
      const data = m.data;
      void serial(() => onUserLookup(screenName, data));
      return;
    }
    const account = accountFromUrl(m.url);
    if (!account) return; // MVP: solo capturas de búsqueda from:user
    const buf = buffers.get(account) ?? [];
    if (buf.length >= MAX_PAGES_PER_ACCOUNT) return; // outage: no crecer sin límite
    buf.push(m.data);
    buffers.set(account, buf);
    console.debug('[xport] captura recibida:', account, '(buffer', buf.length, ')');
    schedule();
  });

  function schedule(): void {
    if (timer === null) timer = setTimeout(() => void flush(), FLUSH_MS);
  }

  function requeue(account: string, pages: unknown[]): void {
    // Reintento: lo que falló va primero, luego lo nuevo; acotado por la cota.
    buffers.set(account, [...pages, ...(buffers.get(account) ?? [])].slice(0, MAX_PAGES_PER_ACCOUNT));
  }

  async function flush(): Promise<void> {
    timer = null;
    if (flushing) {
      schedule(); // ya hay un flush en curso: reintentar tras él (sin solaparse)
      return;
    }
    flushing = true;
    try {
      if (!(await captureEnabled())) {
        buffers.clear(); // captura apagada: descartar lo buffereado
        return;
      }
      const base = await getBase();
      for (const account of [...buffers.keys()]) {
        const all = buffers.get(account) ?? [];
        const pages = all.slice(0, MAX_PAGES_PER_POST);
        const rest = all.slice(MAX_PAGES_PER_POST); // van en el próximo flush
        if (rest.length > 0) buffers.set(account, rest);
        else buffers.delete(account);
        let status = 0; // 0 = el servicio no respondió
        let reply: IngestReply | null = null;
        try {
          const res = await fetch(`${base}/ingest`, {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ account, op: 'SearchTimeline', pages }),
          });
          status = res.status;
          if (res.ok) {
            reply = (await res.json()) as IngestReply;
            await bumpCount(reply.account, reply.saved);
            console.debug(
              '[xport] ingest OK', account, '→ saved', reply.saved, 'rejected', reply.rejected,
              'over_cap', reply.over_cap,
            );
          }
        } catch (err) {
          console.warn('[xport] servicio no responde en', base, err);
        }
        const pending = pages.length + (buffers.get(account)?.length ?? 0);
        const decision = decideIngest(status, reply, pending);
        if (decision.requeue) {
          console.warn('[xport] ingest', status || 'sin respuesta', '→ reintento');
          requeue(account, pages);
        } else if (status && !(status >= 200 && status < 300)) {
          console.warn('[xport] ingest rechazado', status, '(no se reintenta)');
        }
        if (decision.stop) await stopCrawl(stopNotice(decision.stop, base, status));
      }
    } finally {
      flushing = false;
    }
    if (buffers.size > 0) schedule();
  }

  /** Corta el crawl en curso: sin su estado, el content script deja de scrollear y no
   *  narrowa ni navega más. El aviso lo muestra el popup la próxima vez que se abre, y si
   *  es de la cola, el servicio la marca cortada y lo notifica. */
  async function stopCrawl(notice: Note): Promise<void> {
    const had = await readCrawl();
    if (!had) return; // no había crawl (captura manual): nada que cortar
    console.warn('[xport] corto el crawl:', notice.key);
    await browser.storage.local.remove([STORAGE_CRAWL, STORAGE_PAUSED]);
    await browser.storage.local.set({ [STORAGE_STATUS]: notice });
    if (had.captureId !== undefined) {
      await report(had.captureId, 'stopped', notice);
    }
  }

  async function readCrawl(): Promise<CrawlState | null> {
    return ((await browser.storage.local.get(STORAGE_CRAWL))[STORAGE_CRAWL] ??
      null) as CrawlState | null;
  }

  async function client(): Promise<XportClient> {
    const runner = (await browser.storage.local.get(STORAGE_RUNNER))[STORAGE_RUNNER];
    return new XportClient(await getBase(), typeof runner === 'string' ? runner : undefined);
  }

  /** Le avisa al servicio el estado de una captura. Devuelve si lo aceptó. */
  async function report(
    id: number,
    state: 'running' | 'paused' | 'waiting' | 'done' | 'stopped',
    note?: Note,
    resumeAt?: number,
    user?: Record<string, unknown>,
  ): Promise<boolean> {
    const body: CaptureReport = { state };
    if (note) body.note = note;
    if (resumeAt !== undefined) body.resume_at = new Date(resumeAt).toISOString();
    if (user) body.user = user;
    try {
      await (await client()).captureState(id, body);
      return true;
    } catch (err) {
      console.warn('[xport] no pude avisarle al servicio el estado de la captura', id, err);
      return false;
    }
  }

  // --- Cola de capturas (punto 7 de docs/PLAN.md) --------------------------------------
  // Cada medio minuto, al arrancar y cuando el popup encola, el background mira la cola:
  // si no hay crawl, arranca la que diga el servicio; si hay, sigue mientras el servicio
  // diga que es la que va. Todo pasa en fila (`serial`): una vuelta de la cola y el aviso
  // de que un crawl terminó no se pisan.

  let chain: Promise<unknown> = Promise.resolve();
  function serial<T>(fn: () => Promise<T>): Promise<T> {
    const run = chain.then(fn, fn);
    chain = run.catch(() => undefined);
    return run;
  }

  void browser.alarms.create(QUEUE_ALARM, { periodInMinutes: POLL_MINUTES });
  // El servicio le deja la marca a esta extensión apenas carga su navegador sin ventana: con
  // la marca, mirar la cola en el momento en vez de esperar la alarma.
  browser.storage.onChanged.addListener((changes, area) => {
    if (area === 'local' && STORAGE_RUNNER in changes) void serial(pollQueue);
  });
  browser.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === QUEUE_ALARM) void serial(pollQueue);
  });
  void serial(pollQueue);

  browser.runtime.onMessage.addListener((msg: unknown, sender: { tab?: { id?: number } }) => {
    const m = msg as { type?: unknown } | null;
    if (m?.type === QUEUE_MSG) return serial(pollQueue);
    if (m?.type === CRAWL_MSG) return serial(() => onCrawlEvent(msg as CrawlMsg, sender.tab?.id));
    if (m?.type === CLAIM_MSG) return serial(() => claim(sender.tab?.id));
    return undefined; // otros mensajes: no respondemos
  });

  async function pollQueue(): Promise<void> {
    if (!(await captureEnabled())) return; // captura apagada: no se toma trabajo
    const check = await readCheck();
    switch (checkStep(check, Date.now())) {
      case 'waiting':
        return; // la cola sigue cuando conteste x.com o pase el plazo
      case 'expired':
        if (check) {
          console.warn('[xport] x.com no dijo nada de @' + check.account + ': termino sin mirar la cuenta');
          await finishCheck(check);
        }
        break;
      case 'none':
        break;
    }
    const api = await client();
    let next: CaptureOut | null;
    try {
      next = await api.nextCapture();
    } catch {
      return; // sin servicio: no se arranca nada (regla 1 de docs/PLAN.md)
    }
    const crawl = await readCrawl();
    switch (decideQueue(crawl, next)) {
      case 'start':
        if (next) await startCapture(api, next);
        return;
      case 'keep':
        if (crawl) await keepCrawl(crawl);
        return;
      case 'stop-crawl':
        console.debug('[xport] la captura ya no va (cancelada o terminada): corto el crawl');
        await browser.storage.local.remove([STORAGE_CRAWL, STORAGE_PAUSED]);
        return;
      case 'idle':
        return;
    }
  }

  async function startCapture(api: XportClient, cap: CaptureOut): Promise<void> {
    let plan: StartPlan;
    try {
      const prog = await api.progress({ account: cap.account, since: cap.since, until: cap.until });
      plan = startPlan(prog, cap.since, cap.until);
    } catch {
      return; // la próxima vuelta reintenta
    }
    if (plan.kind === 'done') {
      await report(cap.id, 'done'); // ya estaba todo: el servicio exporta y avisa
      return;
    }
    const crawl: CrawlState = {
      account: cap.account,
      since: cap.since,
      until: cap.until,
      untilTime: plan.untilTime,
      captureId: cap.id,
    };
    console.debug('[xport] arranco la captura', cap.id, `@${cap.account}`, crawlUrl(crawl));
    await browser.storage.local.remove(STORAGE_PAUSED);
    await browser.storage.local.set({ [STORAGE_CRAWL]: crawl, [STORAGE_STATUS]: plan.message });
    await openSearch(crawl);
  }

  /** Abre la búsqueda del crawl. Reusa la pestaña de la captura anterior si sigue en una
   *  búsqueda de captura (así no se apilan pestañas); si no, abre otra. */
  async function openSearch(crawl: CrawlState): Promise<void> {
    const url = crawlUrl(crawl);
    // La pestaña donde se miró la cuenta de la captura anterior es nuestra si sigue ahí, aunque
    // ya no esté en una búsqueda.
    const check = await readCheck();
    if (check?.finished) {
      await browser.storage.local.remove(STORAGE_CHECK);
      try {
        const here: unknown = await browser.tabs.sendMessage(check.tabId, {
          type: AUTOSCROLL_MSG,
          action: 'at-check',
          account: check.account,
        });
        if (here === true) {
          await browser.tabs.update(check.tabId, { url, active: true });
          await browser.storage.local.set({ [STORAGE_QUEUE_TAB]: check.tabId });
          return;
        }
      } catch {
        // se cerró, o ya no es de x.com: se sigue como siempre
      }
    }
    const prev = (await browser.storage.local.get(STORAGE_QUEUE_TAB))[STORAGE_QUEUE_TAB];
    if (typeof prev === 'number') {
      try {
        const ours = await browser.tabs.sendMessage(prev, { type: AUTOSCROLL_MSG, action: 'ping' });
        if (ours === true) {
          await browser.tabs.update(prev, { url, active: true });
          return;
        }
      } catch {
        // la pestaña ya no está o no es de x.com: se abre otra
      }
    }
    const tab = await browser.tabs.create({ url });
    await browser.storage.local.set({ [STORAGE_QUEUE_TAB]: tab.id });
  }

  /** Si la pestaña de la captura en curso se cerró (o el navegador se reinició), la vuelve a
   *  abrir donde iba el crawl. Si sigue abierta pero en una búsqueda vieja de la misma cuenta
   *  (la de antes de actualizar la extensión, o una que el navegador restauró), la lleva a la
   *  actual: ahí el crawl no arranca. Si no contesta (está cargando, o no es x.com), se deja. */
  async function ensureTab(crawl: CrawlState): Promise<void> {
    const id = (await browser.storage.local.get(STORAGE_QUEUE_TAB))[STORAGE_QUEUE_TAB];
    if (typeof id === 'number') {
      let open = false;
      try {
        await browser.tabs.get(id);
        open = true;
      } catch {
        // se cerró
      }
      if (open) {
        const stale: unknown = await browser.tabs
          .sendMessage(id, { type: AUTOSCROLL_MSG, action: 'stale' })
          .catch(() => false);
        if (stale === true) {
          console.debug('[xport] la pestaña de la captura quedó en una búsqueda vieja: la llevo a', crawlUrl(crawl));
          await browser.tabs.update(id, { url: crawlUrl(crawl) });
        }
        return;
      }
    }
    const fresh = await withFrontier(crawl);
    if (fresh !== crawl) await browser.storage.local.set({ [STORAGE_CRAWL]: fresh });
    console.debug('[xport] la pestaña de la captura no está: la vuelvo a abrir en', crawlUrl(fresh));
    const tab = await browser.tabs.create({ url: crawlUrl(fresh) });
    await browser.storage.local.set({ [STORAGE_QUEUE_TAB]: tab.id });
  }

  /** El crawl con la cota bajada al tweet más viejo ya guardado, si es más bajo que la suya:
   *  lo avanzado scrolleando dentro de una búsqueda solo queda en lo guardado (ver
   *  `resumeBound`). Sin servicio, el mismo crawl. */
  async function withFrontier(crawl: CrawlState): Promise<CrawlState> {
    try {
      const prog = await (await client()).progress({
        account: crawl.account,
        since: crawl.since,
        until: crawl.until,
      });
      const bound = resumeBound(crawl, prog.oldest);
      return bound < untilTime(crawl) ? narrowed(crawl, bound) : crawl;
    } catch {
      return crawl;
    }
  }

  /** ¿Crawlea la pestaña `tabId`? Al reiniciarse el navegador puede haber dos en la búsqueda de
   *  la captura (la restaurada y la que reabrió `ensureTab`): crawlea una sola, la que ya era
   *  de la captura si sigue viva en una búsqueda; si no, la que pregunta. */
  async function claim(tabId: number | undefined): Promise<boolean> {
    if (tabId === undefined) return true;
    const stored: unknown = (await browser.storage.local.get(STORAGE_QUEUE_TAB))[STORAGE_QUEUE_TAB];
    let crawling = false;
    if (typeof stored === 'number' && stored !== tabId) {
      crawling = await browser.tabs
        .sendMessage(stored, { type: AUTOSCROLL_MSG, action: 'ping' })
        .then((r: unknown) => r === true)
        .catch(() => false);
    }
    if (!claimAllowed(stored, tabId, crawling)) {
      console.debug('[xport] otra pestaña ya crawlea la captura: la', tabId, 'no arranca');
      return false;
    }
    await browser.storage.local.set({ [STORAGE_QUEUE_TAB]: tabId });
    return true;
  }

  /** Un crawl que sigue: si espera a X, nada hasta la hora; pasada la hora, vuelve a la
   *  búsqueda donde iba (punto 11 de docs/PLAN.md). Si no, que su pestaña siga abierta. */
  async function keepCrawl(crawl: CrawlState): Promise<void> {
    switch (waitStep(crawl, Date.now())) {
      case 'waiting':
        return;
      case 'resume': {
        const waited: CrawlState = { ...crawl };
        delete waited.waitUntil;
        const resumed = await withFrontier(waited);
        console.debug('[xport] pasó la espera a X: retomo', crawlUrl(resumed));
        await browser.storage.local.set({ [STORAGE_CRAWL]: resumed });
        await browser.storage.local.remove(STORAGE_PAUSED);
        if (resumed.captureId !== undefined) await report(resumed.captureId, 'running');
        await showSearch(resumed);
        return;
      }
      case 'none':
        if (crawl.captureId !== undefined) await ensureTab(crawl);
        return;
    }
  }

  /** Lleva la pestaña de la captura a la búsqueda del crawl, o abre otra si ya no está. */
  async function showSearch(crawl: CrawlState): Promise<void> {
    const id = (await browser.storage.local.get(STORAGE_QUEUE_TAB))[STORAGE_QUEUE_TAB];
    if (typeof id === 'number') {
      try {
        await browser.tabs.update(id, { url: crawlUrl(crawl) });
        return;
      } catch {
        // se cerró
      }
    }
    const tab = await browser.tabs.create({ url: crawlUrl(crawl) });
    await browser.storage.local.set({ [STORAGE_QUEUE_TAB]: tab.id });
  }

  /** Mientras espera a X, la pestaña deja la búsqueda: si no, x.com reintenta cada pocos
   *  segundos y cada intento es otro 429. Va a la página local, que muestra hasta cuándo. */
  async function parkTab(tabId: number): Promise<void> {
    try {
      await browser.tabs.update(tabId, { url: `${await getBase()}/` });
      await browser.storage.local.set({ [STORAGE_QUEUE_TAB]: tabId });
    } catch (err) {
      console.warn('[xport] no pude sacar la pestaña de la búsqueda mientras espero a X', err);
    }
  }

  async function onCrawlEvent(m: CrawlMsg, tabId: number | undefined): Promise<void> {
    if (m.event === 'waiting' && tabId !== undefined) await parkTab(tabId);
    if (typeof m.captureId !== 'number') return;
    if (m.event === 'done' && tabId !== undefined && (await startCheck(m.captureId, tabId))) {
      return; // el `done` lo avisa la comprobación de la cuenta
    }
    await report(m.captureId, reportedState(m.event), undefined, m.resumeAt);
  }

  // --- Por qué no hubo tweets (punto 10 de docs/PLAN.md) ---------------------------------
  // Una captura de la cola que termina sin un solo tweet guardado en el rango no avisa el
  // `done` enseguida: su pestaña va a `x.com/<cuenta>/following`, que pide UserByScreenName
  // sin cargar posts, y el `done` lleva esa respuesta cruda. Mientras tanto la cola no
  // avanza; si x.com no contesta en el plazo, termina con el aviso de siempre.

  async function readCheck(): Promise<AccountCheck | null> {
    return ((await browser.storage.local.get(STORAGE_CHECK))[STORAGE_CHECK] ??
      null) as AccountCheck | null;
  }

  /** Arranca la comprobación si la captura no guardó ni un tweet en el rango. Devuelve si
   *  arrancó (entonces el `done` lo avisa `finishCheck`). */
  async function startCheck(captureId: number, tabId: number): Promise<boolean> {
    const crawl = await readCrawl();
    if (!crawl || crawl.captureId !== captureId) return false;
    let captured: number;
    try {
      captured = (
        await (await client()).progress({
          account: crawl.account,
          since: crawl.since,
          until: crawl.until,
        })
      ).captured;
    } catch {
      return false; // sin servicio: el `done` de siempre (que también puede fallar)
    }
    if (captured > 0) return false;
    const check: AccountCheck = {
      captureId,
      account: crawl.account,
      tabId,
      startedAt: Date.now(),
    };
    console.debug('[xport] la captura', captureId, 'no trajo tweets: miro la cuenta en', checkUrl(check.account));
    await browser.storage.local.set({ [STORAGE_CHECK]: check });
    // El crawl terminó: sin su estado, ni esta pestaña ni otra lo retoman.
    await browser.storage.local.remove([STORAGE_CRAWL, STORAGE_PAUSED]);
    try {
      await browser.tabs.update(tabId, { url: checkUrl(check.account) });
    } catch {
      await finishCheck(check); // la pestaña ya no está
    }
    return true;
  }

  /** La respuesta de UserByScreenName de la cuenta que se está mirando: la captura termina y
   *  la cola sigue sin esperar a la alarma. */
  async function onUserLookup(screenName: string | null, data: unknown): Promise<void> {
    const check = await readCheck();
    if (!answersCheck(check, screenName)) return;
    await finishCheck(check, userPayload(data));
    await pollQueue();
  }

  /** Avisa el `done` de la captura, con la respuesta de x.com si la hay. Si el servicio no
   *  la acepta, avisa sin ella: una captura que no termina se volvería a crawlear. */
  async function finishCheck(check: AccountCheck, user?: Record<string, unknown>): Promise<void> {
    await browser.storage.local.set({ [STORAGE_CHECK]: { ...check, finished: true } });
    const ok = await report(check.captureId, 'done', undefined, undefined, user);
    if (!ok && user) await report(check.captureId, 'done');
  }

  async function captureEnabled(): Promise<boolean> {
    const v = (await browser.storage.local.get(STORAGE_CAPTURE))[STORAGE_CAPTURE];
    return v !== false; // default: habilitado
  }

  async function getBase(): Promise<string> {
    const v = (await browser.storage.local.get(STORAGE_BASE))[STORAGE_BASE];
    return (typeof v === 'string' && v ? v : DEFAULT_BASE).replace(/\/+$/, '');
  }

  async function bumpCount(account: string, saved: number): Promise<void> {
    if (saved <= 0) return;
    const stored = (await browser.storage.local.get(STORAGE_COUNTS))[STORAGE_COUNTS];
    const counts = (typeof stored === 'object' && stored ? stored : {}) as Record<string, number>;
    counts[account] = (counts[account] ?? 0) + saved;
    await browser.storage.local.set({ [STORAGE_COUNTS]: counts });
  }
});
