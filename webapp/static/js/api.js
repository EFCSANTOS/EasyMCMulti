// EZ Minecraft dashboard, part 1 of 8: Shared state, auth/HTTP (fetchJson), toasts and list helpers.
// Plain scripts that share one global scope, loaded in order by webapp/pages/layout.py::SCRIPTS.

const appState = {
  username: '',
  role: 'owner',
  canStop: true,
  config: {},
  status: {},
  latestVersionList: [],
  busy: false,
};

// Fallback poll interval for the console when its WebSocket can't connect (see console.js).
const CONSOLE_POLL_MS = 2000;
const STATUS_POLL_MS = 5000;
// While a server operation runs in the background (install, start, restart), poll faster.
const STATUS_BUSY_POLL_MS = 1000;
const METRICS_POLL_MS = 2000;
const SERIES_COLOR = '#3987e5';

const STATE_LABELS = {
  running: 'Running',
  stopped: 'Stopped',
  starting: 'Starting…',
  stopping: 'Stopping…',
  installing: 'Installing…',
  ready: 'Ready',
  crashed: 'Crashed',
  error: 'Unreachable',
};
const BUSY_STATES = new Set(['starting', 'stopping', 'installing']);
const RUNNING_ONLY_SELECTOR = '#command-form input, #command-form button, #chat-form input, #chat-form button, #admin button';

const metricsState = {
  paused: false,
  cpu: [],
  ram: [],
  temp: [],
  ramTotal: 0,
  maxPoints: 60,
  timer: null,
};

// ---------- Auth & HTTP ----------
// Auth is an HttpOnly session cookie set by POST /api/login; the browser sends it on its own.
function goToLogin() {
  window.location.href = '/login';
}

async function signOut() {
  try {
    await fetch('/api/logout', { method: 'POST', credentials: 'same-origin' });
  } catch (error) {
    // offline: the session expires on its own
  }
  goToLogin();
}

async function fetchJson(url, method = 'GET', body = null) {
  const options = { method, credentials: 'same-origin', headers: {} };
  if (body instanceof FormData) {
    options.body = body;
  } else if (body !== null || method !== 'GET') {
    // State changes always send JSON: the server refuses other content types (CSRF guard).
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body || {});
  }

  const response = await fetch(url, options);
  if (response.status === 401) {
    goToLogin();
    throw new Error('Session expired. Please sign in again.');
  }
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch (err) {
    data = { detail: text };
  }
  if (!response.ok) throw errorFromResponse(data, response.status);
  return data;
}

// FastAPI-style 422 bodies list each bad field: detail = [{loc: ['body', field], msg}].
// The error carries those field names so forms can highlight the inputs.
function errorFromResponse(data, status) {
  if (Array.isArray(data.detail)) {
    const error = new Error(data.detail.map((item) => item.msg).join(' · ') || `Invalid input (HTTP ${status})`);
    error.fields = data.detail.map((item) => (item.loc || [])[(item.loc || []).length - 1]).filter(Boolean);
    return error;
  }
  return new Error(data.detail || data.error || `Request failed (HTTP ${status})`);
}

// Marks the config inputs the server rejected; each clears itself once edited.
function markInvalidFields(error) {
  const fields = new Set(error && error.fields ? error.fields : []);
  CONFIG_FIELDS.forEach(([id, key]) => {
    const element = document.getElementById(id);
    if (!element) return;
    if (fields.has(key)) {
      element.setAttribute('aria-invalid', 'true');
      element.addEventListener('input', () => element.removeAttribute('aria-invalid'), { once: true });
    } else {
      element.removeAttribute('aria-invalid');
    }
  });
}

// Sends a config payload and highlights rejected inputs before rethrowing.
async function fetchConfig(url, method, body) {
  try {
    const result = await fetchJson(url, method, body);
    markInvalidFields(null);
    return result;
  } catch (error) {
    markInvalidFields(error);
    throw error;
  }
}

// ---------- Feedback ----------
let toastTimer = null;

