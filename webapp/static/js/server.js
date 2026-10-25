// EZ Minecraft dashboard, part 2 of 8: Config form, status polling and server lifecycle buttons.
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
  ['jvm-preset-flags', 'jvm_preset_flags', 'checkbox'],
  ['jvm-extra-args', 'jvm_extra_args', 'text'],
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

// ---------- Move to another version (FEAT-09) ----------
let migratePlan = null;

async function loadMigrateVersions() {
  const loader = document.getElementById('migrate-loader');
  const versions = document.getElementById('migrate-version');
  if (!loader || !versions) return;
  try {
    const data = await fetchJson(`/api/versions/${encodeURIComponent(loader.value)}`);
    versions.replaceChildren(...data.versions.map((version) => {
      const option = el('option', null, version);
      option.value = version;
      return option;
    }));
  } catch (error) {
    versions.replaceChildren();
  }
  document.getElementById('migrate-plan').hidden = true;
}

function planLine(text, warning = false) {
  return el('li', warning ? 'is-warning' : null, text);
}

function renderMigratePlan(plan) {
  migratePlan = plan;
  const list = document.getElementById('migrate-plan-list');
  const lines = [];
  const target = `${plan.to.loader} ${plan.to.version}`;
  lines.push(planLine(plan.worlds.length
    ? `Copy ${plan.worlds.length} world folder(s) (${formatBytes(plan.size)}) to ${target}.`
    : `No generated worlds to copy; ${target} starts fresh.`));
  if (plan.files.length) lines.push(planLine(`Copy ${plan.files.join(', ')}.`));
  if (plan.mods.compatible.length) lines.push(planLine(`Install ${plan.mods.compatible.length} mod(s) for ${plan.to.version}: ${plan.mods.compatible.map((m) => m.title).join(', ')}.`));
  if (plan.mods.carried.length) lines.push(planLine(`Copy ${plan.mods.carried.length} plugin(s) as they are.`));
  if (plan.mods.missing.length) lines.push(planLine(`No ${plan.to.version} version yet for: ${plan.mods.missing.map((m) => m.title).join(', ')}. They will be left out.`, true));
  if (plan.loader_change) lines.push(planLine('Different loader: mods and plugins do not carry over; the world does.', true));
  if (plan.downgrade) lines.push(planLine('This is an older version. Worlds opened in a newer version usually break in an older one.', true));
  if (plan.replaces.length) lines.push(planLine(`${plan.replaces.join(', ')} already exist there and will be kept aside, not overwritten.`, true));
  if (!plan.enough_space) lines.push(planLine('Not enough free space: free some on the Backups page first.', true));
  lines.push(planLine('A backup of the current world is made first.'));
  list.replaceChildren(...lines);
  document.getElementById('migrate-plan').hidden = false;
  document.getElementById('migrate-run-button').disabled = !plan.enough_space;
}

async function checkMigration(event) {
  event.preventDefault();
  await runAction(event.submitter, 'Checking…', async () => {
    renderMigratePlan(await fetchJson('/api/server/migrate/plan', 'POST', {
      loader: document.getElementById('migrate-loader').value,
      version: document.getElementById('migrate-version').value,
    }));
    return null;
  });
}

async function runMigration(button) {
  const plan = migratePlan;
  if (!plan) return;
  const question = plan.downgrade
    ? `Copy your worlds to the OLDER version ${plan.to.version}? They may not load there. The current version stays untouched.`
    : `Move to ${plan.to.loader} ${plan.to.version}? The server folder for the current version stays untouched.`;
  if (!window.confirm(question)) return;
  await runAction(button, null, async () => {
    const response = await fetchJson('/api/server/migrate', 'POST', {
      loader: plan.to.loader, version: plan.to.version, skip_missing: true, allow_downgrade: plan.downgrade,
    });
    trackOperation(response);
    document.getElementById('migrate-plan').hidden = true;
    return response.message;
  });
}

// ---------- Performance presets and RAM advice ----------
const PERFORMANCE_PRESETS = {
  phone: { label: 'Phone / low-power', ram: 2048, view: 6, sim: 4 },
  balanced: { label: 'Balanced', ram: 3072, view: 10, sim: 8 },
  modded: { label: 'PC with many mods', ram: 6144, view: 10, sim: 8 },
};
const MODDED_LOADERS = ['fabric', 'forge', 'neoforge'];

