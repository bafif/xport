import { browser } from 'wxt/browser';

import {
  accountFromUrl,
  CAPTURE_SOURCE,
  type CaptureMsg,
  STORAGE_BASE,
  STORAGE_CAPTURE,
  STORAGE_COUNTS,
} from '../lib/capture';

// Relay del patrón C: recibe las capturas del bridge, las batchea por cuenta y las
// POSTea al FastAPI local (/ingest). El backend hace extract+map+store+gate. Si el
// servicio está caído o devuelve 5xx, re-encola (no pierde capturas); si /ingest
// rechaza con 4xx (429 over-cap, 400), NO reintenta (el acceso ya ocurrió). El buffer
// está acotado (no crece sin fin en un outage). El service worker queda mínimo.

const DEFAULT_BASE = 'http://localhost:8080';
const FLUSH_MS = 1500;
const MAX_PAGES_PER_ACCOUNT = 1000; // cota del buffer ante outage prolongado

interface IngestReply {
  account: string;
  saved: number;
  over_cap: boolean;
}

export default defineBackground(() => {
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
