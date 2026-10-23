// EZ Minecraft dashboard, part 6 of 8: Console output and the CPU/RAM charts.
// Plain scripts that share one global scope, loaded in order by webapp/pages/layout.py::SCRIPTS.

// ---------- Crash diagnosis ----------
async function runDiagnosisAction(item, button) {
  const action = item.action;
  if (action.kind === 'open_setting') {
    window.location.href = `/dashboard/${action.page}#${action.field}`;
    return;
  }
  if (action.kind === 'kill_process' && !window.confirm(
    `Stop the program that is using port ${action.port}? Only a leftover Minecraft (Java) server is stopped.`)) return;
  await runAction(button, null, async () => {
    let message;
    if (action.kind === 'install_mod') {
      message = (await fetchJson('/api/modrinth/install', 'POST', { slug: action.slug })).message;
    } else if (action.kind === 'remove_mod') {
      message = (await fetchJson(`/api/server/mods/${encodeURIComponent(action.file)}`, 'DELETE')).message
        || `Removed ${action.file}.`;
    } else {
      message = (await fetchJson('/api/server/diagnosis/fix', 'POST', action)).message;
    }
    await loadDiagnosis();
    return message;
  });
}

const DIAGNOSIS_BUTTONS = {
  install_mod: (action) => `Install ${action.slug}`,
  remove_mod: (action) => `Remove ${action.file}`,
  open_setting: () => 'Open setting',
  accept_eula: () => 'Accept EULA',
  kill_process: () => 'Stop the old server',
};

function renderDiagnosis(items) {
  const card = document.getElementById('diagnosis');
  if (!card) return;
  card.hidden = !items.length;
  renderItems(document.getElementById('diagnosis-list'), items, (item) => {
    const label = item.action && DIAGNOSIS_BUTTONS[item.action.kind];
    const button = label ? rowButton(label(item.action), 'btn-primary', (b) => runDiagnosisAction(item, b)) : null;
    return itemRow(item.title, item.message, null, button);
  }, '');
}

async function loadDiagnosis() {
  if (!document.getElementById('diagnosis')) return;
  try {
    renderDiagnosis((await fetchJson('/api/server/diagnosis')).items || []);
  } catch (error) {
    // the next status poll retries
  }
}

// ---------- Log files ----------
async function viewLog(file, button) {
  await runAction(button, null, async () => {
    const data = await fetchJson(`/api/server/logs/${file.folder}/${encodeURIComponent(file.name)}`);
    const view = document.getElementById('log-view');
    const fragment = document.createDocumentFragment();
    data.lines.forEach((line) => fragment.appendChild(el('span', consoleLineClass(line), `${line}\n`)));
    view.replaceChildren(fragment);
    view.hidden = false;
    const title = document.getElementById('log-view-title');
    title.textContent = `${file.folder}/${file.name}${data.truncated ? ` (last ${data.lines.length} lines)` : ''}`;
    title.hidden = false;
    view.scrollTop = view.scrollHeight;
    return null;
  });
}

async function loadLogs() {
  const list = document.getElementById('log-list');
  if (!list) return;
  try {
    const { files } = await fetchJson('/api/server/logs');
    renderItems(list, files, (file) => {
      const download = el('a', 'btn btn-ghost btn-sm', 'Download');
      download.href = `/api/server/logs/${file.folder}/${encodeURIComponent(file.name)}?download=true`;
      download.download = file.name;
      const when = new Date(file.modified * 1000).toLocaleString();
      return itemRow(file.name, file.folder === 'crash-reports' ? 'Crash report' : 'Server log',
        `${formatBytes(file.size)} · ${when}`, [rowButton('View', 'btn-ghost', (b) => viewLog(file, b)), download]);
    }, 'No log files yet. They appear after the server has run once.');
  } catch (error) {
    setStatus(`Log list unavailable: ${error.message}`, 'error');
  }
}

// ---------- Console ----------
// Live over the /ws/console WebSocket (session cookie); if that fails, poll /api/console?since=<n>
// for new lines only, and try the socket again every CONSOLE_WS_RETRY_MS.
const CONSOLE_DOM_LIMIT = 2000;
const CONSOLE_WS_RETRY_MS = 30000;
const COMMAND_HISTORY_KEY = 'ez_command_history';
const COMMAND_HISTORY_LIMIT = 50;
const consoleState = { socket: null, last: 0, pollTimer: null, filter: 'all', search: '', historyIndex: -1 };

