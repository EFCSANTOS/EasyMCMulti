const appState = {
  auth: { username: '', password: '' },
  config: {},
  status: {},
  latestVersionList: [],
};

// Browsers cannot send Basic auth on a WebSocket, so the console is polled over REST.
const CONSOLE_POLL_MS = 2000;
const STATUS_POLL_MS = 10000;

const metricsState = {
  endpoint: null,
  paused: false,
  pollMs: 2000,
  cpu: [],
  ram: [],
  maxPoints: 60,
  lastSample: null,
  timer: null,
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

// Feedback for user actions; the #action-status bar exists on every dashboard page.
function setStatus(message, isError = false) {
  const panel = document.getElementById('action-status');
  if (panel) {
    panel.textContent = message;
    panel.hidden = !message;
    panel.classList.toggle('is-error', isError);
  }
}

// Server summary; #status-panel only exists on the overview page.
function setServerSummary(message, isError = false) {
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

async function fetchJson(url, method = 'GET', body = null) {
  const options = {
    method,
    headers: {
      Authorization: buildBasicAuth(),
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

  const knownVersions = appState.latestVersionList;
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

async function loadVersions(loader) {
  try {
    const result = await fetchJson(`/api/versions/${encodeURIComponent(loader)}`);
    appState.latestVersionList = result.versions || [];
  } catch (error) {
    appState.latestVersionList = [];
    setStatus(`Version list unavailable: ${error.message}`, true);
  }
}

async function onLoaderChange() {
  const loader = document.getElementById('loader-select').value;
  await loadVersions(loader);
  // Keep the saved version selected only if the new loader offers it.
  const saved = appState.config.loader === loader ? appState.config.version : null;
  populateConfigForm({ ...appState.config, loader, version: saved || appState.latestVersionList[0] });
}

async function loadConfig() {
  try {
    const config = await fetchJson('/api/config');
    appState.config = config;
    if (document.getElementById('version-select')) {
      await loadVersions(config.loader || 'vanilla');
    }
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
    if (difficulty) difficulty.value = config.difficulty || 'normal';
    if (leveltype) leveltype.value = config.level_type || 'normal';
    if (viewdist) viewdist.value = config.view_distance || 10;
    if (simdist) simdist.value = config.simulation_distance || 10;
    if (seed) seed.value = config.level_seed || '';
    if (online) online.checked = config.online_mode !== false;
    if (pvp) pvp.checked = config.pvp !== false;
    if (spawnMonsters) spawnMonsters.checked = config.spawn_monsters !== false;
    if (allowFlight) allowFlight.checked = !!config.allow_flight;
    if (nether) nether.checked = config.allow_nether !== false;
    if (whitelist) whitelist.checked = !!config.white_list;
    if (commandBlocks) commandBlocks.checked = config.enable_command_block !== false;
  } catch (error) {
    setStatus(`Failed to load config: ${error.message}`, true);
  }
}

async function loadStatus() {
  if (!document.getElementById('status-panel') && !document.getElementById('playit-status-panel')) return;
  try {
    const status = await fetchJson('/api/status');
    appState.status = status;
    const statusText = [
      `State: ${status.state || 'unknown'}`,
      `Loader: ${status.loader || 'n/a'}`,
      `Version: ${status.version || 'n/a'}`,
      `Port: ${appState.config.server_port || 'n/a'}`,
      `Players: ${status.players || 0}${status.player_names && status.player_names.length ? ` (${status.player_names.join(', ')})` : ''}`,
      `LAN: ${status.lan_address || 'n/a'}`,
      `Public: ${status.public_address || 'n/a'}`,
    ].join(' | ');
    setServerSummary(statusText);

    const playitInfo = status.playit || {};
    if (playitInfo.state) {
      setPlayitStatus(`PlayIt: ${playitInfo.state}${playitInfo.address ? ` (${playitInfo.address})` : ''}`);
    }
  } catch (error) {
    setServerSummary(`Status unavailable: ${error.message}`, true);
  }
}

async function refreshConsole() {
  const output = document.getElementById('console-output');
  if (!output) return;
  try {
    const data = await fetchJson('/api/console');
    const atBottom = output.scrollTop + output.clientHeight >= output.scrollHeight - 4;
    output.textContent = (data.lines || []).join('\n') || 'Waiting for logs...';
    if (atBottom) output.scrollTop = output.scrollHeight;
  } catch (error) {
    // no-op; the next poll retries
  }
}

function showListPlaceholder(list, message) {
  const option = document.createElement('option');
  option.value = '';
  option.disabled = true;
  option.textContent = message;
  list.appendChild(option);
}

async function loadBackups() {
  const list = document.getElementById('backup-list');
  if (!list) return;
  try {
    const result = await fetchJson('/api/server/backups');
    list.innerHTML = '';
    const items = result.backups || [];
    if (!items.length) {
      showListPlaceholder(list, 'No backups found');
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
  const list = document.getElementById('mod-installed-list');
  if (!list) return;
  try {
    const result = await fetchJson('/api/server/mods');
    list.innerHTML = '';
    const items = result.mods || [];
    if (!items.length) {
      showListPlaceholder(list, 'No mods installed');
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
    const hits = result.items || [];
    if (!hits.length) {
      showListPlaceholder(list, 'No results found');
      return;
    }
    hits.forEach((hit) => {
      const option = document.createElement('option');
      option.value = hit.slug;
      option.textContent = `${hit.title} (${Number(hit.downloads || 0).toLocaleString()} downloads)`;
      option.title = hit.description || '';
      list.appendChild(option);
    });
  } catch (error) {
    setStatus(`Mod search failed: ${error.message}`, true);
  }
}

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

async function saveConfigFromForm() {
  const payload = collectConfigFromPage();

  try {
    appState.config = await fetchJson('/api/config', 'PUT', payload);
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
    setStatus('Creating server (downloads can take a few minutes)...');
    const response = await fetchJson('/api/server/create', 'POST', collectConfigFromPage());
    setStatus(response.message || `Server ${response.loader} ${response.version} created.`);
    await loadConfig();
    await loadStatus();
  } catch (error) {
    setStatus(`Create failed: ${error.message}`, true);
  }
}

async function backupServer() {
  try {
    const response = await fetchJson('/api/server/backup', 'POST');
    setStatus(response.message || 'Backup created.', response.ok === false);
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
    const response = await fetchJson('/api/server/backups/restore', 'POST', { backup_path: list.value });
    setStatus(response.message || 'Backup restored.');
    await loadBackups();
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
    setStatus('Installing mod...');
    const response = await fetchJson('/api/modrinth/install', 'POST', { slug: list.value });
    setStatus(response.message || 'Mod installed.');
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
    const response = await fetch('/api/server/mods/upload', {
      method: 'POST',
      headers: { Authorization: buildBasicAuth() },
      body: formData,
    });
    const text = await response.text();
    const json = text ? JSON.parse(text) : {};
    if (!response.ok) {
      throw new Error(json.detail || 'Upload failed');
    }
    setStatus(json.message || 'Mods uploaded.');
    input.value = '';
    await loadInstalledMods();
  } catch (error) {
    setStatus(`Upload failed: ${error.message}`, true);
  }
}

async function configurePlayit() {
  setStatus('Waiting for PlayIt authorization: approve it in the browser opened on the server machine...');
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

function drawMetricChart(canvas, values, maxValue, color) {
  if (!canvas) return;

  const context = canvas.getContext('2d');
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(120, rect.width || canvas.width || 300);
  const height = Math.max(80, rect.height || canvas.height || 120);
  const dpr = window.devicePixelRatio || 1;

  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  context.clearRect(0, 0, width, height);

  const padding = 10;
  const usableWidth = width - padding * 2;
  const usableHeight = height - padding * 2;

  context.strokeStyle = 'rgba(148, 163, 184, 0.18)';
  context.lineWidth = 1;
  for (let i = 0; i <= 3; i += 1) {
    const y = padding + (usableHeight / 3) * i;
    context.beginPath();
    context.moveTo(padding, y);
    context.lineTo(width - padding, y);
    context.stroke();
  }

  if (!values.length) {
    context.fillStyle = '#94a3b8';
    context.font = '12px sans-serif';
    context.textAlign = 'center';
    context.fillText('Awaiting data…', width / 2, height / 2);
    return;
  }

  const step = usableWidth / Math.max(1, values.length - 1);
  const safeMax = maxValue > 0 ? maxValue : 100;

  context.beginPath();
  values.forEach((value, index) => {
    const x = padding + index * step;
    const y = padding + usableHeight - (Math.min(Math.max(value, 0), safeMax) / safeMax) * usableHeight;
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  });

  const gradient = context.createLinearGradient(0, padding, 0, height - padding);
  gradient.addColorStop(0, color + 'ee');
  gradient.addColorStop(1, color + '00');
  context.strokeStyle = color;
  context.lineWidth = 2;
  context.stroke();

  const fillPath = context.getLineDash ? context : null;
  if (fillPath) {
    context.beginPath();
    values.forEach((value, index) => {
      const x = padding + index * step;
      const y = padding + usableHeight - (Math.min(Math.max(value, 0), safeMax) / safeMax) * usableHeight;
      if (index === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    });
    context.lineTo(padding + (values.length - 1) * step, height - padding);
    context.lineTo(padding, height - padding);
    context.closePath();
    context.fillStyle = gradient;
    context.fill();
    context.beginPath();
    values.forEach((value, index) => {
      const x = padding + index * step;
      const y = padding + usableHeight - (Math.min(Math.max(value, 0), safeMax) / safeMax) * usableHeight;
      if (index === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    });
    context.strokeStyle = color;
    context.stroke();
  }
}

function formatBytes(value) {
  if (!Number.isFinite(value) || value == null) return '—';
  let bytes = Number(value);
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let unit = 0;
  while (bytes >= 1024 && unit < units.length - 1) {
    bytes /= 1024;
    unit += 1;
  }
  return `${bytes.toFixed(bytes >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function parseMetricsPayload(payload) {
  if (!payload || typeof payload !== 'object') return null;
  const data = payload.data && typeof payload.data === 'object' ? payload.data : payload;
  const cpu = Number(data.cpu ?? data.cpuUsage ?? data.cpuPercent ?? data.cpu_percent ?? 0);
  const ram = Number(data.ram ?? data.ramUsed ?? data.usedMemory ?? data.memoryUsed ?? data.used_ram ?? 0);
  const total = Number(data.ramTotal ?? data.ram_total ?? data.memoryTotal ?? data.totalMemory ?? data.total_ram ?? 0);
  return {
    cpu: Number.isFinite(cpu) ? Math.max(0, Math.min(100, cpu)) : null,
    ram: Number.isFinite(ram) ? ram : null,
    ramTotal: Number.isFinite(total) && total > 0 ? total : null,
  };
}

function updateMetricsUi(sample) {
  const cpuValue = document.getElementById('cpu-value');
  const cpuSub = document.getElementById('cpu-sub');
  const ramValue = document.getElementById('ram-value');
  const ramSub = document.getElementById('ram-sub');
  const cpuMin = document.getElementById('cpu-min');
  const cpuMax = document.getElementById('cpu-max');
  const cpuAvg = document.getElementById('cpu-avg');
  const ramMin = document.getElementById('ram-min');
  const ramMax = document.getElementById('ram-max');
  const ramAvg = document.getElementById('ram-avg');

  if (sample && Number.isFinite(sample.cpu)) {
    cpuValue.textContent = `${sample.cpu.toFixed(1)}%`;
    cpuSub.textContent = 'usage now';
    metricsState.cpu.push(sample.cpu);
    if (metricsState.cpu.length > metricsState.maxPoints) metricsState.cpu.shift();
    const cpuValues = metricsState.cpu;
    const cpuMinValue = cpuValues.reduce((min, value) => Math.min(min, value), Infinity);
    const cpuMaxValue = cpuValues.reduce((max, value) => Math.max(max, value), -Infinity);
    const cpuAvgValue = cpuValues.reduce((sum, value) => sum + value, 0) / cpuValues.length;
    cpuMin.textContent = `${cpuMinValue === Infinity ? '—' : cpuMinValue.toFixed(1)}%`;
    cpuMax.textContent = `${cpuMaxValue === -Infinity ? '—' : cpuMaxValue.toFixed(1)}%`;
    cpuAvg.textContent = `${cpuAvgValue.toFixed(1)}%`;
  } else {
    cpuValue.textContent = '—%';
    cpuSub.textContent = 'waiting';
  }

  if (sample && Number.isFinite(sample.ram) && sample.ramTotal) {
    ramValue.textContent = `${formatBytes(sample.ram)} / ${formatBytes(sample.ramTotal)}`;
    ramSub.textContent = `${((sample.ram / sample.ramTotal) * 100).toFixed(1)}% used`;
    metricsState.ram.push(sample.ram);
    if (metricsState.ram.length > metricsState.maxPoints) metricsState.ram.shift();
    const ramValues = metricsState.ram;
    const ramMinValue = ramValues.reduce((min, value) => Math.min(min, value), Infinity);
    const ramMaxValue = ramValues.reduce((max, value) => Math.max(max, value), -Infinity);
    const ramAvgValue = ramValues.reduce((sum, value) => sum + value, 0) / ramValues.length;
    ramMin.textContent = `${ramMinValue === Infinity ? '—' : ((ramMinValue / sample.ramTotal) * 100).toFixed(1)}%`;
    ramMax.textContent = `${ramMaxValue === -Infinity ? '—' : ((ramMaxValue / sample.ramTotal) * 100).toFixed(1)}%`;
    ramAvg.textContent = `${((ramAvgValue / sample.ramTotal) * 100).toFixed(1)}%`;
  } else {
    ramValue.textContent = '—';
    ramSub.textContent = 'waiting';
  }

  const cpuChart = document.getElementById('cpu-chart');
  const ramChart = document.getElementById('ram-chart');
  drawMetricChart(cpuChart, metricsState.cpu, 100, '#38bdf8');
  drawMetricChart(ramChart, metricsState.ram, sample && sample.ramTotal ? sample.ramTotal : 1, '#818cf8');

  const badge = document.getElementById('metrics-badge');
  if (badge) {
    badge.textContent = metricsState.paused ? '● paused' : '● live';
    badge.className = metricsState.paused ? 'badge' : 'badge badge-live';
  }

  const endpoint = document.getElementById('metrics-endpoint');
  if (endpoint) endpoint.innerHTML = metricsState.endpoint ? `endpoint: <code>${metricsState.endpoint}</code>` : 'endpoint: <code>unavailable</code>';

  const last = document.getElementById('metrics-last');
  if (last) last.textContent = `last update: ${new Date().toLocaleTimeString()}`;
}

async function refreshMetrics() {
  if (metricsState.paused) return;

  try {
    if (!metricsState.endpoint) {
      const candidates = ['/api/system/metrics', '/api/system', '/api/stats', '/api/metrics', '/metrics'];
      for (const candidate of candidates) {
        try {
          const response = await fetch(candidate, { headers: { Authorization: buildBasicAuth(), Accept: 'application/json' } });
          if (!response.ok) continue;
          const text = await response.text();
          if (!text) continue;
          const payload = JSON.parse(text);
          const sample = parseMetricsPayload(payload);
          if (sample && (sample.cpu !== null || sample.ram !== null)) {
            metricsState.endpoint = candidate;
            metricsState.lastSample = sample;
            updateMetricsUi(sample);
            return;
          }
        } catch (err) {
          // try next candidate
        }
      }
      return;
    }

    const response = await fetch(metricsState.endpoint, { headers: { Authorization: buildBasicAuth(), Accept: 'application/json' } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const sample = parseMetricsPayload(payload);
    if (sample && (sample.cpu !== null || sample.ram !== null)) {
      metricsState.lastSample = sample;
      updateMetricsUi(sample);
      return;
    }
    throw new Error('No metric sample');
  } catch (error) {
    const badge = document.getElementById('metrics-badge');
    if (badge) {
      badge.textContent = '● error';
      badge.className = 'badge';
    }
  }
}

function bindMetrics() {
  if (!document.getElementById('metrics')) return;
  const toggle = document.getElementById('metrics-toggle');
  if (toggle) {
    toggle.addEventListener('click', () => {
      metricsState.paused = !metricsState.paused;
      toggle.textContent = metricsState.paused ? 'Resume' : 'Pause';
      const badge = document.getElementById('metrics-badge');
      if (badge) {
        badge.textContent = metricsState.paused ? '● paused' : '● live';
        badge.className = metricsState.paused ? 'badge' : 'badge badge-live';
      }
      if (!metricsState.paused) refreshMetrics();
    });
  }

  if (!metricsState.timer) {
    metricsState.timer = setInterval(() => refreshMetrics(), metricsState.pollMs);
  }
  refreshMetrics();
}

function bindEvents() {
  const setupForm = document.getElementById('setup-form');
  if (setupForm) {
    setupForm.addEventListener('submit', (event) => {
      event.preventDefault();
      saveConfigFromForm();
    });
  }

  const advancedSaveButton = document.getElementById('advanced-save-button');
  if (advancedSaveButton) advancedSaveButton.addEventListener('click', () => saveConfigFromForm());
  const loaderSelect = document.getElementById('loader-select');
  if (loaderSelect) loaderSelect.addEventListener('change', () => onLoaderChange());

  const createButton = document.getElementById('create-button');
  if (createButton) createButton.addEventListener('click', () => createServer());
  const startButton = document.getElementById('start-button');
  if (startButton) startButton.addEventListener('click', () => startServer());
  const stopButton = document.getElementById('stop-button');
  if (stopButton) stopButton.addEventListener('click', () => stopServer());
  const restartButton = document.getElementById('restart-button');
  if (restartButton) restartButton.addEventListener('click', () => restartServer());
  const backupButton = document.getElementById('backup-button');
  if (backupButton) backupButton.addEventListener('click', () => backupServer());
  const restoreButton = document.getElementById('backup-restore-button');
  if (restoreButton) restoreButton.addEventListener('click', () => restoreBackup());
  const commandForm = document.getElementById('command-form');
  if (commandForm) {
    commandForm.addEventListener('submit', (event) => {
      event.preventDefault();
      sendCommand();
    });
  }
  const playitConfigButton = document.getElementById('playit-config-button');
  if (playitConfigButton) playitConfigButton.addEventListener('click', () => configurePlayit());
  const playitStartButton = document.getElementById('playit-start-button');
  if (playitStartButton) playitStartButton.addEventListener('click', () => startPlayit());
  const playitStopButton = document.getElementById('playit-stop-button');
  if (playitStopButton) playitStopButton.addEventListener('click', () => stopPlayit());
  const modSearchButton = document.getElementById('mod-search-button');
  if (modSearchButton) modSearchButton.addEventListener('click', () => searchMods());
  const modRefreshButton = document.getElementById('mod-refresh-button');
  if (modRefreshButton) modRefreshButton.addEventListener('click', () => loadInstalledMods());
  const modInstallButton = document.getElementById('mod-install-button');
  if (modInstallButton) modInstallButton.addEventListener('click', () => installSelectedMod());
  const modUploadButton = document.getElementById('mod-upload-button');
  if (modUploadButton) modUploadButton.addEventListener('click', () => uploadMods());

  const adminActions = [
    ['admin-op', 'op'],
    ['admin-deop', 'deop'],
    ['admin-kick', 'kick'],
    ['admin-ban', 'ban'],
    ['admin-day', 'day'],
    ['admin-night', 'night'],
    ['admin-clear', 'clear'],
    ['admin-rain', 'rain'],
    ['admin-save', 'save'],
  ];

  adminActions.forEach(([id, action]) => {
    const button = document.getElementById(id);
    if (button) button.addEventListener('click', () => applyAdminAction(action));
  });

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
  bindMetrics();
  await loadConfig();
  await loadStatus();
  await loadBackups();
  await loadInstalledMods();
  await refreshConsole();
  if (document.getElementById('console-output')) {
    setInterval(() => refreshConsole(), CONSOLE_POLL_MS);
  }
  setInterval(() => loadStatus(), STATUS_POLL_MS);
}

document.addEventListener('DOMContentLoaded', boot);
window.addEventListener('resize', () => {
  const cpuChart = document.getElementById('cpu-chart');
  const ramChart = document.getElementById('ram-chart');
  if (cpuChart) drawMetricChart(cpuChart, metricsState.cpu, 100, '#38bdf8');
  if (ramChart) drawMetricChart(ramChart, metricsState.ram, metricsState.lastSample && metricsState.lastSample.ramTotal ? metricsState.lastSample.ramTotal : 1, '#818cf8');
});
