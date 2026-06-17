import { browser } from 'wxt/browser';

import { XportClient } from '../../lib/api';
import { AUTOSCROLL_MSG, type CrawlState, isValidDate, searchUrl } from '../../lib/autoscroll';
import {
  DEFAULT_BASE,
  STORAGE_BASE,
  STORAGE_CAPTURE,
  STORAGE_COUNTS,
  STORAGE_CRAWL,
  STORAGE_FORM,
  STORAGE_STATUS,
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

/** Valida cuenta + rango y devuelve un mensaje de error, o `null` si está OK. Fuente
 *  única para capturar y exportar (antes el campo vacío cortaba en silencio). */
function validationError(): string | null {
  if (!account()) return 'Completá la cuenta (sin @).';
  if (!isValidDate(sinceInput.value) || !isValidDate(untilInput.value)) {
    return 'Completá las fechas en formato AAAA-MM-DD.';
  }
  if (sinceInput.value >= untilInput.value) return '"Desde" debe ser anterior a "Hasta".';
  return null;
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

// Inserta los guiones de AAAA-MM-DD a medida que se escribe (los inputs son de texto,
// no `type=date`: el calendario nativo queda tapado por la ventana chica del popup).
function autoFormatDate(input: HTMLInputElement): void {
  input.addEventListener('input', () => {
    const digits = input.value.replace(/\D/g, '').slice(0, 8);
    const parts = [digits.slice(0, 4), digits.slice(4, 6), digits.slice(6, 8)].filter(Boolean);
    input.value = parts.join('-');
  });
}
autoFormatDate(sinceInput);
autoFormatDate(untilInput);

interface FormState {
  account: string;
  since: string;
  until: string;
}

async function persistForm(): Promise<void> {
  // El popup se cierra al abrir la pestaña de captura; sin esto, al reabrirlo para
  // exportar los campos quedaban vacíos y "Exportar" no hacía nada (bug).
  const form: FormState = { account: account(), since: sinceInput.value, until: untilInput.value };
  await browser.storage.local.set({ [STORAGE_FORM]: form });
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
for (const input of [accountInput, sinceInput, untilInput]) {
  input.addEventListener('change', () => void persistForm());
}

async function onCapture(): Promise<void> {
  const err = validationError();
  if (err) {
    setStatus(err);
    return;
  }
  // El crawl adaptativo arranca con el rango COMPLETO; el content script encoge `until`
  // solo si choca la pared. El background lee la base de storage (no del campo).
  const crawl: CrawlState = { account: account(), since: sinceInput.value, until: untilInput.value };
  await browser.storage.local.remove(STORAGE_STATUS); // limpiar avisos viejos
  await browser.storage.local.set({
    [STORAGE_BASE]: currentBase(),
    [STORAGE_CAPTURE]: true,
    [STORAGE_CRAWL]: crawl,
  });
  await persistForm();
  await browser.tabs.create({ url: searchUrl(crawl.account, crawl.since, crawl.until) });
  setStatus('Abriendo la búsqueda y scrolleando… (podés cerrar el popup)');
}

async function stopScroll(): Promise<void> {
  // Borramos el crawl desde el popup también: si la pestaña activa no es la de captura,
  // el mensaje no llega, pero igual no debe seguir narrowando ni navegando.
  await browser.storage.local.remove(STORAGE_CRAWL);
  const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
  if (tab?.id === undefined) {
    setStatus('Captura detenida.');
    return;
  }
  try {
    await browser.tabs.sendMessage(tab.id, { type: AUTOSCROLL_MSG, action: 'stop' });
    setStatus('Auto-scroll detenido.');
  } catch {
    setStatus('Captura detenida (no había scroll activo en esta pestaña).');
  }
}

async function onExport(): Promise<void> {
  const err = validationError();
  if (err) {
    setStatus(err);
    return;
  }
  await persistForm();
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
  const stored = await browser.storage.local.get([STORAGE_BASE, STORAGE_FORM, STORAGE_STATUS]);
  const savedBase = stored[STORAGE_BASE];
  baseInput.value = typeof savedBase === 'string' && savedBase ? savedBase : DEFAULT_BASE;
  const form = stored[STORAGE_FORM] as Partial<FormState> | undefined;
  if (form) {
    accountInput.value = form.account ?? '';
    sinceInput.value = form.since ?? '';
    untilInput.value = form.until ?? '';
  }
  // Aviso del crawl (p.ej. se cortó por errores de carga). Lee-y-borra: se muestra una vez.
  const notice = stored[STORAGE_STATUS];
  if (typeof notice === 'string' && notice) {
    setStatus(notice);
    await browser.storage.local.remove(STORAGE_STATUS);
  }
  await refreshGate();
  await renderCounts();
  setInterval(() => void renderCounts(), COUNTS_REFRESH_MS);
}

void init();