function consoleLineClass(line) {
  if (line.startsWith('> ')) return 'line-cmd';
  if (/\b(ERROR|SEVERE|FATAL|Exception)\b/.test(line)) return 'line-error';
  if (/\bWARN(ING)?\b/.test(line)) return 'line-warn';
  if (/^\[(server|install|playit)\]/.test(line)) return 'line-meta';
  return '';
}

function consoleLineKind(line) {
  if (/\]: (?:\[Not Secure\] )?<[^>]+> /.test(line) || line.startsWith('[chat] ')) return 'chat';
  const cls = consoleLineClass(line);
  return cls === 'line-error' || cls === 'line-warn' ? 'problem' : 'other';
}

function consoleLineVisible(span) {
  if (consoleState.filter === 'chat' && span.dataset.kind !== 'chat') return false;
  if (consoleState.filter === 'problems' && span.dataset.kind !== 'problem') return false;
  return !consoleState.search || span.textContent.toLowerCase().includes(consoleState.search);
}

// Appends lines (or replaces everything); keeps at most CONSOLE_DOM_LIMIT lines in the page.
function appendConsoleLines(lines, replace = false) {
  const output = document.getElementById('console-output');
  if (!output) return;
  const autoscroll = document.getElementById('console-autoscroll');
  const atBottom = output.scrollTop + output.clientHeight >= output.scrollHeight - 8;
  if (replace || !output.firstElementChild) output.replaceChildren(); // also drops "Waiting for logs..."
  const fragment = document.createDocumentFragment();
  lines.forEach((line) => {
    const span = el('span', consoleLineClass(line), `${line}\n`);
    span.dataset.kind = consoleLineKind(line);
    span.hidden = !consoleLineVisible(span);
    fragment.appendChild(span);
  });
  output.appendChild(fragment);
  while (output.childElementCount > CONSOLE_DOM_LIMIT) output.firstElementChild.remove();
  if (!output.childElementCount) output.textContent = 'Waiting for logs...';
  if (autoscroll ? autoscroll.checked : atBottom) output.scrollTop = output.scrollHeight;
}

function applyConsoleFilter() {
  const filter = document.getElementById('console-filter');
  const search = document.getElementById('console-search');
  consoleState.filter = filter ? filter.value : 'all';
  consoleState.search = search ? search.value.trim().toLowerCase() : '';
  document.querySelectorAll('#console-output span').forEach((span) => { span.hidden = !consoleLineVisible(span); });
}

// Full reload of the kept history (start, and when switching from the socket back to polling).
async function reloadConsole() {
  try {
    const data = await fetchJson('/api/console');
    appendConsoleLines(data.lines || [], true);
    consoleState.last = data.last || 0;
  } catch (error) {
    // the next poll retries
  }
}

async function refreshConsole() {
  if (consoleState.socket && consoleState.socket.readyState === WebSocket.OPEN) return; // lines arrive live
  try {
    const data = await fetchJson(`/api/console?since=${consoleState.last}`);
    if ((data.lines || []).length) appendConsoleLines(data.lines);
    consoleState.last = data.last || consoleState.last;
  } catch (error) {
    // the next poll retries
  }
}

function startConsolePolling() {
  if (consoleState.pollTimer) return;
  reloadConsole();
  consoleState.pollTimer = setInterval(refreshConsole, CONSOLE_POLL_MS);
}

function stopConsolePolling() {
  clearInterval(consoleState.pollTimer);
  consoleState.pollTimer = null;
}

