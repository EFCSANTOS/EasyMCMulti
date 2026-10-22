const appState = {
  auth: { username: '', password: '' },
  config: {},
  status: {},
  latestVersionList: [],
  busy: false,
};

// Browsers cannot send Basic auth on a WebSocket, so the console is polled over REST.
const CONSOLE_POLL_MS = 2000;
const STATUS_POLL_MS = 5000;
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
const RUNNING_ONLY_SELECTOR = '#command-form input, #command-form button, #admin button';

const metricsState = {
  paused: false,
  cpu: [],
  ram: [],
  ramTotal: 0,
  maxPoints: 60,
  timer: null,
};

// ---------- Auth & HTTP ----------
function readSessionCredentials() {
  try {
    const username = sessionStorage.getItem('ez_auth_username') || '';
    const password = sessionStorage.getItem('ez_auth_password') || '';
    appState.auth = { username, password };
  } catch (error) {
    appState.auth = { username: '', password: '' };
  }
  return appState.auth;
}

function buildBasicAuth() {
  const { username, password } = appState.auth.username ? appState.auth : readSessionCredentials();
  return 'Basic ' + btoa(`${username}:${password}`);
}

function signOut() {
  try {
    sessionStorage.removeItem('ez_auth_username');
    sessionStorage.removeItem('ez_auth_password');
  } catch (error) {
    // storage unavailable; nothing to clear
  }
  window.location.href = '/login';
}

async function fetchJson(url, method = 'GET', body = null) {
  const options = { method, headers: { Authorization: buildBasicAuth() } };
  if (body instanceof FormData) {
    options.body = body;
  } else if (body !== null) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }

  const response = await fetch(url, options);
  if (response.status === 401) {
    signOut();
    throw new Error('Session expired. Please sign in again.');
  }
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch (err) {
    data = { detail: text };
  }
  if (!response.ok) {
    throw new Error(data.detail || data.error || `Request failed (HTTP ${response.status})`);
  }
  return data;
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
    await navigator.clipboard.writeText(text);
    return;
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

function itemRow(title, desc, meta, button) {
  const row = el('div', 'item');
  const body = el('div', 'item-body');
  body.appendChild(el('div', 'item-title', title));
  if (desc) body.appendChild(el('div', 'item-desc', desc));
  if (meta) body.appendChild(el('div', 'item-meta', meta));
  row.appendChild(body);
  if (button) row.appendChild(button);
  return row;
}

function rowButton(label, className, onClick) {
  const button = el('button', `btn btn-sm ${className}`, label);
  button.type = 'button';
  button.addEventListener('click', () => onClick(button));
  return button;
}

// ---------- Config ----------
// [element id, config key, kind] for every setting input; pages only render some of them.
const CONFIG_FIELDS = [
  ['loader-select', 'loader', 'text'],
  ['version-select', 'version', 'text'],
  ['ram-input', 'ram_mb', 'number'],
  ['port-input', 'server_port', 'number'],
  ['motd-input', 'motd', 'text'],
  ['maxplayers-input', 'max_players', 'number'],
  ['gamemode-input', 'gamemode', 'text'],
  ['difficulty-input', 'difficulty', 'text'],
  ['leveltype-input', 'level_type', 'text'],
  ['viewdist-input', 'view_distance', 'number'],
  ['simdist-input', 'simulation_distance', 'number'],
  ['seed-input', 'level_seed', 'text'],
  ['online-mode', 'online_mode', 'checkbox'],
  ['pvp-mode', 'pvp', 'checkbox'],
  ['spawn-monsters', 'spawn_monsters', 'checkbox'],
  ['allow-flight', 'allow_flight', 'checkbox'],
  ['nether-mode', 'allow_nether', 'checkbox'],
  ['whitelist-mode', 'white_list', 'checkbox'],
  ['command-blocks', 'enable_command_block', 'checkbox'],
  ['autostart-server', 'autostart_server', 'checkbox'],
  ['auto-restart', 'auto_restart', 'checkbox'],
  ['autostart-playit', 'autostart_playit', 'checkbox'],
];

function collectConfigFromPage() {
  const payload = {};
  CONFIG_FIELDS.forEach(([id, key, kind]) => {
    const element = document.getElementById(id);
    if (!element) return;
    if (kind === 'checkbox') payload[key] = element.checked;
    else if (kind === 'number') payload[key] = Number(element.value);
    else payload[key] = element.value;
  });
  return payload;
}

