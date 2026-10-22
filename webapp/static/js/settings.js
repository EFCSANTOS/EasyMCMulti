// EZ Minecraft dashboard, part 5 of 8: PlayIt, alerts, self-update and the remote dashboard link.
// Plain scripts that share one global scope, loaded in order by webapp/pages/layout.py::SCRIPTS.

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
  ['alerts-notify-backup', 'notify_backup', 'checkbox'],
  ['alerts-heartbeat-url', 'heartbeat_url', 'text'],
  ['alerts-friends-status', 'friends_notify_status', 'checkbox'],
  ['alerts-friends-players', 'friends_notify_players', 'checkbox'],
  ['alerts-friends-link', 'friends_notify_link', 'checkbox'],
];
// Write-only secrets: [input id, notify_config key, "<key>_set" flag from the server].
const ALERT_SECRETS = [
  ['alerts-discord-owner', 'discord_owner_url', 'discord_owner_set'],
  ['alerts-discord-friends', 'discord_friends_url', 'discord_friends_set'],
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
  ALERT_SECRETS.forEach(([id, , flag]) => {
    const input = document.getElementById(id);
    input.value = '';
    input.placeholder = settings[flag] ? 'Saved (paste a new URL to replace it)' : 'https://discord.com/api/webhooks/…';
    document.getElementById(`${id}-remove`).hidden = !settings[flag];
  });
}

async function removeWebhook(key, button) {
  await runAction(button, null, async () => {
    renderAlerts(await fetchJson('/api/notifications', 'PUT', { [key]: null }));
    return 'Webhook removed.';
  });
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
  ALERT_SECRETS.forEach(([id, key]) => {
    const value = document.getElementById(id).value.trim();
    if (value) payload[key] = value; // empty keeps the saved webhook
  });
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

// ---------- Invite friends + public status page ----------
function renderInvite(info) {
  const text = document.getElementById('invite-text');
  if (!text) return;
  text.textContent = info.invite || '';
  const toggle = document.getElementById('status-page-enabled');
  if (document.activeElement !== toggle && !toggle.disabled) toggle.checked = Boolean(info.enabled);
  document.getElementById('status-page-options').hidden = !info.enabled;
  setField('status_page_url', info.url || '—');
  document.getElementById('status-page-link').href = info.url || '#';
  document.getElementById('status-page-lan-note').hidden = !info.url_is_lan_only;
  document.getElementById('status-page-show-names').checked = Boolean(info.show_names);
  document.getElementById('status-page-show-lan').checked = Boolean(info.show_lan);
}

async function loadInvite() {
  if (!document.getElementById('invite-text')) return;
  try {
    renderInvite(await fetchJson('/api/status-page'));
  } catch (error) {
    // the next status poll retries
  }
}

async function changeStatusPage(body, button) {
  await runAction(button, null, async () => {
    const info = await fetchJson('/api/status-page', 'POST', body);
    renderInvite(info);
    return info.message;
  });
}

function bindInvite() {
  on('invite-copy-button', 'click', async (event, button) => {
    await runAction(button, null, async () => {
      await copyText(document.getElementById('invite-text').textContent);
      return 'Invite copied. Paste it in your group chat.';
    });
  });
  on('status-page-enabled', 'change', (event, toggle) => changeStatusPage({ action: toggle.checked ? 'enable' : 'disable' }, null));
  on('status-page-show-names', 'change', (event, toggle) => changeStatusPage({ action: 'settings', show_names: toggle.checked }, null));
  on('status-page-show-lan', 'change', (event, toggle) => changeStatusPage({ action: 'settings', show_lan: toggle.checked }, null));
  on('status-page-regenerate', 'click', (event, button) => {
    if (window.confirm('Make a new link? The old link stops working, so you will need to share the new one.')) {
      changeStatusPage({ action: 'regenerate' }, button);
    }
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