function connectConsoleSocket() {
  let socket;
  try {
    socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/console`);
  } catch (error) {
    startConsolePolling();
    return;
  }
  consoleState.socket = socket;
  socket.addEventListener('open', () => {
    stopConsolePolling();
    appendConsoleLines([], true); // the server replays recent lines first
  });
  socket.addEventListener('message', (event) => appendConsoleLines([event.data]));
  socket.addEventListener('close', () => {
    if (consoleState.socket !== socket) return;
    consoleState.socket = null;
    startConsolePolling();
    setTimeout(connectConsoleSocket, CONSOLE_WS_RETRY_MS);
  });
}

async function startConsole() {
  if (!document.getElementById('console-output')) return;
  await reloadConsole();
  connectConsoleSocket();
}

// ---------- Command box: history (↑/↓) and player-name completion (Tab) ----------
function loadCommandHistory() {
  try {
    return JSON.parse(localStorage.getItem(COMMAND_HISTORY_KEY) || '[]');
  } catch (error) {
    return [];
  }
}

function rememberCommand(command) {
  const history = loadCommandHistory().filter((entry) => entry !== command);
  history.unshift(command);
  try {
    localStorage.setItem(COMMAND_HISTORY_KEY, JSON.stringify(history.slice(0, COMMAND_HISTORY_LIMIT)));
  } catch (error) {
    // storage unavailable: history only lasts for this page
  }
  consoleState.historyIndex = -1;
}

function handleCommandKey(event, input) {
  if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
    const history = loadCommandHistory();
    if (!history.length) return;
    event.preventDefault();
    const step = event.key === 'ArrowUp' ? 1 : -1;
    consoleState.historyIndex = Math.max(-1, Math.min(history.length - 1, consoleState.historyIndex + step));
    input.value = consoleState.historyIndex === -1 ? '' : history[consoleState.historyIndex];
  } else if (event.key === 'Tab') {
    const words = input.value.split(' ');
    const partial = words[words.length - 1].toLowerCase();
    const names = (appState.status.player_names || []).filter((name) => name.toLowerCase().startsWith(partial));
    if (!partial || !names.length) return;
    event.preventDefault();
    words[words.length - 1] = names[0];
    input.value = `${words.join(' ')} `;
  }
}

// ---------- Chat with players (overview) ----------
async function sendChat(event) {
  event.preventDefault();
  const input = document.getElementById('chat-input');
  const message = input.value.trim();
  if (!message) return;
  await runAction(event.submitter, null, async () => {
    await fetchJson('/api/server/chat', 'POST', { message });
    input.value = '';
    await refreshConsole();
    return null;
  });
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
  'temp-chart': { values: () => metricsState.temp, max: () => 100, format: (v) => `${v.toFixed(1)} °C` },
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
  const readings = sample.temperatures || [];
  if (readings.length) metricsState.temp.push(readings[0].celsius);
  [metricsState.cpu, metricsState.ram, metricsState.temp].forEach((series) => {
    if (series.length > metricsState.maxPoints) series.shift();
  });

  const cpu = summarize(metricsState.cpu);
  document.getElementById('cpu-value').textContent = `${sample.cpu.toFixed(1)}%`;
  const cpuSub = document.getElementById('cpu-sub');
  // Android hides machine-wide CPU use, so on Termux this is the server plus the other Termux processes.
  cpuSub.textContent = { processes: 'now · Termux processes', unavailable: 'not available' }[sample.cpuScope] || 'now';
  cpuSub.title = sample.cpuScope === 'processes' ? 'Android does not let apps see total CPU use; this counts what runs in Termux, including the server.' : '';
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

  // Hottest sensor on the chart; every sensor listed under it.
  const degrees = (value) => `${value.toFixed(1)} °C`;
  if (readings.length) {
    const temp = summarize(metricsState.temp);
    document.getElementById('temp-value').textContent = degrees(readings[0].celsius);
    document.getElementById('temp-sub').textContent = readings[0].label;
    document.getElementById('temp-min').textContent = degrees(temp.min);
    document.getElementById('temp-avg').textContent = degrees(temp.avg);
    document.getElementById('temp-max').textContent = degrees(temp.max);
    document.getElementById('temp-list').textContent = readings.length > 1
      ? readings.map((reading) => `${reading.label} ${Math.round(reading.celsius)} °C`).join(' · ') : '';
  } else {
    document.getElementById('temp-sub').textContent = 'not available';
    document.getElementById('temp-list').textContent = 'This device does not let apps read its temperature sensors '
      + '(Windows needs admin rights; on Android, install Termux:API and its app for the battery temperature).';
  }

  redrawCharts();
}

async function refreshMetrics() {
  if (metricsState.paused) return;
  const badge = document.getElementById('metrics-badge');
  try {
    const data = await fetchJson('/api/system/metrics');
    updateMetricsUi({ cpu: Number(data.cpu) || 0, cpuScope: data.cpuScope, temperatures: data.temperatures, ram: Number(data.ram) || 0, ramTotal: Number(data.ramTotal) || 1 });
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
