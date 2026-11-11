// La página local de xport (punto 8 de docs/PLAN.md): encola capturas, muestra su avance y
// el tope, y baja los CSV. Habla solo con el servicio que la sirve, en su mismo origen. Todo
// texto que se ve sale del catálogo del servicio con `t()`, en castellano o en inglés.
// Las fechas del formulario son inclusivas («hasta el 31/12/2025 inclusive»); al servicio
// el «hasta» le llega exclusivo, como en todo xport.

const REFRESH_MS = 3000;
const EXCEL_KEY = 'xport:excel';
const LANG_KEY = 'xport:lang';
const EXCEL_ES = { encoding: 'utf-8-sig', delimiter: ';' };

// --- Idioma ---------------------------------------------------------------------------
// Castellano si alguno de los idiomas del navegador lo es; si no, inglés. Lo que la persona
// elige en el selector se guarda y manda sobre eso. Los textos son los del servicio
// (`/i18n/<idioma>`, el mismo catálogo que usan sus mensajes).

function detectLang(languages) {
  return languages.some((l) => /^es(-|_|$)/i.test(l || '')) ? 'es' : 'en';
}

function savedLang() {
  try {
    const v = localStorage.getItem(LANG_KEY);
    return v === 'es' || v === 'en' ? v : null;
  } catch {
    return null;
  }
}

let lang =
  savedLang() ??
  detectLang(navigator.languages?.length ? navigator.languages : [navigator.language]);
let strings = {};

async function loadStrings() {
  const res = await fetch(`/i18n/${lang}`);
  strings = await res.json();
}

/** El texto de `key` con sus `{valores}`. */
function t(key, params = {}) {
  return (strings[key] ?? key).replace(/\{(\w+)\}/g, (m, name) =>
    name in params ? String(params[name]) : m,
  );
}

/** Un aviso guardado por el servicio (`{key, params}`), en el idioma de la página. */
function note(n) {
  if (!n) return '';
  const params = {};
  for (const [k, v] of Object.entries(n.params ?? {})) {
    params[k] = typeof v === 'object' && v ? note(v) : typeof v === 'number' ? num(v) : v;
  }
  return t(n.key, params);
}

const LOCALE = () => (lang === 'es' ? 'es-AR' : 'en-US');

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
  if (lang !== 'es') return day; // AAAA-MM-DD: sin la duda de mes/día
  const [y, m, d] = day.split('-');
  return `${d}/${m}/${y}`;
}

function showTime(stamp) {
  const when = new Date(stamp);
  const options =
    lang === 'es'
      ? { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' } // «18:40»
      : { hour: 'numeric', minute: '2-digit' }; // «6:40 PM»
  const hm = when.toLocaleTimeString(LOCALE(), options);
  return iso(when) === today() ? hm : `${show(iso(when))} ${hm}`;
}

const num = (n) => n.toLocaleString(LOCALE());

// --- Formulario -----------------------------------------------------------------------

function untilInclusive() {
  return untilInput.value || today();
}

function renderRange() {
  const since = sinceInput.value;
  const until = untilInput.value;
  let text = t('page.range_empty');
  if (since) {
    text = until
      ? t('page.range', { since: show(since), until: show(until) })
      : t('page.range_today', { since: show(since) });
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
function fillYears() {
  while (yearSelect.options.length > 1) yearSelect.remove(1);
  for (let y = thisYear; y > thisYear - 8; y--) {
    const label = y === thisYear ? t('page.year_to_date', { year: y }) : String(y);
    yearSelect.add(new Option(label, String(y)));
  }
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

// El aviso del formulario. Uno del catálogo llega como función que lo arma en el idioma actual,
// así se rehace al cambiar de idioma; uno ya armado (un error que contestó el servicio) se borra.
let formNote = null;

function say(text, isError = false) {
  formNote = typeof text === 'function' ? { text, isError } : null;
  formMsg.textContent = typeof text === 'function' ? text() : text;
  formMsg.className = isError ? 'error' : '';
}

function resay() {
  if (formNote) say(formNote.text, formNote.isError);
  else say('');
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const accounts = accountsInput.value.trim();
  const since = sinceInput.value;
  const last = untilInclusive();
  if (!accounts) return say(() => t('page.need_accounts'), true);
  if (!since) return say(() => t('page.need_since'), true);
  if (since > last) return say(() => t('page.order'), true);
  if (last > today()) return say(() => t('page.future'), true);
  try {
    const body = { accounts: [accounts], since, until: addDays(last, 1) };
    const caps = await api('/captures', {
      method: 'POST',
      body: JSON.stringify({ ...body, ...(excelInput.checked ? EXCEL_ES : {}) }),
    });
    const names = caps.map((c) => `@${c.account}`).join(', ');
    say(() => t('page.queued', { names }));
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
    headers: {
      'content-type': 'application/json',
      'accept-language': lang,
      ...(init.headers ?? {}),
    },
  });
  if (res.status === 204) return null;
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body?.detail;
    const text = Array.isArray(detail)
      ? detail.map((d) => String(d.msg)).join('. ')
      : detail || `${res.status} ${res.statusText}`;
    throw new Error(text);
  }
  return body;
}

// --- Capturas -------------------------------------------------------------------------

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
    const exported = t('page.exported', { count: num(c.exported) });
    return c.excluded_replies > 0
      ? `${exported} ${t('page.excluded', { count: num(c.excluded_replies) })}`
      : exported;
  }
  if (c.state === 'queued') {
    return c.captured > 0 ? t('page.saved_before', { count: num(c.captured) }) : null;
  }
  if (c.oldest === null) return c.state === 'stopped' ? null : t('page.nothing_yet');
  return t('page.progress', { date: show(c.oldest), percent: c.percent, count: num(c.captured) });
}

