import { browser } from 'wxt/browser';

import { type CaptureOut, XportClient } from '../lib/api';
import { AUTOSCROLL_MSG, type CrawlState, searchUrl } from '../lib/autoscroll';
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
  STORAGE_STATUS,
} from '../lib/capture';
import { decideIngest, type IngestFlags, MAX_PAGES_PER_POST, stopNotice } from '../lib/ingest';
import {
  CRAWL_MSG,
  type CrawlMsg,
  decideQueue,
  POLL_MINUTES,
  QUEUE_ALARM,
  QUEUE_MSG,
  reportedState,
  startPlan,
  type StartPlan,
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
  async function stopCrawl(notice: string): Promise<void> {
    const had = await readCrawl();
    if (!had) return; // no había crawl (captura manual): nada que cortar
    console.warn('[xport] corto el crawl:', notice);
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
    return new XportClient(await getBase());
  }

  async function report(
    id: number,
    state: 'running' | 'paused' | 'done' | 'stopped',
    note?: string,
  ): Promise<void> {
    try {
      await (await client()).captureState(id, note ? { state, note } : { state });
    } catch (err) {
      console.warn('[xport] no pude avisarle al servicio el estado de la captura', id, err);
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
  browser.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === QUEUE_ALARM) void serial(pollQueue);
  });
  void serial(pollQueue);

  browser.runtime.onMessage.addListener((msg: unknown) => {
    const m = msg as { type?: unknown } | null;
    if (m?.type === QUEUE_MSG) return serial(pollQueue);
    if (m?.type === CRAWL_MSG) return serial(() => onCrawlEvent(msg as CrawlMsg));
    return undefined; // otros mensajes: no respondemos
  });

  async function pollQueue(): Promise<void> {
    if (!(await captureEnabled())) return; // captura apagada: no se toma trabajo
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
        if (crawl?.captureId !== undefined) await ensureTab(crawl);
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
      until: plan.until,
      captureId: cap.id,
    };
    console.debug('[xport] arranco la captura', cap.id, `@${cap.account}`, crawl.since, '→', crawl.until);
    await browser.storage.local.remove(STORAGE_PAUSED);
    await browser.storage.local.set({ [STORAGE_CRAWL]: crawl, [STORAGE_STATUS]: plan.message });
    await openSearch(crawl);
  }

  /** Abre la búsqueda del crawl. Reusa la pestaña de la captura anterior si sigue en una
   *  búsqueda de captura (así no se apilan pestañas); si no, abre otra. */
  async function openSearch(crawl: CrawlState): Promise<void> {
    const url = searchUrl(crawl.account, crawl.since, crawl.until);
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
   *  abrir donde iba el crawl. */
  async function ensureTab(crawl: CrawlState): Promise<void> {
    const id = (await browser.storage.local.get(STORAGE_QUEUE_TAB))[STORAGE_QUEUE_TAB];
    if (typeof id === 'number') {
      try {
        await browser.tabs.get(id);
        return; // sigue abierta
      } catch {
        // se cerró
      }
    }
    console.debug('[xport] la pestaña de la captura no está: la vuelvo a abrir');
    const tab = await browser.tabs.create({
      url: searchUrl(crawl.account, crawl.since, crawl.until),
    });
    await browser.storage.local.set({ [STORAGE_QUEUE_TAB]: tab.id });
  }

  async function onCrawlEvent(m: CrawlMsg): Promise<void> {
    if (typeof m.captureId !== 'number') return;
    await report(m.captureId, reportedState(m.event));
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
