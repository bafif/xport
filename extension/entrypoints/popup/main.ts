import { browser } from 'wxt/browser';

import { XportClient, type JobResponse } from '../../lib/api';
import { STORAGE_BASE, STORAGE_CAPTURE, STORAGE_COUNTS } from '../../lib/capture';

const DEFAULT_BASE = 'http://localhost:8080';
const POLL_MS = 1000;
const COUNTS_REFRESH_MS = 2000;

function el<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error(`falta #${id} en el popup`);
  return node as T;
}

const baseInput = el<HTMLInputElement>('base');
const accountsInput = el<HTMLInputElement>('accounts');
const sinceInput = el<HTMLInputElement>('since');
const untilInput = el<HTMLInputElement>('until');
const form = el<HTMLFormElement>('job-form');
const submitBtn = el<HTMLButtonElement>('submit');
const gateEl = el<HTMLElement>('gate');
const statusEl = el<HTMLElement>('status');
const errorEl = el<HTMLElement>('error');
const captureToggle = el<HTMLInputElement>('capture-toggle');
const countsEl = el<HTMLElement>('counts');
const expAccount = el<HTMLInputElement>('exp-account');
const expSince = el<HTMLInputElement>('exp-since');
const expUntil = el<HTMLInputElement>('exp-until');
const exportForm = el<HTMLFormElement>('export-form');
const exportStatus = el<HTMLElement>('export-status');

function currentBase(): string {
  return baseInput.value.trim().replace(/\/+$/, '') || DEFAULT_BASE;
}

function parseAccounts(raw: string): string[] {
  return raw
    .split(/[\s,]+/)
    .map((a) => a.trim().replace(/^@+/, ''))
    .filter(Boolean);
}

function showError(message: string): void {
  errorEl.textContent = message;
  errorEl.hidden = false;
}

function clearError(): void {
  errorEl.hidden = true;
  errorEl.textContent = '';
}

function escapeHtml(s: string): string {
  return s.replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!,
  );
}

async function refreshGate(client: XportClient): Promise<void> {
  try {
    const g = await client.gate();
    const pct = ((g.usage / g.hard_cap) * 100).toFixed(2);
    gateEl.hidden = false;
    gateEl.textContent =
      `Gate: ${g.usage.toLocaleString()} / ${g.hard_cap.toLocaleString()} accesos ` +
      `en 24 h (${pct}%) · quedan ${g.remaining.toLocaleString()}`;
  } catch {
    // El estado del gate es informativo: si no se puede leer, no rompe el flujo.
  }
}

function renderStatus(job: JobResponse, client: XportClient): void {
  statusEl.hidden = false;
  let html = `<p><code>${job.id.slice(0, 8)}</code> · <span class="badge ${job.status}">${job.status}</span></p>`;
  if (job.error) html += `<p class="error">${escapeHtml(job.error)}</p>`;
  const tail = job.log.slice(-5);
  if (tail.length) html += `<pre>${tail.map(escapeHtml).join('\n')}</pre>`;
  if (job.status === 'done' && job.results) {
    html += '<ul class="results">';
    for (const r of job.results) {
      const url = client.csvUrl(job.id, r.account);
      html +=
        `<li><a href="${url}" target="_blank" rel="noreferrer">${escapeHtml(r.account)}.csv</a>` +
        ` <small>${r.exported} tweets</small></li>`;
    }
    html += '</ul>';
  }
  statusEl.innerHTML = html;
}

async function poll(client: XportClient, id: string): Promise<void> {
  for (;;) {
    const job = await client.getJob(id);
    renderStatus(job, client);
    await refreshGate(client);
    if (job.status === 'done' || job.status === 'error') return;
    await new Promise((r) => setTimeout(r, POLL_MS));
  }
}

form.addEventListener('submit', (event) => {
  event.preventDefault();
  void onSubmit();
});

async function onSubmit(): Promise<void> {
  clearError();
  const base = currentBase();
  const accounts = parseAccounts(accountsInput.value);
  if (accounts.length === 0) {
    showError('Ingresá al menos una cuenta.');
    return;
  }
  await browser.storage.local.set({ [STORAGE_BASE]: base });
  const client = new XportClient(base);
  submitBtn.disabled = true;
  try {
    const job = await client.createJob({
      accounts,
      since: sinceInput.value,
      until: untilInput.value,
    });
    await poll(client, job.id);
  } catch (err) {
    showError(err instanceof Error ? err.message : String(err));
  } finally {
    submitBtn.disabled = false;
  }
}

captureToggle.addEventListener('change', () => {
  void browser.storage.local.set({ [STORAGE_CAPTURE]: captureToggle.checked });
});

exportForm.addEventListener('submit', (event) => {
  event.preventDefault();
  void onExport();
});

async function onExport(): Promise<void> {
  const account = expAccount.value.trim().replace(/^@+/, '');
  if (!account) return;
  const client = new XportClient(currentBase());
  try {
    const r = await client.exportCapture({
      account,
      since: expSince.value,
      until: expUntil.value,
    });
    const url = client.absolute(r.download_url);
    exportStatus.hidden = false;
    exportStatus.innerHTML =
      `<a href="${url}" target="_blank" rel="noreferrer">${escapeHtml(r.csv)}</a>` +
      ` <small>${r.exported} tweets</small>`;
  } catch (err) {
    exportStatus.hidden = false;
    exportStatus.innerHTML =
      `<span class="error">${escapeHtml(err instanceof Error ? err.message : String(err))}</span>`;
  }
}

async function renderCounts(): Promise<void> {
  const stored = await browser.storage.local.get([STORAGE_CAPTURE, STORAGE_COUNTS]);
  captureToggle.checked = stored[STORAGE_CAPTURE] !== false;
  const map = (stored[STORAGE_COUNTS] ?? {}) as Record<string, number>;
  const entries = Object.entries(map);
  if (entries.length === 0) {
    countsEl.hidden = true;
    return;
  }
  countsEl.hidden = false;
  countsEl.textContent = 'Capturado: ' + entries.map(([a, n]) => `${a} (${n})`).join(' · ');
}

async function init(): Promise<void> {
  const stored = await browser.storage.local.get(STORAGE_BASE);
  const saved = stored[STORAGE_BASE];
  baseInput.value = typeof saved === 'string' && saved ? saved : DEFAULT_BASE;
  await refreshGate(new XportClient(currentBase()));
  await renderCounts();
  setInterval(() => void renderCounts(), COUNTS_REFRESH_MS);
}

void init();
