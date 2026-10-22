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

async function loadPlayers() {
  if (!document.getElementById('whitelist-list')) return;
  try {
    renderPlayers(await fetchJson('/api/players/lists'));
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
