// EZ Minecraft dashboard, part 7 of 7: Event wiring and boot (loaded last).
// Plain scripts that share one global scope, loaded in order by webapp/pages/layout.py::SCRIPTS.

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
  on('schedule-save-button', 'click', (event, button) => saveConfigFromForm(button));
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
  bindPlayers();
  on('mod-updates-check', 'click', (event, button) => checkModUpdates(button));
  on('mod-updates-all', 'click', (event, button) => applyModUpdates(null, button));
  on('mod-updates-rollback', 'click', (event, button) => rollbackModUpdates(button));
  on('logs-refresh-button', 'click', () => loadLogs());
  on('backup-schedule-form', 'submit', (event) => {
    event.preventDefault();
    saveBackupSchedule(event.submitter);
  });
  on('backup-prune-manual', 'change', () => saveBackupSchedule(null));
  on('alerts-discord-owner-remove', 'click', (event, button) => removeWebhook('discord_owner_url', button));
  on('alerts-discord-friends-remove', 'click', (event, button) => removeWebhook('discord_friends_url', button));
  on('modpack-export-button', 'click', (event, button) => exportModpack(button));
  bindInvite();

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
  await Promise.all([loadBackups(), loadInstalledMods(), loadAlerts(), loadUpdate(), loadWorlds(), loadPlayers(), loadInvite(), loadModpack(),
    loadDiagnosis(), loadLogs(), loadModUpdates(),
    refreshConsole(true)]);
  initModBrowser(); // after loadInstalledMods so cards can show "Installed"

  if (document.getElementById('console-output')) setInterval(refreshConsole, CONSOLE_POLL_MS);
  scheduleStatusPoll();
}

document.addEventListener('DOMContentLoaded', boot);
