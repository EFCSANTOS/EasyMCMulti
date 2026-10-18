const appState = {
  auth: { username: '', password: '' },
  config: {},
  status: {},
  latestVersionList: [],
};

function readSessionCredentials() {
  try {
    const username = sessionStorage.getItem('ez_auth_username') || '';
    const password = sessionStorage.getItem('ez_auth_password') || '';
    appState.auth = { username, password };
    return { username, password };
  } catch (error) {
    return { username: '', password: '' };
  }
}

function buildBasicAuth() {
  const user = appState.auth.username || readSessionCredentials().username || '';
  const pass = appState.auth.password || readSessionCredentials().password || '';
  return 'Basic ' + btoa(`${user}:${pass}`);
}

function setStatus(message, isError = false) {
  const panel = document.getElementById('status-panel');
  if (panel) {
    panel.textContent = message;
    panel.style.color = isError ? '#fca5a5' : '#e2e8f0';
  }
}

function setPlayitStatus(message, isError = false) {
  const panel = document.getElementById('playit-status-panel');
  if (panel) {
    panel.textContent = message;
    panel.style.color = isError ? '#fca5a5' : '#e2e8f0';
  }
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

async function fetchJson(url, method = 'GET', body = null) {
  const options = {
    method,
    headers: {
      'Authorization': buildBasicAuth(),
      'Content-Type': 'application/json',
    },
  };

  if (body !== null) {
    options.body = JSON.stringify(body);
  }

  const response = await fetch(url, options);
  const text = await response.text();
  if (!response.ok) {
    let message = text || 'Request failed';
    try {
      const json = JSON.parse(text);
      message = json.detail || json.error || message;
    } catch (err) {
      // ignore parse error
    }
    throw new Error(message);
  }

  if (!text) {
    return {};
  }

  try {
    return JSON.parse(text);
  } catch (err) {
    return text;
  }
}

function populateConfigForm(config) {
  const loader = document.getElementById('loader-select');
  const version = document.getElementById('version-select');
  const ram = document.getElementById('ram-input');
  const port = document.getElementById('port-input');

  if (loader) loader.value = config.loader || 'vanilla';
  if (ram) ram.value = config.ram_mb || 2048;
  if (port) port.value = config.server_port || 25565;

  const knownVersions = config.available_versions || appState.latestVersionList;
  if (version) {
    const current = config.version || knownVersions[0] || '1.21.7';
    version.innerHTML = '';
    if (knownVersions.length) {
      knownVersions.forEach((item) => {
        const option = document.createElement('option');
        option.value = item;
        option.textContent = item;
        option.selected = item === current;
        version.appendChild(option);
      });
    } else {
      const fallback = document.createElement('option');
      fallback.value = current;
      fallback.textContent = current;
      version.appendChild(fallback);
    }
  }
}

async function loadConfig() {
  try {
    const config = await fetchJson('/api/config');
    appState.config = config;
    populateConfigForm(config);
    const motd = document.getElementById('motd-input');
    const maxplayers = document.getElementById('maxplayers-input');
    const gamemode = document.getElementById('gamemode-input');
    const difficulty = document.getElementById('difficulty-input');
    const leveltype = document.getElementById('leveltype-input');
    const viewdist = document.getElementById('viewdist-input');
    const simdist = document.getElementById('simdist-input');
    const seed = document.getElementById('seed-input');
    const online = document.getElementById('online-mode');
    const pvp = document.getElementById('pvp-mode');
    const spawnMonsters = document.getElementById('spawn-monsters');
    const allowFlight = document.getElementById('allow-flight');
    const nether = document.getElementById('nether-mode');
    const whitelist = document.getElementById('whitelist-mode');
    const commandBlocks = document.getElementById('command-blocks');

    if (motd) motd.value = config.motd || 'EZ Minecraft Server';
    if (maxplayers) maxplayers.value = config.max_players || 10;
    if (gamemode) gamemode.value = config.gamemode || 'survival';
    if (difficulty) difficulty.value = config.difficulty || 'easy';
    if (leveltype) leveltype.value = config.level_type || 'normal';
    if (viewdist) viewdist.value = config.view_distance || 10;
    if (simdist) simdist.value = config.simulation_distance || 10;
    if (seed) seed.value = config.seed || '';
    if (online) online.checked = config.online_mode !== false;
    if (pvp) pvp.checked = config.pvp !== false;
    if (spawnMonsters) spawnMonsters.checked = config.spawn_monsters !== false;
    if (allowFlight) allowFlight.checked = !!config.allow_flight;
    if (nether) nether.checked = config.allow_nether !== false;
    if (whitelist) whitelist.checked = !!config.whitelist;
    if (commandBlocks) commandBlocks.checked = config.command_blocks !== false;
  } catch (error) {
    setStatus(`Failed to load config: ${error.message}`, true);
  }
}

async function loadStatus() {
  try {
    const status = await fetchJson('/api/status');
    appState.status = status;
    const statusText = [
      `State: ${status.state || 'unknown'}`,
      `Loader: ${status.loader || 'n/a'}`,
      `Version: ${status.version || 'n/a'}`,
      `Port: ${status.server_port || 'n/a'}`,
      `Players: ${status.players || 0}`,
      `Address: ${status.public_address || 'n/a'}`,
    ].join(' | ');
    setStatus(statusText);

    if (status.playit_state) {
      setPlayitStatus(`PlayIt: ${status.playit_state}${status.playit_address ? ` (${status.playit_address})` : ''}`);
    }
  } catch (error) {
    setStatus(`Status unavailable: ${error.message}`, true);
  }
}

async function refreshConsole() {
  try {
    const data = await fetchJson('/api/console');
    const output = document.getElementById('console-output');
    if (output) {
      output.textContent = (data.lines || []).join('\n');
    }
  } catch (error) {
    // no-op; console is optional
  }
}

async function loadBackups() {
  try {
    const result = await fetchJson('/api/backups');
    const list = document.getElementById('backup-list');
    if (!list) return;
    list.innerHTML = '';
    const items = result.backups || [];
    if (!items.length) {
      const option = document.createElement('option');
      option.textContent = 'No backups found';
      list.appendChild(option);
      return;
    }
    items.forEach((backup) => {
      const option = document.createElement('option');
      option.value = backup;
      option.textContent = backup;
      list.appendChild(option);
    });
  } catch (error) {
    setStatus(`Backup list unavailable: ${error.message}`, true);
  }
}

async function loadInstalledMods() {
  try {
    const result = await fetchJson('/api/mods');
    const list = document.getElementById('mod-installed-list');
    if (!list) return;
    list.innerHTML = '';
    const items = result.mods || [];
    if (!items.length) {
      const option = document.createElement('option');
      option.textContent = 'No mods installed';
      list.appendChild(option);
      return;
    }
    items.forEach((mod) => {
      const option = document.createElement('option');
      option.value = mod.name || mod;
      option.textContent = mod.name || mod;
      list.appendChild(option);
    });
  } catch (error) {
    setStatus(`Mod list unavailable: ${error.message}`, true);
  }
}

async function searchMods() {
  const q = document.getElementById('mod-search-query').value.trim();
  if (!q) {
    setStatus('Search query is required.', true);
    return;
  }

  try {
    const result = await fetchJson(`/api/modrinth/search?query=${encodeURIComponent(q)}`);
    const list = document.getElementById('mod-search-results');
    if (!list) return;
    list.innerHTML = '';
    const hits = result.hits || [];
    if (!hits.length) {
      const option = document.createElement('option');
      option.textContent = 'No results found';
      list.appendChild(option);
      return;
    }
    hits.forEach((hit) => {
      const option = document.createElement('option');
      option.value = JSON.stringify({ project_id: hit.project_id, title: hit.title, version: hit.latest_version || hit.version || '' });
      option.textContent = `${hit.title} (${hit.project_type || 'mod'})`;
      list.appendChild(option);
    });
  } catch (error) {
    setStatus(`Mod search failed: ${error.message}`, true);
  }
}

async function saveConfigFromForm() {
  const form = document.getElementById('setup-form');
  const formData = new FormData(form);
  const payload = Object.fromEntries(formData.entries());

  payload.ram_mb = Number(payload.ram_mb || 2048);
  payload.server_port = Number(payload.server_port || 25565);
  payload.online_mode = document.getElementById('online-mode').checked;
  payload.pvp = document.getElementById('pvp-mode').checked;
  payload.spawn_monsters = document.getElementById('spawn-monsters').checked;
  payload.allow_flight = document.getElementById('allow-flight').checked;
  payload.allow_nether = document.getElementById('nether-mode').checked;
  payload.whitelist = document.getElementById('whitelist-mode').checked;
  payload.command_blocks = document.getElementById('command-blocks').checked;
  payload.motd = document.getElementById('motd-input').value;
  payload.max_players = Number(document.getElementById('maxplayers-input').value || 10);
  payload.gamemode = document.getElementById('gamemode-input').value;
  payload.difficulty = document.getElementById('difficulty-input').value;
  payload.level_type = document.getElementById('leveltype-input').value;
  payload.view_distance = Number(document.getElementById('viewdist-input').value || 10);
  payload.simulation_distance = Number(document.getElementById('simdist-input').value || 10);
  payload.seed = document.getElementById('seed-input').value || '';

  try {
    await fetchJson('/api/config', 'PUT', payload);
    setStatus('Configuration saved.');
    await loadStatus();
  } catch (error) {
    setStatus(`Failed to save config: ${error.message}`, true);
  }
}

async function startServer() {
  try {
    const response = await fetchJson('/api/server/start', 'POST');
    setStatus(response.message || 'Server start request sent.');
    await loadStatus();
  } catch (error) {
    setStatus(`Start failed: ${error.message}`, true);
  }
}

async function stopServer() {
  try {
    const response = await fetchJson('/api/server/stop', 'POST');
    setStatus(response.message || 'Server stop request sent.');
    await loadStatus();
  } catch (error) {
    setStatus(`Stop failed: ${error.message}`, true);
  }
}

async function restartServer() {
  try {
    const response = await fetchJson('/api/server/restart', 'POST');
    setStatus(response.message || 'Server restart request sent.');
    await loadStatus();
  } catch (error) {
    setStatus(`Restart failed: ${error.message}`, true);
  }
}

async function createServer() {
  try {
    const payload = {
      loader: document.getElementById('loader-select').value,
      version: document.getElementById('version-select').value,
      ram_mb: Number(document.getElementById('ram-input').value || 2048),
    };
    const response = await fetchJson('/api/server/create', 'POST', payload);
    setStatus(response.message || 'Server created.');
    await loadConfig();
    await loadStatus();
  } catch (error) {
    setStatus(`Create failed: ${error.message}`, true);
  }
}

async function backupServer() {
  try {
    const response = await fetchJson('/api/server/backup', 'POST');
    setStatus(response.message || 'Backup created.');
    await loadBackups();
  } catch (error) {
    setStatus(`Backup failed: ${error.message}`, true);
  }
}

async function restoreBackup() {
  const list = document.getElementById('backup-list');
  if (!list || !list.value) {
    setStatus('Select a backup first.', true);
    return;
  }

  try {
    const response = await fetchJson('/api/backups/restore', 'POST', { name: list.value });
    setStatus(response.message || 'Backup restored.');
  } catch (error) {
    setStatus(`Restore failed: ${error.message}`, true);
  }
}

async function sendCommand() {
  const input = document.getElementById('command-input');
  const value = input.value.trim();
  if (!value) return;

  try {
    const response = await fetchJson('/api/server/command', 'POST', { command: value });
    setStatus(response.message || 'Command sent.');
    input.value = '';
  } catch (error) {
    setStatus(`Command failed: ${error.message}`, true);
  }
}

async function installSelectedMod() {
  const list = document.getElementById('mod-search-results');
  if (!list || !list.value) {
    setStatus('Select a mod to install.', true);
    return;
  }

  try {
    const payload = JSON.parse(list.value);
    const response = await fetchJson('/api/modrinth/install', 'POST', payload);
    setStatus(response.message || 'Mod install started.');
    await loadInstalledMods();
  } catch (error) {
    setStatus(`Mod install failed: ${error.message}`, true);
  }
}

async function uploadMods() {
  const input = document.getElementById('mod-upload-input');
  if (!input || !input.files.length) {
    setStatus('Choose one or more .jar files first.', true);
    return;
  }

  const formData = new FormData();
  Array.from(input.files).forEach((file) => formData.append('files', file));

  try {
    const response = await fetch('/api/mods/upload', {
      method: 'POST',
      headers: { Authorization: buildBasicAuth() },
      body: formData,
    });
    const text = await response.text();
    if (!response.ok) {
      throw new Error(text || 'Upload failed');
    }
    const json = text ? JSON.parse(text) : {};
    setStatus(json.message || 'Mods uploaded.');
    await loadInstalledMods();
  } catch (error) {
    setStatus(`Upload failed: ${error.message}`, true);
  }
}

async function configurePlayit() {
  try {
    const response = await fetchJson('/api/playit/configure', 'POST');
    setStatus(response.message || 'PlayIt configured.');
    await loadStatus();
  } catch (error) {
    setStatus(`PlayIt config failed: ${error.message}`, true);
  }
}

async function startPlayit() {
  try {
    const response = await fetchJson('/api/playit/start', 'POST');
    setStatus(response.message || 'PlayIt tunnel started.');
    await loadStatus();
  } catch (error) {
    setStatus(`PlayIt start failed: ${error.message}`, true);
  }
}

async function stopPlayit() {
  try {
    const response = await fetchJson('/api/playit/stop', 'POST');
    setStatus(response.message || 'PlayIt tunnel stopped.');
    await loadStatus();
  } catch (error) {
    setStatus(`PlayIt stop failed: ${error.message}`, true);
  }
}

async function applyAdminAction(action) {
  const player = document.getElementById('admin-player').value.trim();
  const payload = player ? { player } : {};

  try {
    const response = await fetchJson(`/api/admin/${action}`, 'POST', payload);
    setStatus(response.message || `${action} command sent.`);
  } catch (error) {
    setStatus(`${action} failed: ${error.message}`, true);
  }
}

function bindEvents() {
  document.getElementById('setup-form').addEventListener('submit', (event) => {
    event.preventDefault();
    saveConfigFromForm();
  });

  document.getElementById('create-button').addEventListener('click', () => createServer());
  document.getElementById('start-button').addEventListener('click', () => startServer());
  document.getElementById('stop-button').addEventListener('click', () => stopServer());
  document.getElementById('restart-button').addEventListener('click', () => restartServer());
  document.getElementById('backup-button').addEventListener('click', () => backupServer());
  document.getElementById('backup-restore-button').addEventListener('click', () => restoreBackup());
  document.getElementById('command-form').addEventListener('submit', (event) => {
    event.preventDefault();
    sendCommand();
  });
  document.getElementById('playit-config-button').addEventListener('click', () => configurePlayit());
  document.getElementById('playit-start-button').addEventListener('click', () => startPlayit());
  document.getElementById('playit-stop-button').addEventListener('click', () => stopPlayit());
  document.getElementById('mod-search-button').addEventListener('click', () => searchMods());
  document.getElementById('mod-refresh-button').addEventListener('click', () => loadInstalledMods());
  document.getElementById('mod-install-button').addEventListener('click', () => installSelectedMod());
  document.getElementById('mod-upload-button').addEventListener('click', () => uploadMods());

  document.getElementById('admin-op').addEventListener('click', () => applyAdminAction('op'));
  document.getElementById('admin-deop').addEventListener('click', () => applyAdminAction('deop'));
  document.getElementById('admin-kick').addEventListener('click', () => applyAdminAction('kick'));
  document.getElementById('admin-ban').addEventListener('click', () => applyAdminAction('ban'));
  document.getElementById('admin-day').addEventListener('click', () => applyAdminAction('day'));
  document.getElementById('admin-night').addEventListener('click', () => applyAdminAction('night'));
  document.getElementById('admin-clear').addEventListener('click', () => applyAdminAction('clear'));
  document.getElementById('admin-rain').addEventListener('click', () => applyAdminAction('rain'));
  document.getElementById('admin-save').addEventListener('click', () => applyAdminAction('save'));

  const logoutButton = document.getElementById('logout-button');
  if (logoutButton) {
    logoutButton.addEventListener('click', () => {
      sessionStorage.removeItem('ez_auth_username');
      sessionStorage.removeItem('ez_auth_password');
      window.location.href = '/login';
    });
  }
}

async function boot() {
  readSessionCredentials();
  const sessionStatus = document.getElementById('session-status');
  if (sessionStatus) {
    sessionStatus.textContent = appState.auth.username ? `Signed in as ${appState.auth.username}` : 'Not signed in';
  }
  if (!appState.auth.username || !appState.auth.password) {
    window.location.href = '/login';
    return;
  }
  bindEvents();
  await loadConfig();
  await loadStatus();
  await loadBackups();
  await loadInstalledMods();
  await refreshConsole();
}

document.addEventListener('DOMContentLoaded', boot);
