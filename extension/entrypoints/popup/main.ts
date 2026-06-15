import { browser } from 'wxt/browser';

import { XportClient } from '../../lib/api';
import { AUTOSCROLL_MSG, searchUrl } from '../../lib/autoscroll';
import {
  DEFAULT_BASE,
  STORAGE_AUTOSTART,
  STORAGE_BASE,
  STORAGE_CAPTURE,
  STORAGE_COUNTS,
} from '../../lib/capture';

const COUNTS_REFRESH_MS = 2000;

function el<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error(`falta #${id} en el popup`);
  return node as T;
}

const baseInput = el<HTMLInputElement>('base');
const accountInput = el<HTMLInputElement>('account');
const sinceInput = el<HTMLInputElement>('since');
const untilInput = el<HTMLInputElement>('until');
const captureForm = el<HTMLFormElement>('capture-form');
const captureStop = el<HTMLButtonElement>('capture-stop');
const exportBtn = el<HTMLButtonElement>('export');
const gateEl = el<HTMLElement>('gate');
const countsEl = el<HTMLElement>('counts');
const statusEl = el<HTMLElement>('status');
const exportStatus = el<HTMLElement>('export-status');

function currentBase(): string {
  return baseInput.value.trim().replace(/\/+$/, '') || DEFAULT_BASE;
}

function account(): string {
  return accountInput.value.trim().replace(/^@+/, '');
}

function setStatus(message: string): void {
  statusEl.hidden = false;
  statusEl.textContent = message;
}

function escapeHtml(s: string): string {
  return s.replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!,
  );
}

async function refreshGate(): Promise<void> {
  try {
    const g = await new XportClient(currentBase()).gate();
    const pct = ((g.usage / g.hard_cap) * 100).toFixed(2);
    gateEl.hidden = false;
    gateEl.textContent =
      `Gate: ${g.usage.toLocaleString()} / ${g.hard_cap.toLocaleString()} accesos ` +
      `en 24 h (${pct}%) · quedan ${g.remaining.toLocaleString()}`;
  } catch {
    // El servicio puede no estar corriendo; el estado del gate es informativo.
  }
}

async function renderCounts(): Promise<void> {
  const stored = await browser.storage.local.get(STORAGE_COUNTS);
  const map = (stored[STORAGE_COUNTS] ?? {}) as Record<string, number>;
  const entries = Object.entries(map);
  if (entries.length === 0) {
    countsEl.hidden = true;
    return;
  }
  countsEl.hidden = false;
  countsEl.textContent = 'Capturado: ' + entries.map(([a, n]) => `${a} (${n})`).join(' · ');
}

captureForm.addEventListener('submit', (event) => {
  event.preventDefault();
  void onCapture();
});
captureStop.addEventListener('click', () => void stopScroll());
exportBtn.addEventListener('click', () => void onExport());
baseInput.addEventListener('change', () => {
  void browser.storage.local.set({ [STORAGE_BASE]: currentBase() });
});

async function onCapture(): Promise<void> {
  if (!account()) return;
  // El background lee la base de storage (no del campo), así que la persistimos acá.
  // El flag de autostart lo consume el content script de la pestaña nueva al cargar
  // (el popup se cierra al abrir la pestaña, no se puede mensajear el start).
  await browser.storage.local.set({
    [STORAGE_BASE]: currentBase(),
    [STORAGE_CAPTURE]: true,
    [STORAGE_AUTOSTART]: true,
  });
  await browser.tabs.create({ url: searchUrl(account(), sinceInput.value, untilInput.value) });
  setStatus('Abriendo la búsqueda y scrolleando… (podés cerrar el popup)');
}

async function stopScroll(): Promise<void> {
  const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
  if (tab?.id === undefined) return;
  try {
    await browser.tabs.sendMessage(tab.id, { type: AUTOSCROLL_MSG, action: 'stop' });
    setStatus('Auto-scroll detenido.');
  } catch {
    setStatus('No hay una captura activa en esta pestaña.');
  }
}

async function onExport(): Promise<void> {
  if (!account()) return;
  try {
    const client = new XportClient(currentBase());
    const r = await client.exportCapture({
      account: account(),
      since: sinceInput.value,
      until: untilInput.value,
    });
    exportStatus.hidden = false;
    exportStatus.innerHTML =
      `<a href="${client.absolute(r.download_url)}" target="_blank" rel="noreferrer">` +
      `${escapeHtml(r.csv)}</a> <small>${r.exported} tweets</small>`;
  } catch (err) {
    exportStatus.hidden = false;
    exportStatus.innerHTML =
      `<span class="error">${escapeHtml(err instanceof Error ? err.message : String(err))}</span>`;
  }
}

async function init(): Promise<void> {
  const stored = await browser.storage.local.get(STORAGE_BASE);
  const saved = stored[STORAGE_BASE];
  baseInput.value = typeof saved === 'string' && saved ? saved : DEFAULT_BASE;
  await refreshGate();
  await renderCounts();
  setInterval(() => void renderCounts(), COUNTS_REFRESH_MS);
}

void init();
