// La página local de xport (punto 8 de docs/PLAN.md): encola capturas, muestra su avance y
// el tope, y baja los CSV. Habla solo con el servicio que la sirve, en su mismo origen.
// Las fechas del formulario son inclusivas («hasta el 31/12/2025 inclusive»); al servicio
// el «hasta» le llega exclusivo, como en todo xport.

const REFRESH_MS = 3000;
const EXCEL_KEY = 'xport:excel';
const EXCEL_ES = { encoding: 'utf-8-sig', delimiter: ';' };

const $ = (id) => document.getElementById(id);
const form = $('form');
const accountsInput = $('accounts');
const sinceInput = $('since');
const untilInput = $('until');
const yearSelect = $('year');
const excelInput = $('excel');
const formMsg = $('form-msg');

// --- Fechas (AAAA-MM-DD, sin horas) ---------------------------------------------------

function iso(d) {
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function parse(day) {
  const [y, m, d] = day.split('-').map(Number);
  return new Date(y, m - 1, d);
}

function today() {
  return iso(new Date());
}

function addDays(day, n) {
  const d = parse(day);
  d.setDate(d.getDate() + n);
  return iso(d);
}

function addMonths(day, n) {
  const d = parse(day);
  const wanted = d.getMonth() + n;
  d.setMonth(wanted);
  if (d.getMonth() !== ((wanted % 12) + 12) % 12) d.setDate(0); // 31/3 menos un mes: 28/2
  return iso(d);
}

function show(day) {
  const [y, m, d] = day.split('-');
  return `${d}/${m}/${y}`;
}

function showTime(stamp) {
  const t = new Date(stamp);
  const hm = t.toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit' });
  return iso(t) === today() ? hm : `${show(iso(t))} ${hm}`;
}

const num = (n) => n.toLocaleString('es-AR');

// --- Formulario -----------------------------------------------------------------------

function untilInclusive() {
  return untilInput.value || today();
}

function renderRange() {
  const since = sinceInput.value;
  const until = untilInput.value;
  let text = '«Hasta» vacío quiere decir hoy.';
  if (since) {
    text = until
      ? `Del ${show(since)} al ${show(until)}, los dos inclusive.`
      : `Del ${show(since)} hasta hoy inclusive.`;
  }
  $('range').textContent = text;
}

function setRange(since, until) {
  sinceInput.value = since;
  untilInput.value = until;
  yearSelect.value = '';
  renderRange();
}

for (const button of document.querySelectorAll('[data-months]')) {
  button.addEventListener('click', () => {
    const end = today();
    setRange(addDays(addMonths(end, -Number(button.dataset.months)), 1), end);
  });
}

const thisYear = new Date().getFullYear();
for (let y = thisYear; y > thisYear - 8; y--) {
  yearSelect.add(new Option(y === thisYear ? `${y} (hasta hoy)` : String(y), String(y)));
}
yearSelect.addEventListener('change', () => {
  const y = Number(yearSelect.value);
  if (!y) return;
  const end = y === thisYear ? today() : `${y}-12-31`;
  sinceInput.value = `${y}-01-01`;
  untilInput.value = end;
  renderRange();
});
sinceInput.addEventListener('input', renderRange);
untilInput.addEventListener('input', renderRange);

excelInput.checked = localStorage.getItem(EXCEL_KEY) === 'true';
excelInput.addEventListener('change', () => {
  localStorage.setItem(EXCEL_KEY, String(excelInput.checked));
});

function say(text, isError = false) {
  formMsg.textContent = text;
  formMsg.className = isError ? 'error' : '';
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const accounts = accountsInput.value.trim();
  const since = sinceInput.value;
  const last = untilInclusive();
  if (!accounts) return say('Escribí al menos una cuenta.', true);
  if (!since) return say('Elegí desde qué fecha.', true);
  if (since > last) return say('«Desde» tiene que ser anterior a «Hasta».', true);
  if (last > today()) return say('«Hasta» no puede ser una fecha que todavía no pasó.', true);
  try {
    const body = { accounts: [accounts], since, until: addDays(last, 1) };
    const caps = await api('/captures', {
      method: 'POST',
      body: JSON.stringify({ ...body, ...(excelInput.checked ? EXCEL_ES : {}) }),
    });
    const names = caps.map((c) => `@${c.account}`).join(', ');
    say(`En la cola: ${names}. La extensión las toma de a una.`);
    accountsInput.value = '';
    await refresh();
  } catch (err) {
    say(err.message, true);
  }
});

// --- Servicio -------------------------------------------------------------------------

async function api(path, init = {}) {
  const res = await fetch(path, {
    ...init,
    headers: { 'content-type': 'application/json', ...(init.headers ?? {}) },
  });
  if (res.status === 204) return null;
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body?.detail;
    const text = Array.isArray(detail)
      ? detail.map((d) => String(d.msg).replace(/^Value error, /, '')).join('. ')
      : detail || `${res.status} ${res.statusText}`;
    throw new Error(text);
  }
  return body;
}

// --- Capturas -------------------------------------------------------------------------

const STATES = {
  queued: 'En cola',
  running: 'En curso',
  paused: 'En pausa',
  waiting: 'Esperando a X',
  done: 'Terminada',
  stopped: 'Cortada',
};
const ORDER = { running: 0, paused: 0, waiting: 0, queued: 1, done: 2, stopped: 2 };
const ACTIVE = ['running', 'paused', 'waiting'];

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else node.setAttribute(k, v);
  }
  node.append(...children.filter((c) => c !== null && c !== undefined));
  return node;
}

