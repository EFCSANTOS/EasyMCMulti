// EZ Minecraft dashboard, part 4 of 7: Players page (whitelist, operators, bans, recently seen).
// Plain scripts that share one global scope, loaded in order by webapp/pages/layout.py::SCRIPTS.

// ---------- Players ----------
const OP_LEVELS = { 1: 'Level 1 · spawn protection', 2: 'Level 2 · cheats', 3: 'Level 3 · moderation', 4: 'Level 4 · everything' };

function timeAgo(epochSeconds) {
  const minutes = Math.round((Date.now() / 1000 - epochSeconds) / 60);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  return hours < 24 ? `${hours} h ago` : `${Math.round(hours / 24)} d ago`;
}

// Resolves to true when the change went through (the toast shows the outcome either way).
async function changePlayerList(kind, body, button) {
  let changed = false;
  await runAction(button, null, async () => {
    const result = await fetchJson(`/api/players/${kind}`, 'POST', body);
    renderPlayers(result);
    changed = true;
    return result.message;
  });
  return changed;
}

function renderPlayers(data) {
  const whitelistBox = document.getElementById('whitelist-list');
  if (!whitelistBox) return;

  const toggle = document.getElementById('players-whitelist-enabled');
  if (document.activeElement !== toggle && !toggle.disabled) toggle.checked = Boolean(data.whitelist_enabled);
  document.getElementById('players-whitelist-off').hidden = Boolean(data.whitelist_enabled);

  renderItems(whitelistBox, data.whitelist || [], (player) => itemRow(player.name, null, null,
    rowButton('Remove', 'btn-ghost', (b) => changePlayerList('whitelist', { action: 'remove', name: player.name }, b))),
  'Nobody yet. Add your friends by their Minecraft name.');

  renderItems(document.getElementById('recent-players-list'), data.recent || [], (player) => {
    const meta = `${player.rejected ? 'Refused by the whitelist' : 'Joined'} · ${timeAgo(player.last_seen)}`;
    const buttons = player.whitelisted ? null : rowButton('Add to whitelist', 'btn-primary',
      (b) => changePlayerList('whitelist', { action: 'add', name: player.name }, b));
    return itemRow(player.name, null, meta, buttons);
  }, 'No one has joined since the dashboard started.');

  renderItems(document.getElementById('ops-list'), data.ops || [], (player) => itemRow(player.name, null,
    OP_LEVELS[player.level] || `Level ${player.level}`,
    rowButton('Remove', 'btn-ghost', (b) => changePlayerList('ops', { action: 'remove', name: player.name }, b))),
  'No operators.');

  const banned = (data.bans || []).map((ban) => ({ ...ban, kind: 'bans', label: ban.name }))
    .concat((data.ips || []).map((ban) => ({ ...ban, kind: 'ips', label: `IP ${ban.ip}` })));
  renderItems(document.getElementById('bans-list'), banned, (ban) => itemRow(ban.label, ban.reason || null, null,
    rowButton('Unban', 'btn-ghost', (b) => changePlayerList(ban.kind,
      ban.kind === 'ips' ? { action: 'remove', ip: ban.ip } : { action: 'remove', name: ban.name }, b))),
  'Nobody is banned.');
}

// ---------- Activity (playtime + players online by hour) ----------
const SVG_NS = 'http://www.w3.org/2000/svg';

function svgEl(tag, attrs, text) {
  const node = document.createElementNS(SVG_NS, tag);
  Object.entries(attrs || {}).forEach(([key, value]) => node.setAttribute(key, value));
  if (text !== undefined) node.textContent = text;
  return node;
}

function formatPlaytime(seconds) {
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return minutes % 60 ? `${hours} h ${minutes % 60} min` : `${hours} h`;
}

