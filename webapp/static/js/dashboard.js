const appState = {
  username: '',
  config: {},
  status: {},
  latestVersionList: [],
  busy: false,
};

// The console is polled over REST (the /ws/console socket also accepts the session cookie).
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
  const busy = BUSY_STATES.has(state) || appState.busy || operationRunning(appState.status);
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

function operationRunning(status) {
  return Boolean(status && status.operation && status.operation !== 'idle');
}

// Lifecycle requests return at once (202) and finish in the background; remember that one is
// running so the status poll can report how it ended.
function trackOperation(response) {
  if (response && response.operation) appState.status = { ...appState.status, operation: response.operation };
  scheduleStatusPoll();
}

function reportFinishedOperation(previous, status) {
  if (!operationRunning(previous) || operationRunning(status)) return;
  if (status.operation_error) setStatus(status.operation_error, 'error');
  else if (status.operation_message) setStatus(status.operation_message);
}

async function loadStatus() {
  const previous = appState.status;
  try {
    appState.status = await fetchJson('/api/status');
  } catch (error) {
    appState.status = { state: 'error' };
  }
  renderStatus(appState.status);
  reportFinishedOperation(previous, appState.status);
  if (document.getElementById('playit-status-panel')) await loadPlayitStatus();
  if (document.getElementById('remote-dashboard-toggle')) await loadRemoteDashboard();
}

let statusTimer = null;

// One status poll chain: every STATUS_POLL_MS, or every STATUS_BUSY_POLL_MS while busy.
function scheduleStatusPoll() {
  clearTimeout(statusTimer);
  const busy = operationRunning(appState.status) || appState.status.install_status === 'installing';
  statusTimer = setTimeout(async () => {
    await loadStatus();
    scheduleStatusPoll();
  }, busy ? STATUS_BUSY_POLL_MS : STATUS_POLL_MS);
}

// ---------- Server actions ----------
function serverAction(path, pendingMessage) {
  return (button) => runAction(button, pendingMessage, async () => {
    appState.busy = true;
    try {
      const response = await fetchJson(path, 'POST');
      trackOperation(response);
      return response.message || 'Done.';
    } finally {
      appState.busy = false;
      renderServerControls();
    }
  });
}

async function createServer(button) {
  await runAction(button, null, async () => {
    const response = await fetchJson('/api/server/create', 'POST', collectConfigFromPage());
    trackOperation(response);
    await loadConfig();
    return response.message;
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
const modState = { offset: 0, total: 0, installedIds: new Set(), loading: false };

async function loadInstalledMods() {
  const list = document.getElementById('mod-installed-list');
  if (!list) return;
  try {
    const result = await fetchJson('/api/server/mods');
    modState.installedIds = new Set(result.project_ids || []);
    renderItems(list, result.mods || [], (mod) => itemRow(
      mod.title || mod.name,
      mod.title ? mod.name : null,
      `${mod.folder}/${mod.version ? ` · ${mod.version}` : ''}${mod.project_id ? ' · from Modrinth' : ' · uploaded'}`,
      rowButton('Remove', 'btn-danger', (button) => removeMod(mod, button)),
    ), 'Nothing installed yet.');
    refreshInstalledMarks();
  } catch (error) {
    setStatus(`Installed list unavailable: ${error.message}`, 'error');
  }
}

async function removeMod(mod, button) {
  if (!window.confirm(`Remove ${mod.title || mod.name}?`)) return;
  await runAction(button, null, async () => {
    const response = await fetchJson(`/api/server/mods/${encodeURIComponent(mod.name)}`, 'DELETE');
    await loadInstalledMods();
    return `${response.message} Restart the server to apply.`;
  });
}

function modIcon(hit, size) {
  if (hit.icon_url) {
    const img = el('img', 'mod-icon');
    img.src = hit.icon_url;
    img.alt = '';
    img.loading = 'lazy';
    img.width = size;
    img.height = size;
    return img;
  }
  return el('div', 'mod-icon mod-icon-empty', (hit.title || '?').charAt(0).toUpperCase());
}

function installButton(hit, extraClass = '') {
  const installed = modState.installedIds.has(hit.id);
  const button = el('button', `btn btn-sm ${installed ? 'btn-ghost' : 'btn-primary'} ${extraClass}`, installed ? 'Installed' : 'Install');
  button.type = 'button';
  button.dataset.projectId = hit.id || '';
  button.disabled = installed;
  button.addEventListener('click', (event) => {
    event.stopPropagation();
    installMod(hit, button);
  });
  return button;
}

function refreshInstalledMarks() {
  document.querySelectorAll('button[data-project-id]').forEach((button) => {
    if (modState.installedIds.has(button.dataset.projectId)) {
      button.textContent = 'Installed';
      button.disabled = true;
      button.className = button.className.replace('btn-primary', 'btn-ghost');
    }
  });
}

function modCard(hit) {
  const card = el('article', 'mod-card');
  card.tabIndex = 0;
  card.setAttribute('role', 'button');
  card.setAttribute('aria-label', `${hit.title}: show details`);
  const head = el('div', 'mod-card-head');
  const titles = el('div', 'mod-titles');
  titles.append(el('div', 'mod-title', hit.title), el('div', 'mod-author', hit.author ? `by ${hit.author}` : ''));
  head.append(modIcon(hit, 48), titles);
  const meta = el('div', 'mod-meta',
    `${compactNumber(hit.downloads)} downloads · ${compactNumber(hit.follows)} followers · updated ${relativeTime(hit.updated)}`);
  const chips = el('div', 'chips');
  (hit.categories || []).slice(0, 3).forEach((name) => chips.appendChild(el('span', 'chip', capitalize(name))));
  const footer = el('div', 'mod-card-foot');
  footer.append(chips, installButton(hit));
  card.append(head, el('p', 'mod-desc', hit.description), meta, footer);
  const open = () => openModDetails(hit);
  card.addEventListener('click', open);
  card.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      open();
    }
  });
  return card;
}

