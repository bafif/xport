import { browser } from 'wxt/browser';

import { EXCEL_ES, type GateResponse, XportApiError, XportClient } from '../../lib/api';
import { AUTOSCROLL_MSG, type CrawlState, isValidDate } from '../../lib/autoscroll';
import {
  DEFAULT_BASE,
  STORAGE_BASE,
  STORAGE_CAPTURE,
  STORAGE_COUNTS,
  STORAGE_CRAWL,
  STORAGE_EXCEL,
  STORAGE_FORM,
  STORAGE_PAUSED,
  STORAGE_STATUS,
} from '../../lib/capture';
import { QUEUE_MSG, type QueueMsg } from '../../lib/queue';

const COUNTS_REFRESH_MS = 2000;
const SERVICE_TRIES = 5; // intentos contra el servicio antes de no arrancar la captura
const SERVICE_RETRY_MS = 2000;

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
const resetBtn = el<HTMLButtonElement>('reset');
const excelInput = el<HTMLInputElement>('excel');
const pausedEl = el<HTMLElement>('paused');
const gateEl = el<HTMLElement>('gate');
const countsEl = el<HTMLElement>('counts');
const statusEl = el<HTMLElement>('status');
const exportStatus = el<HTMLElement>('export-status');
const pageLink = el<HTMLAnchorElement>('page-link');

function currentBase(): string {
  return baseInput.value.trim().replace(/\/+$/, '') || DEFAULT_BASE;
}

function account(): string {
  // En minúsculas, como lo guarda el backend: X no distingue mayúsculas en los handles.
  return accountInput.value.trim().replace(/^@+/, '').toLowerCase();
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

/** Muestra el aviso de pausa (pestaña de la captura oculta) mientras haya un crawl: si
 *  el crawl se cortó o terminó, un aviso viejo no se muestra. */
async function renderPaused(): Promise<void> {
  const stored = await browser.storage.local.get([STORAGE_PAUSED, STORAGE_CRAWL]);
  const notice = stored[STORAGE_PAUSED];
  pausedEl.hidden = !(typeof notice === 'string' && notice && stored[STORAGE_CRAWL]);
  if (!pausedEl.hidden) pausedEl.textContent = notice as string;
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
resetBtn.addEventListener('click', () => void onReset());
excelInput.addEventListener('change', () => {
  void browser.storage.local.set({ [STORAGE_EXCEL]: excelInput.checked });
});
browser.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && (STORAGE_PAUSED in changes || STORAGE_CRAWL in changes)) {
    void renderPaused();
  }
});
baseInput.addEventListener('change', () => {
  void browser.storage.local.set({ [STORAGE_BASE]: currentBase() });
  pageLink.href = `${currentBase()}/`;
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
  const acc = account();
  const since = sinceInput.value;
  const until = untilInput.value;

  // Sin servicio no se captura (regla 1 de docs/PLAN.md): lo que se viera en X no
  // quedaría contado en el gate. Se espera un poco por si el native host lo está
  // levantando; si no aparece, o si el tope de 24 h está lleno, no se encola.
  const client = new XportClient(currentBase());
  let gate: GateResponse;
  try {
    gate = await whenServiceUp(() => client.gate());
  } catch (err) {
    setStatus(
      `No arranco la captura: ${err instanceof Error ? err.message : String(err)}. ` +
        'Sin el servicio, lo que se vea en X no queda contado en el tope de 24 h.',
    );
    return;
  }
  if (gate.remaining <= 0) {
    setStatus('El tope de 24 h del gate está lleno: no arranco la captura. Probá más tarde.');
    return;
  }

  // La captura entra a la cola del servicio, como las de la página, y el background la
  // arranca (o la retoma desde donde quedó) cuando le toca: va de a una. Al terminar, el
  // servicio exporta el CSV y lo avisa con una notificación.
  let id: number;
  try {
    const [cap] = await client.enqueue({
      accounts: [acc],
      since,
      until,
      ...(excelInput.checked ? EXCEL_ES : {}),
    });
    id = cap.id;
  } catch (err) {
    setStatus(`No pude encolar la captura: ${err instanceof Error ? err.message : String(err)}`);
    return;
  }
  await browser.storage.local.remove(STORAGE_STATUS); // limpiar avisos viejos
  await browser.storage.local.set({ [STORAGE_BASE]: currentBase(), [STORAGE_CAPTURE]: true });
  await persistForm();
  const poll: QueueMsg = { type: QUEUE_MSG };
  await browser.runtime.sendMessage(poll).catch(() => {});

  const crawl = (await browser.storage.local.get(STORAGE_CRAWL))[STORAGE_CRAWL] as
    | CrawlState
    | undefined;
  const cap = (await client.captures().catch(() => [])).find((c) => c.id === id);
  if (cap?.state === 'done') {
    setStatus(
      `Ya está todo capturado en ese rango (${cap.exported ?? 0} tuits en el CSV). ` +
        'Lo tenés en la página de xport.',
    );
  } else if (crawl?.captureId === id) {
    setStatus('Abriendo la búsqueda y scrolleando… (podés cerrar el popup)');
  } else {
    setStatus('En la cola: arranca cuando termine la captura en curso. La página de xport muestra el avance.');
  }
}

/** Reintenta `call` mientras el servicio no responda (status 0), hasta ~10 s: al abrir
 *  el navegador, el native host puede estar levantándolo todavía. */
async function whenServiceUp<T>(call: () => Promise<T>): Promise<T> {
  for (let attempt = 1; ; attempt++) {
    try {
      return await call();
    } catch (err) {
      if (!(err instanceof XportApiError) || err.status !== 0 || attempt >= SERVICE_TRIES) throw err;
      setStatus('Esperando al servicio local…');
      await new Promise((r) => setTimeout(r, SERVICE_RETRY_MS));
    }
  }
}

/** Corta el crawl en curso, y si es de la cola, le avisa al servicio que la cortó la persona. */
async function endCrawl(note: string): Promise<void> {
  const crawl = (await browser.storage.local.get(STORAGE_CRAWL))[STORAGE_CRAWL] as
    | CrawlState
    | undefined;
  await browser.storage.local.remove([STORAGE_CRAWL, STORAGE_PAUSED]);
  if (crawl?.captureId !== undefined) {
    await new XportClient(currentBase())
      .captureState(crawl.captureId, { state: 'stopped', note, by_user: true })
      .catch(() => {});
  }
}

async function stopScroll(): Promise<void> {
  // Borramos el crawl desde el popup también: si la pestaña activa no es la de captura,
  // el mensaje no llega, pero igual no debe seguir narrowando ni navegando.
  await endCrawl('La detuviste desde el popup.');
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
      ...(excelInput.checked ? EXCEL_ES : {}),
    });
    exportStatus.hidden = false;
    // Aclaración: el CSV trae menos que el contador "Capturado" porque deja afuera las
    // respuestas a otras cuentas (se exportan tweets propios + hilos propios). Solo se
    // muestra si efectivamente se excluyó algo.
    const note =
      r.excluded_replies > 0
        ? `<small class="muted">No se incluyen ${r.excluded_replies} respuestas a otras ` +
          `cuentas (el CSV trae tweets propios e hilos propios).</small>`
        : '';
    exportStatus.innerHTML =
      `<a href="${client.absolute(r.download_url)}" target="_blank" rel="noreferrer">` +
      `${escapeHtml(r.csv)}</a> <small>${r.exported} tweets</small>${note}`;
  } catch (err) {
    exportStatus.hidden = false;
    exportStatus.innerHTML =
      `<span class="error">${escapeHtml(err instanceof Error ? err.message : String(err))}</span>`;
  }
}