// Safe range for this device: at most ~50% of a phone's memory (Android needs headroom), ~70% on a PC.
function ramLimits(loader) {
  const host = appState.host || {};
  const total = host.ram_mb || 0;
  const max = total ? Math.max(512, Math.floor((total * (host.phone ? 0.5 : 0.7)) / 256) * 256) : 8192;
  const wanted = MODDED_LOADERS.includes(loader) ? 3072 : (host.phone ? 1024 : 2048);
  return { total, max, min: Math.min(wanted, max), phone: Boolean(host.phone) };
}

function currentLoader() {
  const select = document.getElementById('loader-select');
  return select ? select.value : (appState.config.loader || 'vanilla');
}

function renderRamAdvice() {
  const advice = document.getElementById('ram-advice');
  const input = document.getElementById('ram-input');
  const paperHint = document.getElementById('paper-pregen-hint');
  if (paperHint) paperHint.hidden = currentLoader() !== 'paper';
  if (!advice || !input || !appState.host) return;
  const limits = ramLimits(currentLoader());
  const device = limits.phone ? 'phone' : 'computer';
  const value = Number(input.value);
  if (value > limits.max) {
    advice.textContent = `Too much for this ${device} (${limits.total} MB total): use at most ${limits.max} MB, or it may be slowed down or stopped by the system.`;
    advice.classList.add('hint-error');
  } else {
    advice.textContent = `Recommended: ${limits.min}–${limits.max} MB on this ${device} (${limits.total} MB total).`;
    advice.classList.remove('hint-error');
  }
}

async function loadHostInfo() {
  if (!document.getElementById('ram-advice')) return;
  try {
    appState.host = await fetchJson('/api/system/host');
  } catch (error) {
    appState.host = null;
  }
  renderRamAdvice();
}

async function applyPreset(name, button) {
  const preset = PERFORMANCE_PRESETS[name];
  await runAction(button, null, async () => {
    const ram = Math.min(preset.ram, ramLimits(currentLoader()).max);
    appState.config = await fetchJson('/api/config', 'PUT', {
      ram_mb: ram, view_distance: preset.view, simulation_distance: preset.sim,
    });
    populateConfigForm(appState.config);
    renderRamAdvice();
    return `${preset.label}: ${ram} MB memory, view distance ${preset.view}, simulation distance ${preset.sim}.`;
  });
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
  renderSleep(status.device_sleep);

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

// Deep sleep in the last 24 hours (phones): the server was unreachable meanwhile.
function renderSleep(sleep) {
  const card = document.getElementById('sleep-warning');
  if (!card) return;
  card.hidden = !(sleep && sleep.count);
  if (card.hidden) return;
  const minutes = (seconds) => `${Math.max(1, Math.round(seconds / 60))} min`;
  const last = new Date(sleep.last_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const what = sleep.phone ? 'phone' : 'computer';
  document.getElementById('sleep-title').textContent = `The ${what} fell asleep`;
  document.getElementById('sleep-detail').textContent = sleep.count === 1
    ? `For ${minutes(sleep.last_seconds)}, noticed at ${last}. Nobody could reach the server meanwhile.`
    : `${sleep.count} times in the last 24 hours (${minutes(sleep.total_seconds)} in total; last ${minutes(sleep.last_seconds)}, noticed at ${last}).`;
  document.getElementById('sleep-phone-steps').hidden = !sleep.phone;
}

async function openBatterySettings(button) {
  await runAction(button, null, async () => (await fetchJson('/api/power/battery-settings', 'POST')).message);
}

async function dismissSleepWarning(button) {
  await runAction(button, null, async () => {
    renderSleep(await fetchJson('/api/power/dismiss', 'POST'));
    return 'Dismissed. It comes back if the phone falls asleep again.';
  });
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
  if ((appState.status.diagnosis_at || 0) !== (previous.diagnosis_at || 0)) await loadDiagnosis();
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
    rememberCommand(value);
    input.value = '';
    await refreshConsole();
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