async function browseMods(reset = true) {
  const grid = document.getElementById('mod-search-results');
  if (!grid || modState.loading) return;
  if (reset) {
    modState.offset = 0;
    grid.replaceChildren(el('p', 'empty', 'Loading…'));
  }
  const params = new URLSearchParams({
    query: document.getElementById('mod-search-query').value.trim(),
    sort: document.getElementById('mod-sort').value,
    category: document.getElementById('mod-category').value,
    offset: String(modState.offset),
  });
  const loadMore = document.getElementById('mod-load-more');
  modState.loading = true;
  loadMore.disabled = true;
  try {
    const result = await fetchJson(`/api/modrinth/search?${params}`);
    if (reset) grid.replaceChildren();
    document.getElementById('mod-context').textContent =
      `Mods and plugins that run on your server (${capitalize(result.loader)} ${result.version}). Client-only mods are hidden.`;
    (result.hits || []).forEach((hit) => grid.appendChild(modCard(hit)));
    modState.offset += (result.hits || []).length;
    modState.total = result.total || 0;
    if (!grid.children.length) grid.appendChild(el('p', 'empty', 'No compatible results. Try another search or category.'));
    document.getElementById('mod-result-count').textContent = modState.total
      ? `Showing ${modState.offset} of ${modState.total.toLocaleString()}` : '';
    loadMore.hidden = modState.offset >= modState.total;
  } catch (error) {
    grid.replaceChildren(el('p', 'empty', error.message));
    loadMore.hidden = true;
  } finally {
    modState.loading = false;
    loadMore.disabled = false;
  }
}

async function loadModCategories() {
  const select = document.getElementById('mod-category');
  if (!select) return;
  try {
    const result = await fetchJson('/api/modrinth/categories');
    (result.categories || []).forEach((name) => {
      const option = el('option', '', capitalize(name));
      option.value = name;
      select.appendChild(option);
    });
  } catch (error) {
    // the filter just stays at "All categories"
  }
}

function detailLink(label, href) {
  const link = el('a', 'link', label);
  link.href = href;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  return link;
}

