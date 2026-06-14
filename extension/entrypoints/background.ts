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
// servicio está caído, re-encola (no pierde capturas); si /ingest rechaza (429 por
// over-cap), no reintenta (el acceso ya ocurrió). El service worker queda mínimo.

const DEFAULT_BASE = 'http://localhost:8080';
const FLUSH_MS = 1500;

interface IngestReply {
  account: string;
  saved: number;
  over_cap: boolean;
}

export default defineBackground(() => {
  const buffers = new Map<string, unknown[]>(); // account -> páginas crudas
  let timer: ReturnType<typeof setTimeout> | null = null;

  browser.runtime.onMessage.addListener((msg: unknown): void => {
    const m = msg as Partial<CaptureMsg> | null;
    if (!m || m.source !== CAPTURE_SOURCE || typeof m.url !== 'string') return;
    const account = accountFromUrl(m.url);
    if (!account) return; // MVP: solo capturas de búsqueda from:user
    buffers.set(account, [...(buffers.get(account) ?? []), m.data]);
    schedule();
  });

  function schedule(): void {
    if (timer === null) timer = setTimeout(() => void flush(), FLUSH_MS);
  }

  async function flush(): Promise<void> {
    timer = null;
    if (!(await captureEnabled())) {
      buffers.clear(); // captura apagada: descartar lo buffereado
      return;
    }
    const base = await getBase();
    for (const account of [...buffers.keys()]) {
      const pages = buffers.get(account) ?? [];
      buffers.delete(account);
      try {
        const res = await fetch(`${base}/ingest`, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ account, op: 'SearchTimeline', pages }),
        });
        if (res.ok) {
          const reply = (await res.json()) as IngestReply;
          await bumpCount(reply.account, reply.saved);
        }
        // !ok (p.ej. 429 over-cap): el acceso ya ocurrió, no se reintenta (drop).
      } catch {
        buffers.set(account, [...pages, ...(buffers.get(account) ?? [])]); // re-encola
      }
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
