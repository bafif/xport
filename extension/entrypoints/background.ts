import { browser } from 'wxt/browser';

import {
  accountFromUrl,
  CAPTURE_SOURCE,
  type CaptureMsg,
  DEFAULT_BASE,
  STORAGE_BASE,
  STORAGE_CAPTURE,
  STORAGE_COUNTS,
} from '../lib/capture';

// Relay del patrón C: recibe las capturas del bridge, las batchea por cuenta y las
// POSTea al FastAPI local (/ingest). El backend hace extract+map+store+gate. Si el
// servicio está caído o devuelve 5xx, re-encola (no pierde capturas); si /ingest
// rechaza con 4xx (429 over-cap, 400), NO reintenta (el acceso ya ocurrió). El buffer
// está acotado (no crece sin fin en un outage). El service worker queda mínimo.

const FLUSH_MS = 1500;
const MAX_PAGES_PER_ACCOUNT = 1000; // cota del buffer ante outage prolongado

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
        const pages = buffers.get(account) ?? [];
        buffers.delete(account);
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
          continue;
        }
        if (res.ok) {
          const reply = (await res.json()) as IngestReply;
          await bumpCount(reply.account, reply.saved);
          console.debug('[xport] ingest OK', account, '→ saved', reply.saved, 'over_cap', reply.over_cap);
        } else if (res.status >= 500) {
          console.warn('[xport] ingest', res.status, '(server) → reintento');
          requeue(account, pages); // error transitorio del server: reintentar
        } else {
          console.warn('[xport] ingest rechazado', res.status, '(no se reintenta)');
        }
        // 4xx (429 over-cap, 400): el acceso ya ocurrió, no se reintenta (drop).
      }
    } finally {
      flushing = false;
    }
    if (buffers.size > 0) schedule();
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