async function openModDetails(hit) {
  const dialog = document.getElementById('mod-details');
  const body = document.getElementById('mod-details-body');
  const head = el('div', 'mod-card-head');
  const titles = el('div', 'mod-titles');
  const title = el('h2', 'mod-title', hit.title);
  title.id = 'mod-details-title';
  titles.append(title, el('div', 'mod-author', hit.author ? `by ${hit.author}` : ''));
  head.append(modIcon(hit, 64), titles, installButton(hit, 'details-install'));
  const content = el('div', 'mod-details-content', 'Loading details…');
  body.replaceChildren(head, el('p', 'mod-desc-full', hit.description), content);
  dialog.showModal();
  try {
    const details = await fetchJson(`/api/modrinth/project/${encodeURIComponent(hit.slug)}`);
    if (!hit.icon_url && details.icon_url) head.replaceChild(modIcon(details, 64), head.firstChild);
    const stats = el('div', 'mod-meta',
      `${compactNumber(details.downloads)} downloads · ${compactNumber(details.follows)} followers · updated ${relativeTime(details.updated)}`
      + `${details.license ? ` · ${details.license}` : ''}`);
    const compat = el('p', details.compatible_versions ? 'hint' : 'callout callout-error',
      details.compatible_versions
        ? `${details.compatible_versions} version(s) for your server; Install picks ${details.latest_version}. Required dependencies are installed too.`
        : 'No version of this project matches your loader and Minecraft version.');
    const chips = el('div', 'chips');
    (details.categories || []).forEach((name) => chips.appendChild(el('span', 'chip', capitalize(name))));
    const gallery = el('div', 'mod-gallery');
    (details.gallery || []).forEach((image) => {
      const img = el('img');
      img.src = image.url;
      img.alt = image.title || '';
      img.loading = 'lazy';
      gallery.appendChild(img);
    });
    const links = el('div', 'mod-links');
    links.appendChild(detailLink('Modrinth page', details.page_url));
    const names = { source_url: 'Source', wiki_url: 'Wiki', discord_url: 'Discord', issues_url: 'Issues' };
    Object.entries(details.links || {}).forEach(([key, href]) => links.appendChild(detailLink(names[key] || key, href)));
    const description = el('div', 'mod-body', details.body || '');
    content.replaceChildren(stats, chips, compat, ...(gallery.children.length ? [gallery] : []), description, links);
  } catch (error) {
    content.replaceChildren(el('p', 'callout callout-error', error.message));
  }
}

async function installMod(hit, button) {
  await runAction(button, `Installing ${hit.title}…`, async () => {
    const response = await fetchJson('/api/modrinth/install', 'POST', { slug: hit.slug });
    await loadInstalledMods();
    return `${response.message || 'Installed.'} Restart the server to load it.`;
  });
}

function initModBrowser() {
  if (!document.getElementById('mod-search-results')) return;
  const dialog = document.getElementById('mod-details');
  document.getElementById('mod-details-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) dialog.close(); // click on the backdrop
  });
  on('mod-category', 'change', () => browseMods(true));
  on('mod-sort', 'change', () => browseMods(true));
  on('mod-load-more', 'click', () => browseMods(false));
  loadModCategories();
  browseMods(true);
}

// ---------- Worlds ----------
function formatSize(bytes) {
  return bytes ? formatBytes(bytes) : '';
}

function worldTitle(world) {
  const title = el('div', 'item-title');
  title.appendChild(document.createTextNode(world.name));
  if (world.active) title.appendChild(el('span', 'badge badge-live world-badge', 'Active'));
  return title;
}

function worldMeta(world) {
  const parts = [`folder ${world.folder}`];
  if (!world.exists) parts.push(world.seed ? `not generated yet · seed ${world.seed}` : 'not generated yet');
  if (world.size_bytes) parts.push(formatSize(world.size_bytes));
  if (world.last_played) parts.push(`played ${relativeTime(world.last_played)}`);
  if (world.source === 'upload') parts.push('uploaded');
  return parts.join(' · ');
}

function renderWorlds(result) {
  const list = document.getElementById('world-list');
  renderItems(list, result.worlds || [], (world) => {
    const buttons = [];
    if (!world.active) {
      buttons.push(rowButton(result.server_running ? 'Switch & restart' : 'Switch to', 'btn-primary', (b) => activateWorld(world, result.server_running, b)));
    }
    buttons.push(rowButton('Rename', 'btn-ghost', (b) => renameWorld(world, b)));
    if (world.exists) buttons.push(rowButton('Download', 'btn-ghost', (b) => downloadWorld(world, b)));
    if (!world.active) buttons.push(rowButton('Delete', 'btn-danger', (b) => deleteWorld(world, b)));
    return itemRow(worldTitle(world), null, worldMeta(world), buttons);
  }, 'No worlds yet. Start the server once, upload a world, or create a new one.');
}

