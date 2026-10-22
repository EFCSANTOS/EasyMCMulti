// EZ Minecraft dashboard, part 2 of 7: Config form, status polling and server lifecycle buttons.
// Plain scripts that share one global scope, loaded in order by webapp/pages/layout.py::SCRIPTS.

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
  ['restart-daily-time', 'restart_daily_time', 'text'],
  ['idle-stop-minutes', 'idle_stop_minutes', 'number'],
  ['quiet-hours-start', 'quiet_hours_start', 'text'],
  ['quiet-hours-end', 'quiet_hours_end', 'text'],
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
    appState.config = await fetchConfig('/api/config', 'PUT', collectConfigFromPage());
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
  if (label) {
    const idle = state === 'stopped' && status.stopped_reason === 'idle';
    label.textContent = idle ? 'Stopped · idle' : (STATE_LABELS[state] || state);
    label.title = idle ? 'Stopped automatically because nobody was online (Server setup → Schedule).' : '';
  }
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
    const response = await fetchConfig('/api/server/create', 'POST', collectConfigFromPage());
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