// Shows a toast. kind: 'ok' | 'error' | 'pending' (a boolean true still means error).
function setStatus(message, kind = 'ok') {
  const toast = document.getElementById('action-status');
  if (!toast) return;
  if (kind === true) kind = 'error';
  if (kind === false) kind = 'ok';
  clearTimeout(toastTimer);
  toast.textContent = message;
  toast.classList.toggle('is-error', kind === 'error');
  toast.classList.toggle('is-pending', kind === 'pending');
  toast.hidden = !message;
  if (message && kind !== 'pending') {
    toastTimer = setTimeout(() => { toast.hidden = true; }, kind === 'error' ? 9000 : 4500);
  }
}

// Runs an async action with the button disabled and showing a spinner.
async function runAction(button, pendingMessage, action) {
  if (button) {
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
  }
  if (pendingMessage) setStatus(pendingMessage, 'pending');
  try {
    const message = await action();
    if (message) setStatus(message);
  } catch (error) {
    setStatus(error.message, 'error');
  } finally {
    if (button) {
      button.removeAttribute('aria-busy');
      button.disabled = false;
    }
    renderServerControls();
  }
}

function setField(name, value) {
  document.querySelectorAll(`[data-field="${name}"]`).forEach((element) => {
    element.textContent = value;
  });
}

async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch (error) {
      // permission denied (some phones, embedded browsers): use the fallback below
    }
  }
  // Plain-HTTP LAN pages have no Clipboard API; fall back to a temporary textarea.
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.style.position = 'fixed';
  area.style.opacity = '0';
  document.body.appendChild(area);
  area.select();
  document.execCommand('copy');
  area.remove();
}

function bindCopyButtons() {
  document.querySelectorAll('[data-copy-field]').forEach((button) => {
    button.addEventListener('click', async () => {
      const source = button.parentElement.querySelector(`[data-field="${button.dataset.copyField}"]`);
      const text = source ? source.textContent.trim() : '';
      if (!text || text === '—' || /unavailable|^not |^off$|^waiting for|^assigned on/i.test(text)) {
        setStatus('Nothing to copy yet.', 'error');
        return;
      }
      try {
        await copyText(text);
        button.classList.add('copied');
        setTimeout(() => button.classList.remove('copied'), 1200);
        setStatus(`Copied ${text}`);
      } catch (error) {
        setStatus('Copy failed. Select the address and copy it manually.', 'error');
      }
    });
  });
}

// ---------- Lists ----------
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderItems(container, items, renderItem, emptyText) {
  container.replaceChildren();
  if (!items.length) {
    container.appendChild(el('p', 'empty', emptyText));
    return;
  }
  items.forEach((item) => container.appendChild(renderItem(item)));
}

// buttons: one element or an array of elements, shown on the right.
function itemRow(title, desc, meta, buttons) {
  const row = el('div', 'item');
  const body = el('div', 'item-body');
  body.appendChild(title instanceof Node ? title : el('div', 'item-title', title));
  if (desc) body.appendChild(el('div', 'item-desc', desc));
  if (meta) body.appendChild(el('div', 'item-meta', meta));
  row.appendChild(body);
  if (buttons) {
    const actions = el('div', 'item-actions');
    [].concat(buttons).forEach((button) => actions.appendChild(button));
    row.appendChild(actions);
  }
  return row;
}

function compactNumber(value) {
  return new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(value || 0);
}

function relativeTime(value) {
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const days = Math.round((Date.now() - date.getTime()) / 86400000);
  if (days < 1) return 'today';
  if (days < 2) return 'yesterday';
  if (days < 31) return `${days} days ago`;
  if (days < 365) return `${Math.round(days / 30)} months ago`;
  return `${Math.round(days / 365)} years ago`;
}

function capitalize(text) {
  return text ? text.charAt(0).toUpperCase() + text.slice(1).replace(/-/g, ' ') : '';
}

function rowButton(label, className, onClick) {
  const button = el('button', `btn btn-sm ${className}`, label);
  button.type = 'button';
  button.addEventListener('click', () => onClick(button));
  return button;
}