function populateVersions(current) {
  const select = document.getElementById('version-select');
  if (!select) return;
  const versions = appState.latestVersionList.length ? appState.latestVersionList : [current || '1.21.7'];
  select.replaceChildren();
  versions.forEach((version) => {
    const option = el('option', '', version);
    option.value = version;
    option.selected = version === current;
    select.appendChild(option);
  });
}

function populateConfigForm(config) {
  CONFIG_FIELDS.forEach(([id, key, kind]) => {
    const element = document.getElementById(id);
    if (!element || key === 'version' || config[key] === undefined) return;
    if (kind === 'checkbox') element.checked = Boolean(config[key]);
    else element.value = config[key];
  });
  populateVersions(config.version);
}

async function loadVersions(loader) {
  try {
    const result = await fetchJson(`/api/versions/${encodeURIComponent(loader)}`);
    appState.latestVersionList = result.versions || [];
  } catch (error) {
    appState.latestVersionList = [];
    setStatus(`Version list unavailable: ${error.message}`, 'error');
  }
}

async function onLoaderChange() {
  const loader = document.getElementById('loader-select').value;
  await loadVersions(loader);
  // Keep the saved version selected only if the new loader offers it.
  const saved = appState.config.loader === loader ? appState.config.version : null;
  populateVersions(saved || appState.latestVersionList[0]);
}

async function loadConfig() {
  try {
    appState.config = await fetchJson('/api/config');
    if (document.getElementById('version-select')) {
      await loadVersions(appState.config.loader || 'vanilla');
    }
    populateConfigForm(appState.config);
  } catch (error) {
    setStatus(`Failed to load settings: ${error.message}`, 'error');
  }
}

async function saveConfigFromForm(button) {
  await runAction(button, 'Saving…', async () => {
    appState.config = await fetchJson('/api/config', 'PUT', collectConfigFromPage());
    renderStatus(appState.status);
    return 'Settings saved.';
  });
}

// ---------- Status ----------
function currentState() {
  return appState.status.state || 'unknown';
}

function renderServerControls() {
  const state = currentState();
  const busy = BUSY_STATES.has(state) || appState.busy;
  const running = state === 'running';
  const toggle = (id, enabled) => {
    const button = document.getElementById(id);
    if (button && button.getAttribute('aria-busy') !== 'true') button.disabled = !enabled;
  };
  toggle('start-button', !busy && !running && state !== 'error' && state !== 'unknown');
  toggle('stop-button', !busy && running);
  toggle('restart-button', !busy && running);

  // Commands only reach a running server.
  document.querySelectorAll(RUNNING_ONLY_SELECTOR).forEach((element) => {
    if (element.getAttribute('aria-busy') !== 'true') element.disabled = !running;
  });
  const commandInput = document.getElementById('command-input');
  if (commandInput) commandInput.placeholder = running ? 'Type a server command, e.g. say hello' : 'Start the server to send commands';
}

function renderStatus(status) {
  const state = status.state || 'unknown';
  const pill = document.getElementById('server-state');
  if (pill) pill.dataset.state = state;
  const label = document.getElementById('server-state-label');
  if (label) label.textContent = STATE_LABELS[state] || state;
  renderServerControls();

  const port = appState.config.server_port || 25565;
  const ram = appState.config.ram_mb || 2048;
  const lan = status.lan_address;
  const publicIp = status.public_address;
  setField('server_name', status.loader ? `${status.loader} ${status.version}` : '—');
  setField('server_meta', `Port ${port} · ${ram} MB memory`);
  setField('players', String(status.players || 0));
  const names = status.player_names || [];
  setField('player_names', names.length ? names.join(', ') : 'No one online');
  setField('lan_join', lan ? `${lan}:${port}` : '—');
  setField('public_join', publicIp && publicIp !== 'unavailable' ? `${publicIp}:${port}` : 'Unavailable');

  const banner = document.getElementById('install-banner');
  if (banner) {
    const installing = status.install_status === 'installing';
    banner.hidden = !installing;
    if (installing) {
      setField('install_message', status.install_message || '');
      document.getElementById('install-progress-bar').style.width = `${Math.round(status.install_progress || 0)}%`;
    }
  }
}

