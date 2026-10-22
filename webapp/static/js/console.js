// EZ Minecraft dashboard, part 6 of 7: Console output and the CPU/RAM charts.
// Plain scripts that share one global scope, loaded in order by webapp/pages/layout.py::SCRIPTS.

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
