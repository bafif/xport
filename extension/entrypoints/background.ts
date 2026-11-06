import { browser } from 'wxt/browser';

import {
  accountFromUrl,
  CAPTURE_SOURCE,
  type CaptureMsg,
  DEFAULT_BASE,
  STORAGE_BASE,
  STORAGE_CAPTURE,
  STORAGE_COUNTS,
  STORAGE_CRAWL,
  STORAGE_STATUS,
} from '../lib/capture';

// Relay del patrón C: recibe las capturas del bridge, las batchea por cuenta y las
// POSTea al FastAPI local (/ingest). El backend hace extract+map+store+gate. Si el
// servicio está caído o devuelve 5xx, re-encola (no pierde capturas); si /ingest
// rechaza con 4xx (429 over-cap, 400), NO reintenta (el backend ya registró el acceso o
// el lote no sirve). El buffer está acotado. El service worker queda mínimo.
//
// Regla 1 de docs/PLAN.md: la captura no puede seguir accediendo a tweets que el ledger
// del gate no llega a contar. Por eso el crawl se CORTA (se borra su estado, y el
// content script deja de scrollear) cuando el backend avisa el tope (429 u `over_cap`),
// cuando rechaza un lote, o cuando no responde y ya hay `STOP_AFTER_PENDING` páginas
// sin registrar. Lo buffereado se sigue reintentando.

const FLUSH_MS = 1500;
const MAX_PAGES_PER_ACCOUNT = 1000; // cota del buffer ante outage prolongado
// Páginas por POST: cada página de SearchTimeline pesa cientos de KB y nginx acepta
// 32 MB en /ingest; un lote entero tras un corte largo daba 413 y se perdía.
const MAX_PAGES_PER_POST = 20;
// Páginas pendientes (servicio caído) a partir de las cuales se corta el crawl: unos
// 400 tweets vistos y todavía sin contar en el gate.
const STOP_AFTER_PENDING = 20;

interface IngestReply {
  account: string;
  saved: number;
  over_cap: boolean;
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
        let res: Response;
        try {
          res = await fetch(`${base}/ingest`, {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ account, op: 'SearchTimeline', pages }),
          });
        } catch (err) {
          console.warn('[xport] servicio no responde en', base, '→ reintento', err);
          requeue(account, pages); // red caída: reintentar
          if ((buffers.get(account)?.length ?? 0) >= STOP_AFTER_PENDING) {
            await stopCrawl(
              `El servicio local no responde en ${base}: corté la captura para no ver tweets ` +
                'que el tope de 24 h no puede contar. Levantá el servicio y volvé a capturar.',
            );
          }
          continue;
        }
        if (res.ok) {
          const reply = (await res.json()) as IngestReply;
          await bumpCount(reply.account, reply.saved);
          console.debug('[xport] ingest OK', account, '→ saved', reply.saved, 'over_cap', reply.over_cap);
          if (reply.over_cap) await stopCrawl(CAP_NOTICE);
        } else if (res.status >= 500) {
          console.warn('[xport] ingest', res.status, '(server) → reintento');
          requeue(account, pages); // error transitorio del server: reintentar
        } else {
          // 4xx: no se reintenta. Con 429 el backend ya registró el acceso; con otro
          // rechazo el lote no sirve. En los dos casos, seguir scrolleando sería acceder
          // a tweets que no se cuentan: se corta el crawl.
          console.warn('[xport] ingest rechazado', res.status, '(no se reintenta)');
          await stopCrawl(
            res.status === 429
              ? CAP_NOTICE
              : `El servicio rechazó la captura (${res.status}): la corté. Mirá el log del servicio.`,
          );
        }
      }
    } finally {
      flushing = false;
    }
    if (buffers.size > 0) schedule();
  }

  const CAP_NOTICE =
    'Se llenó el tope de 24 h del gate: corté la captura. Vas a poder seguir cuando ' +
    'salgan de la ventana los accesos más viejos (el popup muestra cuántos quedan).';

  /** Corta el crawl en curso: sin su estado, el content script deja de scrollear y no
   *  narrowa ni navega más. El aviso lo muestra el popup la próxima vez que se abre. */
  async function stopCrawl(notice: string): Promise<void> {
    const had = (await browser.storage.local.get(STORAGE_CRAWL))[STORAGE_CRAWL];
    if (!had) return; // no había crawl (captura manual): nada que cortar
    console.warn('[xport] corto el crawl:', notice);
    await browser.storage.local.remove(STORAGE_CRAWL);
    await browser.storage.local.set({ [STORAGE_STATUS]: notice });
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