async function loadStatus() {
  try {
    appState.status = await fetchJson('/api/status');
  } catch (error) {
    appState.status = { state: 'error' };
  }
  renderStatus(appState.status);
  if (document.getElementById('playit-status-panel')) await loadPlayitStatus();
  if (document.getElementById('remote-dashboard-toggle')) await loadRemoteDashboard();
}

// ---------- Server actions ----------
function serverAction(path, pendingMessage) {
  return (button) => runAction(button, pendingMessage, async () => {
    appState.busy = true;
    try {
      const response = await fetchJson(path, 'POST');
      return response.message || 'Done.';
    } finally {
      appState.busy = false;
      await loadStatus();
    }
  });
}

async function createServer(button) {
  await runAction(button, 'Creating server — downloads can take a few minutes…', async () => {
    const response = await fetchJson('/api/server/create', 'POST', collectConfigFromPage());
    await loadConfig();
    await loadStatus();
    return `Server ${response.loader} ${response.version} is ready.`;
  });
}

async function sendCommand(input) {
  const value = input.value.trim();
  if (!value) return;
  try {
    await fetchJson('/api/server/command', 'POST', { command: value });
    input.value = '';
    await refreshConsole(true);
  } catch (error) {
    setStatus(`Command failed: ${error.message}`, 'error');
  }
}

async function applyAdminAction(action, button) {
  const playerInput = document.getElementById('admin-player');
  const player = playerInput ? playerInput.value.trim() : '';
  if (['op', 'deop', 'kick', 'ban'].includes(action) && !player) {
    setStatus('Enter a player name first.', 'error');
    if (playerInput) playerInput.focus();
    return;
  }
  if (action === 'ban' && !window.confirm(`Ban ${player} from the server?`)) return;
  await runAction(button, null, async () => {
    const response = await fetchJson(`/api/admin/${action}`, 'POST', player ? { player } : {});
    return `Sent: ${response.command}`;
  });
}