// Bars anchored to the baseline with 4px rounded tops, 2px gaps; hover shows a tooltip.
function drawActivityChart(container, hours) {
  container.replaceChildren();
  const width = Math.max(container.clientWidth, 240);
  const height = 140;
  const top = 8;
  const bottom = 20;
  const plot = height - top - bottom;
  const max = Math.max(...hours, 0.0001);
  const slot = width / 24;
  const svg = svgEl('svg', { viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: 'none' });
  const tip = el('div', 'chart-tip');
  tip.hidden = true;
  hours.forEach((value, hour) => {
    const barHeight = value > 0 ? Math.max(2, (value / max) * plot) : 0;
    const x = hour * slot + 1;
    const w = Math.max(2, slot - 2);
    const y = top + plot - barHeight;
    const r = Math.min(4, w / 2, barHeight);
    const hit = svgEl('rect', { class: 'bar-hit', x: hour * slot, y: top, width: slot, height: plot });
    const bar = svgEl('path', {
      class: 'bar',
      d: barHeight ? `M${x},${top + plot} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${top + plot} Z` : '',
    });
    hit.addEventListener('mouseenter', () => {
      bar.classList.add('is-hover');
      tip.replaceChildren(el('strong', null, value.toFixed(1)), el('span', null,
        `players on average, ${String(hour).padStart(2, '0')}:00–${String((hour + 1) % 24).padStart(2, '0')}:00`));
      tip.style.left = `${((hour + 0.5) / 24) * 100}%`;
      // Keep the tooltip inside the card near the edges (narrow phone screens).
      tip.style.transform = hour >= 18 ? 'translateX(-100%)' : (hour < 6 ? 'translateX(0)' : '');
      tip.hidden = false;
    });
    hit.addEventListener('mouseleave', () => {
      bar.classList.remove('is-hover');
      tip.hidden = true;
    });
    svg.append(hit, bar);
  });
  svg.appendChild(svgEl('line', { class: 'baseline', x1: 0, x2: width, y1: top + plot + 0.5, y2: top + plot + 0.5 }));
  [0, 6, 12, 18].forEach((hour) => svg.appendChild(svgEl('text', {
    class: 'axis-label', x: hour * slot + 1, y: height - 4 }, `${String(hour).padStart(2, '0')}:00`)));
  container.append(svg, tip);
}

function renderActivity(data) {
  const list = document.getElementById('activity-list');
  if (!list) return;
  const chart = document.getElementById('activity-chart');
  const any = (data.hours || []).some((value) => value > 0);
  chart.hidden = !any;
  if (any) drawActivityChart(chart, data.hours);
  renderItems(list, data.players || [], (player) => {
    const title = el('div', 'item-title', player.name);
    if (player.online) title.appendChild(el('span', 'badge badge-live', 'Online'));
    const since = new Date(player.first_seen * 1000).toLocaleDateString();
    return itemRow(title, `${formatPlaytime(player.playtime_seconds)} played · ${player.sessions} session(s)`,
      player.online ? `Playing now · first seen ${since}` : `Last seen ${timeAgo(player.last_seen)} · first seen ${since}`);
  }, 'No one has played yet. Joins and leaves are recorded from now on.');
}

async function loadActivity() {
  if (!document.getElementById('activity-list')) return;
  try {
    renderActivity(await fetchJson('/api/players/activity'));
  } catch (error) {
    // shown again on the next visit
  }
}

async function loadPlayers() {
  if (!document.getElementById('whitelist-list')) return;
  try {
    renderPlayers(await fetchJson('/api/players/lists'));
    await loadActivity();
  } catch (error) {
    setStatus(`Could not load players: ${error.message}`, 'error');
  }
}

function bindPlayers() {
  on('players-refresh-button', 'click', () => loadPlayers());
  on('players-whitelist-enabled', 'change', async (event, toggle) => {
    toggle.disabled = true;
    try {
      const wanted = toggle.checked;
      if (!await changePlayerList('whitelist', { action: wanted ? 'enable' : 'disable' }, null)) toggle.checked = !wanted;
    } finally {
      toggle.disabled = false;
    }
  });
  on('whitelist-add-form', 'submit', async (event) => {
    event.preventDefault();
    const input = document.getElementById('whitelist-name');
    if (await changePlayerList('whitelist', { action: 'add', name: input.value.trim() }, event.submitter)) input.value = '';
  });
  on('ops-add-form', 'submit', async (event) => {
    event.preventDefault();
    const input = document.getElementById('ops-name');
    const level = Number(document.getElementById('ops-level').value);
    if (await changePlayerList('ops', { action: 'add', name: input.value.trim(), level }, event.submitter)) input.value = '';
  });
}