async function loadWorlds() {
  if (!document.getElementById('world-list')) return;
  try {
    renderWorlds(await fetchJson('/api/worlds'));
  } catch (error) {
    setStatus(`Worlds unavailable: ${error.message}`, 'error');
  }
}

async function activateWorld(world, running, button) {
  if (running && !window.confirm(`Switch to "${world.name}"? The server restarts and players are disconnected briefly.`)) return;
  await runAction(button, running ? 'Switching world and restarting…' : null, async () => {
    const result = await fetchJson(`/api/worlds/${encodeURIComponent(world.folder)}/activate`, 'POST');
    renderWorlds(result);
    if (running) trackOperation({ operation: 'switching world' });
    else await loadStatus();
    return result.message;
  });
}

async function renameWorld(world, button) {
  const name = window.prompt('New name for this world:', world.name);
  if (!name || name.trim() === world.name) return;
  await runAction(button, null, async () => {
    const result = await fetchJson(`/api/worlds/${encodeURIComponent(world.folder)}/rename`, 'POST', { name: name.trim() });
    renderWorlds(result);
    return result.message;
  });
}

async function deleteWorld(world, button) {
  const typed = window.prompt(`Delete "${world.name}" permanently? Download it first if you want a copy.\n\nType the world name to confirm:`);
  if (typed === null) return;
  if (typed.trim() !== world.name) {
    setStatus('The name did not match; nothing was deleted.', 'error');
    return;
  }
  await runAction(button, null, async () => {
    const result = await fetchJson(`/api/worlds/${encodeURIComponent(world.folder)}`, 'DELETE');
    renderWorlds(result);
    return result.message;
  });
}