function progressText(c) {
  if (c.state === 'done') {
    if (c.csv === null) return null;
    const left =
      c.excluded_replies > 0
        ? ` No incluye ${num(c.excluded_replies)} respuestas a otras cuentas: el CSV trae los ` +
          'tweets propios y los hilos propios.'
        : '';
    return `${num(c.exported)} tweets en el CSV.${left}`;
  }
  if (c.state === 'queued') {
    return c.captured > 0 ? `Ya hay ${num(c.captured)} tweets guardados de antes.` : null;
  }
  if (c.oldest === null) return c.state === 'stopped' ? null : 'Todavía no entró ningún tweet.';
  return `Vas por el ${show(c.oldest)}: ${c.percent} % del rango, ${num(c.captured)} tweets.`;
}

async function cancel(c) {
  if (!confirm(`¿Cancelar la captura de @${c.account}? Lo que ya se guardó queda.`)) return;
  try {
    await api(`/captures/${c.id}/state`, {
      method: 'POST',
      body: JSON.stringify({ state: 'stopped', note: 'La cancelaste.', by_user: true }),
    });
  } catch (err) {
    alert(err.message);
  }
  await refresh();
}

function captureItem(c) {
  const last = addDays(c.until, -1);
  const lines = [];
  const progress = progressText(c);
  if (progress) lines.push(el('p', {}, progress));
  if (c.state === 'paused') {
    lines.push(el('p', {}, 'Su pestaña de x.com quedó oculta: volvé a esa pestaña para que siga.'));
  }
  if (c.state === 'waiting') {
    const when = c.resume_at ? ` hasta las ${showTime(c.resume_at)}` : '';
    lines.push(
      el('p', {}, `X frenó la búsqueda por un rato: espera${when} y sigue sola. No hace falta tocar nada.`),
    );
  }
  if (c.note) lines.push(el('p', { class: c.state === 'stopped' ? 'error' : '' }, c.note));
  if (c.last_page_at && ACTIVE.includes(c.state)) {
    lines.push(el('p', { class: 'hint' }, `Última página: ${showTime(c.last_page_at)}.`));
  }
  if (ACTIVE.includes(c.state)) {
    const fill = el('div');
    fill.style.width = `${c.percent}%`;
    lines.push(el('div', { class: 'bar', 'aria-hidden': 'true' }, fill));
  }
  const actions = [];
  if (c.download_url) {
    actions.push(el('a', { href: c.download_url, download: c.csv }, 'Bajar el CSV'));
  }
  if (c.state === 'queued' || ACTIVE.includes(c.state)) {
    const b = el('button', { type: 'button' }, 'Cancelar');
    b.addEventListener('click', () => void cancel(c));
    actions.push(b);
  }
  return el(
    'li',
    { class: 'item' },
    el(
      'div',
      { class: 'row' },
      el('span', { class: 'who' }, `@${c.account}`),
      el('span', { class: `state ${c.state}` }, STATES[c.state] ?? c.state),
    ),
    el('p', { class: 'hint' }, `Del ${show(c.since)} al ${show(last)}, inclusive.`),
    ...lines,
    actions.length ? el('div', { class: 'row' }, ...actions) : null,
  );
}

function renderQueue(caps) {
  const sorted = [...caps].sort(
    (a, b) => ORDER[a.state] - ORDER[b.state] || (ORDER[a.state] === 1 ? a.id - b.id : b.id - a.id),
  );
  $('queue').replaceChildren(...sorted.map(captureItem));
  $('queue-empty').hidden = caps.length > 0;
}

// --- Tope -----------------------------------------------------------------------------

function renderGate(g, days) {
  const pct = (g.usage / g.hard_cap) * 100;
  $('gate').textContent =
    `${num(g.usage)} de ${num(g.hard_cap)} tweets vistos en las últimas 24 horas ` +
    `(${pct.toLocaleString('es-AR', { maximumFractionDigits: 1 })} %). ` +
    (g.remaining > 0
      ? `Quedan ${num(g.remaining)}.`
      : 'Está lleno: las capturas esperan a que salgan los más viejos.');
  $('gate-bar').style.width = `${Math.min(100, pct)}%`;
  $('days').replaceChildren(
    ...days.map((d) =>
      el('tr', {}, el('td', {}, show(d.day)), el('td', { class: 'num' }, num(d.accessed))),
    ),
  );
}

// Quién captura: el navegador propio del servicio (sin ventana) o la extensión del navegador
// de siempre, que necesita la pestaña a la vista (punto 13 de docs/PLAN.md).
const BROWSER_TEXT = {
  idle: 'Las hace el navegador propio de xport, sin ninguna ventana: no tenés que tocar nada.',
  running: 'El navegador propio de xport está capturando, sin ninguna ventana a la vista.',
  login: 'Iniciá sesión en X en la ventana chica de xport que se abrió (una sola vez).',
};

function renderBrowser(b) {
  $('browser').textContent =
    BROWSER_TEXT[b.state] ??
    `${b.note ? `${b.note} ` : ''}La extensión de tu navegador abre la búsqueda en una pestaña: ` +
      'dejala abierta y a la vista mientras captura.';
}

// --- Ciclo ----------------------------------------------------------------------------

async function refresh() {
  try {
    const [caps, gate, days, nav] = await Promise.all([
      api('/captures'),
      api('/gate'),
      api('/gate/days'),
      api('/browser'),
    ]);
    $('offline').hidden = true;
    renderQueue(caps);
    renderGate(gate, days);
    renderBrowser(nav);
  } catch {
    $('offline').hidden = false;
  }
}

renderRange();
void refresh();
setInterval(() => void refresh(), REFRESH_MS);