// ---------- Backups ----------
function describeBackup(path) {
  const name = path.split(/[\\/]/).pop();
  const match = name.match(/(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/);
  const date = match ? new Date(match[1], match[2] - 1, match[3], match[4], match[5], match[6]) : null;
  return { name, when: date ? date.toLocaleString() : name };
}

async function loadBackups() {
  const list = document.getElementById('backup-list');
  if (!list) return;
  try {
    const result = await fetchJson('/api/server/backups');
    renderItems(list, result.backups || [], (path) => {
      const { name, when } = describeBackup(path);
      return itemRow(when, null, name, rowButton('Restore', 'btn-ghost', (button) => restoreBackup(path, when, button)));
    }, 'No backups yet. Create one before making big changes.');
  } catch (error) {
    setStatus(`Backup list unavailable: ${error.message}`, 'error');
  }
}

async function backupServer(button) {
  await runAction(button, 'Creating backup…', async () => {
    const response = await fetchJson('/api/server/backup', 'POST');
    await loadBackups();
    if (response.ok === false) throw new Error(response.message);
    return response.message || 'Backup created.';
  });
}

async function restoreBackup(path, when, button) {
  if (!window.confirm(`Restore the backup from ${when}?\n\nThe current world is moved aside first, so it is not lost.`)) return;
  await runAction(button, 'Restoring backup…', async () => {
    const response = await fetchJson('/api/server/backups/restore', 'POST', { backup_path: path });
    await loadBackups();
    return response.message || 'Backup restored.';
  });
}

// ---------- Mods ----------
async function loadInstalledMods() {
  const list = document.getElementById('mod-installed-list');
  if (!list) return;
  try {
    const result = await fetchJson('/api/server/mods');
    renderItems(list, result.mods || [], (mod) => {
      const folder = /[\\/]plugins[\\/]/.test(mod.path || '') ? 'plugins' : 'mods';
      return itemRow(mod.name, null, `${folder}/`, null);
    }, 'Nothing installed yet.');
  } catch (error) {
    setStatus(`Installed list unavailable: ${error.message}`, 'error');
  }
}

async function searchMods(button) {
  const query = document.getElementById('mod-search-query').value.trim();
  const list = document.getElementById('mod-search-results');
  if (!query) {
    setStatus('Type something to search for.', 'error');
    return;
  }
  await runAction(button, null, async () => {
    const result = await fetchJson(`/api/modrinth/search?query=${encodeURIComponent(query)}`);
    renderItems(list, result.items || [], (hit) => itemRow(
      hit.title,
      hit.description,
      `${Number(hit.downloads || 0).toLocaleString()} downloads`,
      rowButton('Install', 'btn-primary', (installButton) => installMod(hit, installButton)),
    ), 'No compatible results for your loader and version.');
    return null;
  });
}

async function installMod(hit, button) {
  await runAction(button, `Installing ${hit.title}…`, async () => {
    const response = await fetchJson('/api/modrinth/install', 'POST', { slug: hit.slug });
    await loadInstalledMods();
    button.textContent = 'Installed';
    return `${response.message || 'Installed.'} Restart the server to load it.`;
  });
}

async function uploadMods(input) {
  if (!input.files.length) return;
  const formData = new FormData();
  Array.from(input.files).forEach((file) => formData.append('files', file));
  await runAction(null, `Uploading ${input.files.length} file(s)…`, async () => {
    const response = await fetchJson('/api/server/mods/upload', 'POST', formData);
    await loadInstalledMods();
    return `${response.message || 'Uploaded.'} Restart the server to load them.`;
  });
  input.value = '';
}

// ---------- PlayIt ----------
const PLAYIT_LABELS = {
  running: 'Tunnel running',
  configured: 'Configured · stopped',
  stopped: 'Configured · stopped',
  'not-configured': 'Not configured',
};

const PLAYIT_SETUP_LABELS = {
  'awaiting-approval': 'Waiting for approval',
  connecting: 'Connecting…',
};

function renderPlayit(info) {
  const badge = document.getElementById('playit-status-panel');
  const setupLabel = PLAYIT_SETUP_LABELS[info.setup];
  badge.textContent = setupLabel || PLAYIT_LABELS[info.state] || info.state;
  badge.className = info.state === 'running' ? 'badge badge-live' : 'badge';
  setField('playit_address', info.address || (info.configured ? 'Assigned on first start' : 'Not configured'));

  const claim = document.getElementById('playit-claim');
  claim.hidden = !(info.setup === 'awaiting-approval' && info.claim_url);
  if (!claim.hidden) {
    setField('playit_claim_url', info.claim_url);
    document.getElementById('playit-claim-link').href = info.claim_url;
  }
  const errorBox = document.getElementById('playit-error');
  errorBox.hidden = info.setup !== 'error' || !info.error;
  errorBox.textContent = info.error ? `PlayIt setup failed: ${info.error}` : '';

  const settingUp = Boolean(setupLabel);
  const start = document.getElementById('playit-start-button');
  const stop = document.getElementById('playit-stop-button');
  const configure = document.getElementById('playit-config-button');
  if (start.getAttribute('aria-busy') !== 'true') start.disabled = settingUp || !info.configured || info.state === 'running';
  if (stop.getAttribute('aria-busy') !== 'true') stop.disabled = !settingUp && info.state !== 'running';
  stop.textContent = settingUp ? 'Cancel setup' : 'Stop tunnel';
  if (configure.getAttribute('aria-busy') !== 'true') configure.disabled = settingUp;
  configure.textContent = info.configured ? 'Change account' : 'Configure';
  configure.dataset.configured = info.configured ? 'true' : '';

}

async function loadPlayitStatus() {
  try {
    renderPlayit(await fetchJson('/api/playit/status'));
  } catch (error) {
    // the next status poll retries
  }
}

function playitAction(path, pendingMessage) {
  return (button) => runAction(button, pendingMessage, async () => {
    const response = await fetchJson(path, 'POST');
    renderPlayit(response);
    return response.message || 'Done.';
  });
}

async function configurePlayit(button) {
  const reset = button.dataset.configured === 'true';
  if (reset && !window.confirm(
    'Forget the saved PlayIt account and link a new one?\n\n'
    + 'The tunnel stops and the current addresses stop working until you approve the new link.',
  )) return;
  await runAction(button, 'Requesting a PlayIt approval link…', async () => {
    const response = await fetchJson('/api/playit/configure', 'POST', { reset });
    renderPlayit(response);
    return response.message || 'Done.';
  });
}

// ---------- Alerts (email + heartbeat) ----------
// [element id, notify_config key, kind]. The SMTP password is write-only: never filled from the server.
const ALERT_FIELDS = [
  ['alerts-email-enabled', 'email_enabled', 'checkbox'],
  ['alerts-email-to', 'email_to', 'text'],
  ['alerts-smtp-user', 'smtp_user', 'text'],
  ['alerts-smtp-host', 'smtp_host', 'text'],
  ['alerts-smtp-port', 'smtp_port', 'number'],
  ['alerts-notify-server', 'notify_server', 'checkbox'],
  ['alerts-notify-link', 'notify_link', 'checkbox'],
  ['alerts-notify-tunnels', 'notify_tunnels', 'checkbox'],
  ['alerts-heartbeat-url', 'heartbeat_url', 'text'],
];

function renderAlerts(settings) {
  ALERT_FIELDS.forEach(([id, key, kind]) => {
    const element = document.getElementById(id);
    if (kind === 'checkbox') element.checked = Boolean(settings[key]);
    else element.value = settings[key] ?? '';
  });
  const password = document.getElementById('alerts-smtp-password');
  password.value = '';
  password.placeholder = settings.password_set ? 'Saved (leave empty to keep it)' : '16-character app password';
}

async function loadAlerts() {
  if (!document.getElementById('alerts-form')) return;
  try {
    renderAlerts(await fetchJson('/api/notifications'));
  } catch (error) {
    setStatus(`Alert settings unavailable: ${error.message}`, 'error');
  }
}

function collectAlerts() {
  const payload = {};
  ALERT_FIELDS.forEach(([id, key, kind]) => {
    const element = document.getElementById(id);
    if (kind === 'checkbox') payload[key] = element.checked;
    else if (kind === 'number') payload[key] = Number(element.value);
    else payload[key] = element.value.trim();
  });
  const password = document.getElementById('alerts-smtp-password').value.replace(/\s+/g, '');
  if (password) payload.smtp_password = password; // Gmail shows app passwords with spaces
  return payload;
}

async function saveAlerts(button) {
  await runAction(button, 'Saving…', async () => {
    renderAlerts(await fetchJson('/api/notifications', 'PUT', collectAlerts()));
    return 'Alert settings saved.';
  });
}

async function testAlerts(button) {
  await runAction(button, 'Sending test…', async () => {
    renderAlerts(await fetchJson('/api/notifications', 'PUT', collectAlerts()));
    const response = await fetchJson('/api/notifications/test', 'POST');
    return response.message;
  });
}

// ---------- Remote dashboard (Cloudflare) ----------
const REMOTE_LABELS = { off: 'Off', starting: 'Starting…', running: 'Online', error: 'Problem' };

function renderRemoteDashboard(info) {
  const badge = document.getElementById('remote-dashboard-badge');
  const state = info.enabled ? info.state : 'off';
  badge.textContent = REMOTE_LABELS[state] || state;
  badge.className = state === 'running' ? 'badge badge-live' : 'badge';

  const toggle = document.getElementById('remote-dashboard-toggle');
  if (document.activeElement !== toggle && !toggle.disabled) toggle.checked = Boolean(info.enabled);

  const link = document.getElementById('remote-dashboard-link');
  let text = 'Off';
  if (info.enabled) text = info.url || 'Waiting for the link…';
  setField('remote_dashboard_url', text);
  link.href = info.url || '#';

  const errorBox = document.getElementById('remote-dashboard-error');
  errorBox.hidden = !(info.enabled && info.error);
  errorBox.textContent = info.error || '';
}

async function loadRemoteDashboard() {
  try {
    renderRemoteDashboard(await fetchJson('/api/remote-dashboard'));
  } catch (error) {
    // the next status poll retries
  }
}

async function toggleRemoteDashboard(toggle) {
  const enabled = toggle.checked;
  if (enabled && !window.confirm(
    'Share this dashboard on the internet?\n\n'
    + 'The https link is encrypted, but anyone who gets it can try to sign in, and there is no '
    + 'lockout for wrong passwords yet. Continue only with a long, unique password.',
  )) {
    toggle.checked = false;
    return;
  }
  toggle.disabled = true;
  await runAction(null, null, async () => {
    try {
      const response = await fetchJson('/api/remote-dashboard', 'POST', { enabled });
      renderRemoteDashboard(response);
      return response.message;
    } finally {
      toggle.disabled = false;
    }
  });
}

// ---------- Console ----------
let lastConsoleSignature = '';

function consoleLineClass(line) {
  if (line.startsWith('> ')) return 'line-cmd';
  if (/\b(ERROR|SEVERE|FATAL|Exception)\b/.test(line)) return 'line-error';
  if (/\bWARN(ING)?\b/.test(line)) return 'line-warn';
  if (/^\[(server|install|playit)\]/.test(line)) return 'line-meta';
  return '';
}

async function refreshConsole(forceScroll = false) {
  const output = document.getElementById('console-output');
  if (!output) return;
  try {
    const data = await fetchJson('/api/console');
    const lines = data.lines || [];
    const signature = `${lines.length}:${lines[lines.length - 1] || ''}`;
    if (signature === lastConsoleSignature && !forceScroll) return;
    lastConsoleSignature = signature;

    const autoscroll = document.getElementById('console-autoscroll');
    const atBottom = output.scrollTop + output.clientHeight >= output.scrollHeight - 8;
    const fragment = document.createDocumentFragment();
    if (!lines.length) fragment.appendChild(document.createTextNode('Waiting for logs...'));
    lines.forEach((line) => {
      const span = el('span', consoleLineClass(line), `${line}\n`);
      fragment.appendChild(span);
    });
    output.replaceChildren(fragment);
    if (forceScroll || (autoscroll ? autoscroll.checked : atBottom)) output.scrollTop = output.scrollHeight;
  } catch (error) {
    // the next poll retries
  }
}

// ---------- Metrics ----------
function formatBytes(value) {
  if (!Number.isFinite(value)) return '—';
  let bytes = Number(value);
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let unit = 0;
  while (bytes >= 1024 && unit < units.length - 1) {
    bytes /= 1024;
    unit += 1;
  }
  return `${bytes.toFixed(bytes >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function chartGeometry(canvas, count) {
  const width = canvas.clientWidth || 300;
  const height = canvas.clientHeight || 120;
  const pad = { top: 8, right: 4, bottom: 8, left: 4 };
  // Samples span the full width; once the buffer is full the chart scrolls left.
  const step = (width - pad.left - pad.right) / Math.max(1, count - 1);
  return { width, height, pad, x: (i) => pad.left + i * step };
}

function drawMetricChart(canvas, values, maxValue, hoverIndex = null) {
  if (!canvas) return;
  const context = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const { width, height, pad, x } = chartGeometry(canvas, values.length);
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  context.clearRect(0, 0, width, height);

  const usable = height - pad.top - pad.bottom;
  const safeMax = maxValue > 0 ? maxValue : 100;
  const y = (value) => pad.top + usable - (Math.min(Math.max(value, 0), safeMax) / safeMax) * usable;

  // Recessive gridlines at 0/50/100%.
  context.strokeStyle = 'rgba(154, 161, 171, 0.12)';
  context.lineWidth = 1;
  [0, 0.5, 1].forEach((fraction) => {
    const gy = Math.round(pad.top + usable * fraction) + 0.5;
    context.beginPath();
    context.moveTo(pad.left, gy);
    context.lineTo(width - pad.right, gy);
    context.stroke();
  });

  if (!values.length) {
    context.fillStyle = '#6b727c';
    context.font = '12px system-ui, sans-serif';
    context.textAlign = 'center';
    context.fillText('Collecting samples…', width / 2, height / 2);
    return;
  }

  const trace = () => {
    context.beginPath();
    values.forEach((value, index) => {
      if (index === 0) context.moveTo(x(index), y(value));
      else context.lineTo(x(index), y(value));
    });
  };

  const gradient = context.createLinearGradient(0, pad.top, 0, height - pad.bottom);
  gradient.addColorStop(0, 'rgba(57, 135, 229, 0.28)');
  gradient.addColorStop(1, 'rgba(57, 135, 229, 0)');
  trace();
  context.lineTo(x(values.length - 1), height - pad.bottom);
  context.lineTo(x(0), height - pad.bottom);
  context.closePath();
  context.fillStyle = gradient;
  context.fill();

  trace();
  context.strokeStyle = SERIES_COLOR;
  context.lineWidth = 2;
  context.lineJoin = 'round';
  context.stroke();

  if (hoverIndex !== null && values[hoverIndex] !== undefined) {
    const hx = Math.round(x(hoverIndex)) + 0.5;
    context.strokeStyle = 'rgba(236, 237, 238, 0.35)';
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(hx, pad.top);
    context.lineTo(hx, height - pad.bottom);
    context.stroke();
    context.beginPath();
    context.arc(hx, y(values[hoverIndex]), 4, 0, Math.PI * 2);
    context.fillStyle = SERIES_COLOR;
    context.fill();
    context.lineWidth = 2;
    context.strokeStyle = '#0d0e10';
    context.stroke();
  }
}

const CHARTS = {
  'cpu-chart': { values: () => metricsState.cpu, max: () => 100, format: (v) => `${v.toFixed(1)}%` },
  'ram-chart': { values: () => metricsState.ram, max: () => metricsState.ramTotal, format: (v) => formatBytes(v) },
};

function redrawCharts() {
  Object.entries(CHARTS).forEach(([id, chart]) => {
    drawMetricChart(document.getElementById(id), chart.values(), chart.max());
  });
}

function bindChartHover() {
  Object.entries(CHARTS).forEach(([id, chart]) => {
    const canvas = document.getElementById(id);
    if (!canvas) return;
    const tip = canvas.parentElement.querySelector('.chart-tip');
    const hide = () => {
      tip.hidden = true;
      drawMetricChart(canvas, chart.values(), chart.max());
    };
    canvas.addEventListener('pointermove', (event) => {
      const values = chart.values();
      if (!values.length) return;
      const rect = canvas.getBoundingClientRect();
      const { x } = chartGeometry(canvas, values.length);
      let nearest = 0;
      values.forEach((_, index) => {
        if (Math.abs(x(index) - (event.clientX - rect.left)) < Math.abs(x(nearest) - (event.clientX - rect.left))) nearest = index;
      });
      drawMetricChart(canvas, values, chart.max(), nearest);
      const secondsAgo = Math.round(((values.length - 1 - nearest) * METRICS_POLL_MS) / 1000);
      const strong = el('strong', '', chart.format(values[nearest]));
      const when = el('span', '', secondsAgo === 0 ? 'now' : `${secondsAgo}s ago`);
      tip.replaceChildren(strong, when);
      tip.style.left = `${Math.min(Math.max(x(nearest), 50), rect.width - 50)}px`;
      tip.hidden = false;
    });
    canvas.addEventListener('pointerleave', hide);
  });
}

function summarize(values) {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const avg = values.reduce((sum, value) => sum + value, 0) / values.length;
  return { min, max, avg };
}

function updateMetricsUi(sample) {
  metricsState.cpu.push(sample.cpu);
  metricsState.ram.push(sample.ram);
  metricsState.ramTotal = sample.ramTotal;
  [metricsState.cpu, metricsState.ram].forEach((series) => {
    if (series.length > metricsState.maxPoints) series.shift();
  });

  const cpu = summarize(metricsState.cpu);
  document.getElementById('cpu-value').textContent = `${sample.cpu.toFixed(1)}%`;
  document.getElementById('cpu-sub').textContent = 'now';
  document.getElementById('cpu-min').textContent = `${cpu.min.toFixed(1)}%`;
  document.getElementById('cpu-avg').textContent = `${cpu.avg.toFixed(1)}%`;
  document.getElementById('cpu-max').textContent = `${cpu.max.toFixed(1)}%`;

  const ram = summarize(metricsState.ram);
  const pct = (value) => `${((value / sample.ramTotal) * 100).toFixed(1)}%`;
  document.getElementById('ram-value').textContent = `${formatBytes(sample.ram)} / ${formatBytes(sample.ramTotal)}`;
  document.getElementById('ram-sub').textContent = `${pct(sample.ram)} used`;
  document.getElementById('ram-min').textContent = pct(ram.min);
  document.getElementById('ram-avg').textContent = pct(ram.avg);
  document.getElementById('ram-max').textContent = pct(ram.max);

  redrawCharts();
}

async function refreshMetrics() {
  if (metricsState.paused) return;
  const badge = document.getElementById('metrics-badge');
  try {
    const data = await fetchJson('/api/system/metrics');
    updateMetricsUi({ cpu: Number(data.cpu) || 0, ram: Number(data.ram) || 0, ramTotal: Number(data.ramTotal) || 1 });
    badge.textContent = 'live';
    badge.className = 'badge badge-live';
  } catch (error) {
    badge.textContent = 'offline';
    badge.className = 'badge';
  }
}

function bindMetrics() {
  if (!document.getElementById('metrics')) return;
  const toggle = document.getElementById('metrics-toggle');
  toggle.addEventListener('click', () => {
    metricsState.paused = !metricsState.paused;
    toggle.textContent = metricsState.paused ? 'Resume' : 'Pause';
    const badge = document.getElementById('metrics-badge');
    badge.textContent = metricsState.paused ? 'paused' : 'live';
    badge.className = metricsState.paused ? 'badge' : 'badge badge-live';
    if (!metricsState.paused) refreshMetrics();
  });
  bindChartHover();
  redrawCharts();
  metricsState.timer = setInterval(refreshMetrics, METRICS_POLL_MS);
  refreshMetrics();
  window.addEventListener('resize', redrawCharts);
}

// ---------- Wiring ----------
function on(id, eventName, handler) {
  const element = document.getElementById(id);
  if (element) element.addEventListener(eventName, (event) => handler(event, element));
}

function bindEvents() {
  on('setup-form', 'submit', (event) => {
    event.preventDefault();
    saveConfigFromForm(event.submitter);
  });
  on('advanced-save-button', 'click', (event, button) => saveConfigFromForm(button));
  on('always-on-save-button', 'click', (event, button) => saveConfigFromForm(button));
  on('alerts-form', 'submit', (event) => {
    event.preventDefault();
    saveAlerts(document.getElementById('alerts-save-button'));
  });
  on('alerts-test-button', 'click', (event, button) => testAlerts(button));
  on('loader-select', 'change', () => onLoaderChange());
  on('create-button', 'click', (event, button) => createServer(button));

  on('start-button', 'click', (event, button) => serverAction('/api/server/start', 'Starting server — the first start downloads files and can take a while…')(button));
  on('stop-button', 'click', (event, button) => serverAction('/api/server/stop', 'Stopping server…')(button));
  on('restart-button', 'click', (event, button) => serverAction('/api/server/restart', 'Restarting server — waiting for the world to save…')(button));
  on('backup-button', 'click', (event, button) => backupServer(button));

  on('command-form', 'submit', (event) => {
    event.preventDefault();
    sendCommand(document.getElementById('command-input'));
  });

  on('playit-config-button', 'click', (event, button) => configurePlayit(button));
  on('remote-dashboard-toggle', 'change', (event, toggle) => toggleRemoteDashboard(toggle));
  on('playit-start-button', 'click', (event, button) => playitAction('/api/playit/start', 'Starting tunnel…')(button));
  on('playit-stop-button', 'click', (event, button) => playitAction('/api/playit/stop', 'Stopping tunnel…')(button));

  on('mod-search-form', 'submit', (event) => {
    event.preventDefault();
    searchMods(document.getElementById('mod-search-button'));
  });
  on('mod-refresh-button', 'click', () => loadInstalledMods());
  on('mod-upload-input', 'change', (event, input) => uploadMods(input));

  ['op', 'deop', 'kick', 'ban', 'day', 'night', 'clear', 'rain', 'save'].forEach((action) => {
    on(`admin-${action}`, 'click', (event, button) => applyAdminAction(action, button));
  });

  on('logout-button', 'click', () => signOut());
  on('action-status', 'click', (event, toast) => { toast.hidden = true; });
  bindCopyButtons();
}

async function boot() {
  readSessionCredentials();
  if (!appState.auth.username || !appState.auth.password) {
    window.location.href = '/login';
    return;
  }
  const session = document.getElementById('session-status');
  if (session) session.textContent = `Signed in as ${appState.auth.username}`;

  bindEvents();
  bindMetrics();
  await loadConfig();
  await loadStatus();
  await Promise.all([loadBackups(), loadInstalledMods(), loadAlerts(), refreshConsole(true)]);

  if (document.getElementById('console-output')) setInterval(refreshConsole, CONSOLE_POLL_MS);
  setInterval(loadStatus, STATUS_POLL_MS);
}

document.addEventListener('DOMContentLoaded', boot);