async function onReset(): Promise<void> {
  // Solo necesita la cuenta (borra todo lo de esa cuenta, sin importar el rango). El
  // frente de reanudación de /progress se arma con lo guardado: un tweet viejo colado
  // lo deja trabado en el pasado y la captura no vuelve a traer los nuevos.
  const acc = account();
  if (!acc) {
    setStatus('Completá la cuenta (sin @) para borrar lo capturado.');
    return;
  }
  if (
    !confirm(
      `¿Borrar todo lo capturado de @${acc}? Vas a tener que volver a capturar el rango. ` +
        `(No afecta el gate de 24 h.)`,
    )
  ) {
    return;
  }
  try {
    const r = await new XportClient(currentBase()).resetAccount(acc);
    // Limpiar el estado local para que la próxima captura arranque desde el tope del
    // rango (sin frente viejo): el contador de la cuenta y cualquier crawl/aviso en curso.
    const stored = await browser.storage.local.get(STORAGE_COUNTS);
    const counts = (stored[STORAGE_COUNTS] ?? {}) as Record<string, number>;
    delete counts[acc];
    await browser.storage.local.set({ [STORAGE_COUNTS]: counts });
    await endCrawl('Borraste lo capturado desde el popup.');
    await browser.storage.local.remove(STORAGE_STATUS);
    await renderCounts();
    setStatus(`Borrado: ${r.deleted} tuits de @${acc}. La próxima captura arranca de cero.`);
  } catch (err) {
    setStatus(err instanceof Error ? err.message : String(err));
  }
}

async function init(): Promise<void> {
  const stored = await browser.storage.local.get([
    STORAGE_BASE,
    STORAGE_EXCEL,
    STORAGE_FORM,
    STORAGE_STATUS,
  ]);
  excelInput.checked = stored[STORAGE_EXCEL] === true;
  const savedBase = stored[STORAGE_BASE];
  baseInput.value = typeof savedBase === 'string' && savedBase ? savedBase : DEFAULT_BASE;
  pageLink.href = `${currentBase()}/`;
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
  await renderPaused();
  await refreshGate();
  await renderCounts();
  setInterval(() => void renderCounts(), COUNTS_REFRESH_MS);
}

void init();