async function cancel(c) {
  if (!confirm(t('page.cancel_confirm', { account: c.account }))) return;
  try {
    await api(`/captures/${c.id}/state`, {
      method: 'POST',
      body: JSON.stringify({ state: 'stopped', note: { key: 'note.cancelled' }, by_user: true }),
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
    lines.push(el('p', {}, t('page.paused')));
  }
  if (c.state === 'waiting') {
    lines.push(
      el(
        'p',
        {},
        c.resume_at
          ? t('page.waiting_until', { time: showTime(c.resume_at) })
          : t('page.waiting'),
      ),
    );
  }
  if (c.note) lines.push(el('p', { class: c.state === 'stopped' ? 'error' : '' }, note(c.note)));
  if (c.last_page_at && ACTIVE.includes(c.state)) {
    lines.push(el('p', { class: 'hint' }, t('page.last_page', { time: showTime(c.last_page_at) })));
  }
  if (ACTIVE.includes(c.state)) {
    const fill = el('div');
    fill.style.width = `${c.percent}%`;
    lines.push(el('div', { class: 'bar', 'aria-hidden': 'true' }, fill));
  }
  const actions = [];
  if (c.download_url) {
    actions.push(el('a', { href: c.download_url, download: c.csv }, t('page.download')));
  }
  if (c.state === 'queued' || ACTIVE.includes(c.state)) {
    const b = el('button', { type: 'button' }, t('page.cancel'));
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
      el('span', { class: `state ${c.state}` }, t(`state.${c.state}`)),
    ),
    el('p', { class: 'hint' }, t('page.item_range', { since: show(c.since), until: show(last) })),
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
  const percent = pct.toLocaleString(LOCALE(), { maximumFractionDigits: 1 });
  $('gate').textContent =
    `${t('page.gate', { usage: num(g.usage), cap: num(g.hard_cap), percent })} ` +
    (g.remaining > 0 ? t('page.gate_left', { count: num(g.remaining) }) : t('page.gate_full'));
  $('gate-bar').style.width = `${Math.min(100, pct)}%`;
  $('days').replaceChildren(
    ...days.map((d) =>
      el('tr', {}, el('td', {}, show(d.day)), el('td', { class: 'num' }, num(d.accessed))),
    ),
  );
}

// Quién captura: el navegador propio del servicio (sin ventana) o la extensión del navegador
// de siempre, que necesita la pestaña a la vista (punto 13 de docs/PLAN.md).
const OWN_BROWSER = ['idle', 'running', 'login'];

// Sin sesión de X en el perfil de xport (la primera vez), la página ofrece iniciarla antes de
// la primera captura; también si la persona cerró la ventana sin terminar (punto 14).
function renderBrowser(b) {
  const firstLogin = b.state === 'idle' && b.session === false;
  $('browser').textContent = firstLogin
    ? t('page.browser_first_login')
    : OWN_BROWSER.includes(b.state)
      ? t(`page.browser_${b.state}`)
      : `${b.note ? `${note(b.note)} ` : ''}${t('page.browser_extension')}`;
  $('login-row').hidden = !(firstLogin || (b.state === 'failed' && b.session === false));
  if ($('login-row').hidden) $('login-msg').textContent = '';
}

$('login').addEventListener('click', async () => {
  $('login').disabled = true;
  $('login-msg').textContent = '';
  try {
    renderBrowser(await api('/browser/login', { method: 'POST' }));
  } catch (e) {
    $('login-msg').textContent = e.message;
  } finally {
    $('login').disabled = false;
  }
});

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

// --- Textos fijos y selector de idioma --------------------------------------------------

function applyStrings() {
  document.documentElement.lang = lang;
  for (const node of document.querySelectorAll('[data-i18n]')) {
    node.textContent = t(node.dataset.i18n);
  }
  for (const node of document.querySelectorAll('[data-i18n-aria-label]')) {
    node.setAttribute('aria-label', t(node.dataset.i18nAriaLabel));
  }
  $('lang').value = lang;
  fillYears();
  renderRange();
  resay();
}

$('lang').addEventListener('change', async () => {
  lang = $('lang').value === 'es' ? 'es' : 'en';
  try {
    localStorage.setItem(LANG_KEY, lang);
  } catch {
    // sin almacenamiento, la elección dura lo que dure la pestaña
  }
  await loadStrings();
  applyStrings();
  await refresh();
});

try {
  await loadStrings();
} catch {
  $('offline').hidden = false;
}
applyStrings();
void refresh();
setInterval(() => void refresh(), REFRESH_MS);