async function downloadWorld(world, button) {
  await runAction(button, `Preparing ${world.name}.zip…`, async () => {
    const response = await fetch(`/api/worlds/${encodeURIComponent(world.folder)}/download`, {
      credentials: 'same-origin',
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || `HTTP ${response.status}`);
    const url = URL.createObjectURL(await response.blob());
    const link = el('a');
    link.href = url;
    link.download = `${world.folder}.zip`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    return `Downloaded ${world.name}.`;
  });
}

function uploadWorld(input) {
  const file = input.files[0];
  if (!file) return;
  // XMLHttpRequest instead of fetch: it reports upload progress for big worlds.
  const form = new FormData();
  form.append('file', file);
  form.append('name', file.name.replace(/\.zip$/i, ''));
  const request = new XMLHttpRequest();
  request.open('POST', '/api/worlds/upload');
  request.upload.onprogress = (event) => {
    if (event.lengthComputable) {
      const percent = Math.round((event.loaded / event.total) * 100);
      setStatus(percent < 100 ? `Uploading ${file.name}: ${percent}%` : `Importing ${file.name}…`, 'pending');
    }
  };
  request.onload = () => {
    let data = {};
    try {
      data = JSON.parse(request.responseText);
    } catch (error) {
      data = { detail: request.status === 413 ? 'The file is too big for this connection.' : request.responseText };
    }
    if (request.status >= 200 && request.status < 300) {
      renderWorlds(data);
      setStatus(`${data.message} Use "Switch to" to play it.`);
    } else {
      setStatus(`Upload failed: ${data.detail || `HTTP ${request.status}`}`, 'error');
    }
  };
  request.onerror = () => setStatus('Upload failed: connection lost.', 'error');
  setStatus(`Uploading ${file.name}…`, 'pending');
  request.send(form);
  input.value = '';
}

async function createWorld(event) {
  event.preventDefault();
  const name = document.getElementById('world-name');
  const seed = document.getElementById('world-seed');
  await runAction(event.submitter, null, async () => {
    const result = await fetchJson('/api/worlds', 'POST', { name: name.value.trim(), seed: seed.value.trim() });
    renderWorlds(result);
    name.value = '';
    seed.value = '';
    return result.message;
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

async function loadPlayitJson() {
  const input = document.getElementById('playit-json-input');
  const cfg = await fetchJson('/api/playit/config');
  // The secret never leaves the host; an empty value keeps it on save.
  input.value = JSON.stringify({ secret: '', address: cfg.address || '' }, null, 2);
}

async function savePlayitJson(button) {
  let body;
  try {
    body = JSON.parse(document.getElementById('playit-json-input').value);
  } catch (error) {
    setStatus(`Invalid JSON: ${error.message}`, 'error');
    return;
  }
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    setStatus('The config must be a JSON object, e.g. {"secret": "", "address": ""}.', 'error');
    return;
  }
  await runAction(button, 'Saving PlayIt config…', async () => {
    const response = await fetchJson('/api/playit/config', 'PUT', body);
    renderPlayit(response);
    await loadPlayitJson();
    return response.message || 'Saved.';
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
  ['alerts-notify-updates', 'notify_updates', 'checkbox'],
  ['alerts-notify-security', 'notify_security', 'checkbox'],
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

// ---------- Updates ----------
const UPDATE_BUSY = new Set(['updating', 'restarting']);

function markNavUpdate(info) {
  const link = document.querySelector('.nav-link[href="/dashboard/setup"]');
  if (link) link.classList.toggle('has-update', Boolean(info.available && info.behind));
}

function renderUpdate(info) {
  markNavUpdate(info);
  const badge = document.getElementById('update-badge');
  if (!badge) return;
  const busy = UPDATE_BUSY.has(info.state);
  const blocked = Boolean((info.local_changes && info.local_changes.length) || info.ahead);
  let label = 'Up to date';
  if (!info.available) label = 'Unavailable';
  else if (busy) label = info.state === 'restarting' ? 'Restarting…' : 'Updating…';
  else if (blocked) label = "Can't update";
  else if (info.behind) label = `${info.behind} update${info.behind === 1 ? '' : 's'} available`;
  badge.textContent = label;
  badge.className = info.available && info.behind && !blocked ? 'badge badge-live' : 'badge';

  const current = info.current;
  setField('update_current', current ? `${current.hash} · ${current.subject} (${current.when}) on ${info.branch}` : '—');

  let summary = info.available ? `Following ${info.upstream}.` : (info.reason || '');
  if (info.local_changes && info.local_changes.length) {
    summary += ` Local changes block updating: ${info.local_changes.slice(0, 5).join(', ')}.`;
  }
  if (info.ahead) summary += ` This copy has ${info.ahead} commit(s) not on ${info.upstream}; update it with git.`;
  document.getElementById('update-summary').textContent = summary;

  const list = document.getElementById('update-commits');
  list.replaceChildren();
  (info.commits || []).forEach((commit) => {
    const item = el('li');
    item.append(el('code', '', commit.hash), el('span', '', commit.subject), el('span', 'commit-when', commit.when));
    list.appendChild(item);
  });
  list.hidden = !(info.commits && info.commits.length);

  const errorBox = document.getElementById('update-error');
  errorBox.hidden = !(info.state === 'error' && info.error);
  errorBox.textContent = info.error ? `Update failed: ${info.error}` : '';

  const apply = document.getElementById('update-apply-button');
  if (apply.getAttribute('aria-busy') !== 'true') {
    apply.disabled = busy || !info.available || !info.behind || Boolean(info.local_changes && info.local_changes.length) || Boolean(info.ahead);
  }
  const autoCheck = document.getElementById('update-auto-check');
  const autoInstall = document.getElementById('update-auto-install');
  if (document.activeElement !== autoCheck) autoCheck.checked = Boolean(info.auto_check);
  if (document.activeElement !== autoInstall) autoInstall.checked = Boolean(info.auto_install);
}

async function loadUpdate() {
  try {
    renderUpdate(await fetchJson('/api/update'));
  } catch (error) {
    // optional information; ignore
  }
}

async function checkUpdate(button) {
  await runAction(button, 'Checking for updates…', async () => {
    const info = await fetchJson('/api/update/check', 'POST');
    renderUpdate(info);
    return info.message;
  });
}

async function waitForRestart() {
  // The dashboard goes away while it restarts; reload the page once it answers again.
  const deadline = Date.now() + 15 * 60 * 1000;
  let wentDown = false;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    let info;
    try {
      info = await fetchJson('/api/update');
    } catch (error) {
      wentDown = true;
      setStatus('Restarting the dashboard…', 'pending');
      continue;
    }
    if (wentDown) {
      window.location.reload();
      return;
    }
    renderUpdate(info);
    if (info.state === 'error') {
      setStatus(`Update failed: ${info.error}`, 'error');
      return;
    }
    if (info.state === 'idle') {
      setStatus(info.message || 'Already up to date.');
      return;
    }
    setStatus(info.message || 'Updating…', 'pending');
  }
  setStatus('The dashboard did not come back after 15 minutes. Check the console or run ezmc logs.', 'error');
}

async function applyUpdate(button) {
  if (!window.confirm(
    'Install the update now?\n\nThe dashboard restarts (about a minute; longer if packages must be reinstalled). '
    + 'Minecraft and PlayIt stop and start again if they were running, so players are disconnected briefly.',
  )) return;
  await runAction(button, 'Updating…', async () => {
    renderUpdate(await fetchJson('/api/update/apply', 'POST'));
    return null;
  });
  setStatus('Updating…', 'pending');
  await waitForRestart();
}

async function saveUpdateSetting(key, toggle) {
  try {
    appState.config = await fetchJson('/api/config', 'PUT', { [key]: toggle.checked });
    setStatus('Update setting saved.');
  } catch (error) {
    toggle.checked = !toggle.checked;
    setStatus(`Could not save: ${error.message}`, 'error');
  }
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

  const weakPassword = document.getElementById('remote-dashboard-weak-password');
  if (weakPassword) weakPassword.hidden = !(info.enabled && info.weak_password);
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
    + 'The https link is encrypted, but anyone who gets it can try to sign in. Wrong passwords '
    + 'are locked out after 5 tries, but use a long, unique password.',
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
  on('update-check-button', 'click', (event, button) => checkUpdate(button));
  on('update-apply-button', 'click', (event, button) => applyUpdate(button));
  on('update-auto-check', 'change', (event, toggle) => saveUpdateSetting('auto_update_check', toggle));
  on('update-auto-install', 'change', (event, toggle) => saveUpdateSetting('auto_update_install', toggle));
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
  on('playit-json', 'toggle', (event, details) => {
    if (details.open) loadPlayitJson().catch((error) => setStatus(error.message, 'error'));
  });
  on('playit-json-reload', 'click', (event, button) => runAction(button, null, async () => {
    await loadPlayitJson();
    return 'PlayIt config reloaded.';
  }));
  on('playit-json-save', 'click', (event, button) => savePlayitJson(button));
  on('remote-dashboard-toggle', 'change', (event, toggle) => toggleRemoteDashboard(toggle));
  on('playit-start-button', 'click', (event, button) => playitAction('/api/playit/start', 'Starting tunnel…')(button));
  on('playit-stop-button', 'click', (event, button) => playitAction('/api/playit/stop', 'Stopping tunnel…')(button));

  on('mod-search-form', 'submit', (event) => {
    event.preventDefault();
    const sort = document.getElementById('mod-sort');
    const hasQuery = Boolean(document.getElementById('mod-search-query').value.trim());
    if (hasQuery && sort.value === 'downloads') sort.value = 'relevance';
    if (!hasQuery && sort.value === 'relevance') sort.value = 'downloads';
    browseMods(true);
  });
  on('mod-refresh-button', 'click', () => loadInstalledMods());
  on('mod-upload-input', 'change', (event, input) => uploadMods(input));
  on('world-refresh-button', 'click', () => loadWorlds());
  on('world-upload-input', 'change', (event, input) => uploadWorld(input));
  on('world-create-form', 'submit', (event) => createWorld(event));

  ['op', 'deop', 'kick', 'ban', 'day', 'night', 'clear', 'rain', 'save'].forEach((action) => {
    on(`admin-${action}`, 'click', (event, button) => applyAdminAction(action, button));
  });

  on('logout-button', 'click', () => signOut());
  on('action-status', 'click', (event, toast) => { toast.hidden = true; });
  bindCopyButtons();
}

async function boot() {
  try {
    appState.username = (await fetchJson('/api/session')).username;
  } catch (error) {
    if (error.message.startsWith('Session expired')) return; // redirecting to /login
  }
  const session = document.getElementById('session-status');
  if (session && appState.username) session.textContent = `Signed in as ${appState.username}`;

  bindEvents();
  bindMetrics();
  await loadConfig();
  await loadStatus();
  await Promise.all([loadBackups(), loadInstalledMods(), loadAlerts(), loadUpdate(), loadWorlds(), refreshConsole(true)]);
  initModBrowser(); // after loadInstalledMods so cards can show "Installed"

  if (document.getElementById('console-output')) setInterval(refreshConsole, CONSOLE_POLL_MS);
  scheduleStatusPoll();
}

document.addEventListener('DOMContentLoaded', boot);
