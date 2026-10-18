from __future__ import annotations

import asyncio
import glob
import os
import sys
import time
from typing import Any

try:
    import psutil
except ModuleNotFoundError:  # pragma: no cover - installed in project venv for runtime metrics
    psutil = None

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from core import config_store, downloaders, java_manager, modrinth, netinfo, paths, playit
from core.server_process import ServerProcess, accept_eula, update_properties
from webapp.auth import validate_auth_settings, verify_basic_auth_header, verify_credentials
from webapp.state import RuntimeState
from webapp.websocket_manager import WebSocketManager

KNOWN_LOADERS = ("vanilla", "paper", "fabric", "forge", "neoforge")
FALLBACK_VERSIONS = {
    "vanilla": ["1.21.7", "1.21.6", "1.21.5", "1.21.4", "1.21.3", "1.21.2", "1.21.1", "1.20.4"],
    "paper": ["1.21.7", "1.21.6", "1.21.4", "1.21.1", "1.20.4"],
    "fabric": ["1.21.7", "1.21.6", "1.21.4", "1.20.4"],
    "forge": ["1.20.1", "1.19.2", "1.16.5"],
    "neoforge": ["1.21.7", "1.21.6", "1.21.1", "1.20.1"],
}

security = HTTPBasic()

DASHBOARD_CSS = """
      body { font-family: Arial, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 2rem; }
      .wrap { max-width: 980px; margin: 0 auto; }
      h1 { margin-bottom: 0.25rem; }
      .card { background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
      .row { display: flex; gap: 0.75rem; flex-wrap: wrap; align-items: center; }
      .row input, .row button, .row textarea { font: inherit; }
      .row input, .row textarea { background: #0b1120; color: #e2e8f0; border: 1px solid #475569; border-radius: 8px; padding: 0.65rem 0.75rem; }
      button { background: #2563eb; color: white; border: none; border-radius: 8px; padding: 0.7rem 1rem; cursor: pointer; }
      button.secondary { background: #374151; }
      button.danger { background: #dc2626; }
      pre { white-space: pre-wrap; word-break: break-word; background: #020817; border-radius: 8px; padding: 1rem; min-height: 120px; }
      label { display: block; margin-bottom: 0.45rem; color: #cbd5e1; }
      .status { font-weight: 600; }
"""

DASHBOARD_BODY_HTML = """<div class=\"wrap\">
      <h1>EZ Minecraft Server</h1>
      <h2>Dashboard</h2>

      <div class=\"card\">
        <form id=\"auth-form\" class=\"row\">
          <div>
            <label for=\"username\">Username</label>
            <input id=\"username\" name=\"username\" value=\"__DEFAULT_USERNAME__\" />
          </div>
          <div>
            <label for=\"password\">Password</label>
            <input id=\"password\" name=\"password\" type=\"password\" value=\"__DEFAULT_PASSWORD__\" />
          </div>
          <button type=\"submit\">Use credentials</button>
        </form>
      </div>

      <div class=\"card\">
        <form id=\"setup-form\" class=\"row\">
          <div>
            <label for=\"loader-select\">Loader</label>
            <select id=\"loader-select\" name=\"loader\">
              <option value=\"vanilla\">vanilla</option>
              <option value=\"paper\">paper</option>
              <option value=\"fabric\">fabric</option>
              <option value=\"forge\">forge</option>
              <option value=\"neoforge\">neoforge</option>
            </select>
          </div>
          <div>
            <label for=\"version-select\">Version</label>
            <select id=\"version-select\" name=\"version\"></select>
          </div>
          <div>
            <label for=\"ram-input\">RAM (MB)</label>
            <input id=\"ram-input\" name=\"ram_mb\" type=\"number\" min=\"512\" step=\"256\" value=\"2048\" />
          </div>
          <div>
            <label for=\"port-input\">Port</label>
            <input id=\"port-input\" name=\"server_port\" type=\"number\" min=\"1024\" max=\"65535\" value=\"25565\" />
          </div>
          <button type=\"submit\">Save config</button>
          <button id=\"create-button\" type=\"button\">Create server</button>
        </form>
      </div>
      <div class=\"card\">
        <h3>Advanced settings</h3>
        <div class=\"row\">
          <div>
            <label for=\"motd-input\">MOTD</label>
            <input id=\"motd-input\" value=\"EZ Minecraft Server\" />
          </div>
          <div>
            <label for=\"maxplayers-input\">Max players</label>
            <input id=\"maxplayers-input\" type=\"number\" min=\"1\" max=\"1000\" value=\"10\" />
          </div>
          <div>
            <label for=\"gamemode-input\">Game mode</label>
            <select id=\"gamemode-input\">
              <option value=\"survival\">survival</option>
              <option value=\"creative\">creative</option>
              <option value=\"adventure\">adventure</option>
              <option value=\"spectator\">spectator</option>
            </select>
          </div>
          <div>
            <label for=\"difficulty-input\">Difficulty</label>
            <select id=\"difficulty-input\">
              <option value=\"peaceful\">peaceful</option>
              <option value=\"easy\">easy</option>
              <option value=\"normal\">normal</option>
              <option value=\"hard\">hard</option>
            </select>
          </div>
        </div>
        <div class=\"row\" style=\"margin-top:0.75rem;\">
          <div>
            <label for=\"leveltype-input\">World type</label>
            <select id=\"leveltype-input\">
              <option value=\"normal\">normal</option>
              <option value=\"flat\">flat</option>
              <option value=\"largebiomes\">largebiomes</option>
              <option value=\"amplified\">amplified</option>
            </select>
          </div>
          <div>
            <label for=\"viewdist-input\">View distance</label>
            <input id=\"viewdist-input\" type=\"number\" min=\"3\" max=\"32\" value=\"10\" />
          </div>
          <div>
            <label for=\"simdist-input\">Simulation distance</label>
            <input id=\"simdist-input\" type=\"number\" min=\"3\" max=\"32\" value=\"10\" />
          </div>
          <div>
            <label for=\"seed-input\">Seed</label>
            <input id=\"seed-input\" value=\"\" placeholder=\"empty = random\" />
          </div>
        </div>
        <div class=\"row\" style=\"margin-top:0.75rem;\">
          <label><input id=\"online-mode\" type=\"checkbox\" checked /> Online mode</label>
          <label><input id=\"pvp-mode\" type=\"checkbox\" checked /> PvP</label>
          <label><input id=\"spawn-monsters\" type=\"checkbox\" checked /> Spawn monsters</label>
          <label><input id=\"allow-flight\" type=\"checkbox\" /> Allow flight</label>
          <label><input id=\"nether-mode\" type=\"checkbox\" checked /> Nether</label>
          <label><input id=\"whitelist-mode\" type=\"checkbox\" /> Whitelist</label>
          <label><input id=\"command-blocks\" type=\"checkbox\" checked /> Command blocks</label>
        </div>
      </div>

      <div class=\"card\">
        <div class=\"row\">
          <button id=\"start-button\">Start server</button>
          <button id=\"stop-button\" class=\"danger\">Stop server</button>
          <button id=\"restart-button\" class=\"secondary\">Restart server</button>
          <button id=\"backup-button\" class=\"secondary\">Backup world</button>
        </div>
      </div>

      <div class=\"card\">
        <div class=\"status\" id=\"status-panel\">Loading status...</div>
      </div>

      <div class=\"card\">
        <form id=\"command-form\" class=\"row\">
          <div style=\"flex:1\">
            <label for=\"command-input\">Console command</label>
            <input id=\"command-input\" name=\"command\" placeholder=\"say hello\" />
          </div>
          <button type=\"submit\">Send</button>
        </form>
      </div>

      <div class=\"card\">
        <h3>Remote access (PlayIt)</h3>
        <div class=\"row\">
          <button id=\"playit-config-button\" class=\"secondary\" type=\"button\">Configure tunnel</button>
          <button id=\"playit-start-button\" class=\"secondary\" type=\"button\">Start tunnel</button>
          <button id=\"playit-stop-button\" class=\"danger\" type=\"button\">Stop tunnel</button>
        </div>
        <div class=\"status\" id=\"playit-status-panel\">PlayIt not configured.</div>
      </div>

      <div class=\"card\">
        <h3>Mods / Plugins</h3>
        <div class=\"row\">
          <input id=\"mod-search-query\" placeholder=\"Search Modrinth\" style=\"flex:1; min-width: 220px;\" />
          <button id=\"mod-search-button\" class=\"secondary\" type=\"button\">Search</button>
          <button id=\"mod-refresh-button\" class=\"secondary\" type=\"button\">Refresh list</button>
        </div>
        <div class=\"row\" style=\"margin-top:0.75rem;\">
          <input id=\"mod-upload-input\" type=\"file\" multiple accept=\".jar,application/java-archive\" />
          <button id=\"mod-upload-button\" class=\"secondary\" type=\"button\">Upload jars</button>
        </div>
        <div class=\"row\" style=\"margin-top:0.75rem;\">
          <select id=\"mod-search-results\" size=\"6\" style=\"flex:1; min-width: 300px; background:#0b1120; color:#e2e8f0; border:1px solid #475569; border-radius:8px; padding:0.5rem;\"></select>
          <button id=\"mod-install-button\" type=\"button\">Install selected</button>
        </div>
        <div class=\"row\" style=\"margin-top:0.75rem;\">
          <select id=\"mod-installed-list\" size=\"6\" style=\"flex:1; min-width: 300px; background:#0b1120; color:#e2e8f0; border:1px solid #475569; border-radius:8px; padding:0.5rem;\"></select>
        </div>
      </div>

      <div class=\"card\">
        <h3>Admin commands</h3>
        <div class=\"row\">
          <input id=\"admin-player\" placeholder=\"Player name\" style=\"min-width: 180px;\" />
          <button id=\"admin-op\" class=\"secondary\" type=\"button\">OP</button>
          <button id=\"admin-deop\" class=\"secondary\" type=\"button\">De-OP</button>
          <button id=\"admin-kick\" class=\"secondary\" type=\"button\">Kick</button>
          <button id=\"admin-ban\" class=\"secondary\" type=\"button\">Ban</button>
        </div>
        <div class=\"row\" style=\"margin-top:0.75rem;\">
          <button id=\"admin-day\" class=\"secondary\" type=\"button\">Day</button>
          <button id=\"admin-night\" class=\"secondary\" type=\"button\">Night</button>
          <button id=\"admin-clear\" class=\"secondary\" type=\"button\">Clear weather</button>
          <button id=\"admin-rain\" class=\"secondary\" type=\"button\">Rain</button>
          <button id=\"admin-save\" class=\"secondary\" type=\"button\">Save all</button>
        </div>
      </div>

      <div class=\"card\">
        <h3>Backups</h3>
        <div class=\"row\">
          <select id=\"backup-list\" size=\"5\" style=\"flex:1; min-width: 280px; background:#0b1120; color:#e2e8f0; border:1px solid #475569; border-radius:8px; padding:0.5rem;\"></select>
          <button id=\"backup-restore-button\" class=\"secondary\" type=\"button\">Restore selected</button>
        </div>
      </div>

      <div class=\"card\">
        <h3>Console</h3>
        <pre id=\"console-output\">Waiting for logs...</pre>
      </div>
    </div>
"""

DASHBOARD_SCRIPT = """
      const state = {
        username: '__DEFAULT_USERNAME__',
        password: '__DEFAULT_PASSWORD__'
      };

      function getAuthHeader() {
        const user = state.username || '';
        const pass = state.password || '';
        return 'Basic ' + btoa(user + ':' + pass);
      }

      async function apiRequest(path, options = {}) {
        const headers = Object.assign({}, options.headers || {});
        headers.Authorization = getAuthHeader();
        const response = await fetch(path, Object.assign({ headers }, options));
        const text = await response.text();
        let data = null;
        try { data = text ? JSON.parse(text) : null; } catch (error) { data = text; }
        if (!response.ok) {
          throw new Error(data && data.detail ? data.detail : 'Request failed');
        }
        return data;
      }

      function renderStatus(payload) {
        const status = payload || {};
        const lines = [
          'State: ' + (status.state || 'unknown'),
          'Operation: ' + (status.operation || 'unknown'),
          'Install: ' + (status.install_status || 'ready') + ' (' + ((status.install_progress ?? 0) | 0) + '%)',
          'Loader: ' + (status.loader || 'unknown'),
          'Version: ' + (status.version || 'unknown'),
          'Players: ' + (status.players ?? 0),
          'LAN: ' + (status.lan_address || 'unknown'),
          'Public: ' + (status.public_address || 'unknown'),
        ];
        if (status.install_message) {
          lines.push('Message: ' + status.install_message);
        }
        document.getElementById('status-panel').textContent = lines.join('\\n');
      }

      function renderConsole(lines) {
        const output = Array.isArray(lines) && lines.length ? lines.join('\\n') : 'No console output yet.';
        document.getElementById('console-output').textContent = output;
      }

      function readConfigPayload() {
        return {
          loader: document.getElementById('loader-select').value,
          version: document.getElementById('version-select').value,
          ram_mb: Number(document.getElementById('ram-input').value || 2048),
          server_port: Number(document.getElementById('port-input').value || 25565),
          motd: document.getElementById('motd-input').value || 'EZ Minecraft Server',
          max_players: Number(document.getElementById('maxplayers-input').value || 10),
          gamemode: document.getElementById('gamemode-input').value || 'survival',
          difficulty: document.getElementById('difficulty-input').value || 'normal',
          level_type: document.getElementById('leveltype-input').value || 'normal',
          view_distance: Number(document.getElementById('viewdist-input').value || 10),
          simulation_distance: Number(document.getElementById('simdist-input').value || 10),
          level_seed: document.getElementById('seed-input').value || '',
          online_mode: document.getElementById('online-mode').checked,
          pvp: document.getElementById('pvp-mode').checked,
          spawn_monsters: document.getElementById('spawn-monsters').checked,
          allow_flight: document.getElementById('allow-flight').checked,
          allow_nether: document.getElementById('nether-mode').checked,
          white_list: document.getElementById('whitelist-mode').checked,
          enable_command_block: document.getElementById('command-blocks').checked,
        };
      }

      async function loadVersions(loader) {
        try {
          const payload = await apiRequest('/api/versions/' + loader);
          const versionSelect = document.getElementById('version-select');
          versionSelect.innerHTML = '';
          for (const version of payload.versions || []) {
            const option = document.createElement('option');
            option.value = version;
            option.textContent = version;
            versionSelect.appendChild(option);
          }
        } catch (error) {
          const versionSelect = document.getElementById('version-select');
          versionSelect.innerHTML = '<option value="">Unavailable</option>';
        }
      }

      async function refreshConfig() {
        try {
          const payload = await apiRequest('/api/config');
          const loader = document.getElementById('loader-select');
          const version = document.getElementById('version-select');
          const ram = document.getElementById('ram-input');
          const port = document.getElementById('port-input');
          const motd = document.getElementById('motd-input');
          const maxPlayers = document.getElementById('maxplayers-input');
          const gamemode = document.getElementById('gamemode-input');
          const difficulty = document.getElementById('difficulty-input');
          const levelType = document.getElementById('leveltype-input');
          const viewDist = document.getElementById('viewdist-input');
          const simDist = document.getElementById('simdist-input');
          const seed = document.getElementById('seed-input');
          loader.value = payload.loader || 'vanilla';
          ram.value = payload.ram_mb || 2048;
          port.value = payload.server_port || 25565;
          motd.value = payload.motd || 'EZ Minecraft Server';
          maxPlayers.value = payload.max_players || 10;
          gamemode.value = payload.gamemode || 'survival';
          difficulty.value = payload.difficulty || 'normal';
          levelType.value = payload.level_type || 'normal';
          viewDist.value = payload.view_distance || 10;
          simDist.value = payload.simulation_distance || 10;
          seed.value = payload.level_seed || '';
          document.getElementById('online-mode').checked = payload.online_mode !== false;
          document.getElementById('pvp-mode').checked = payload.pvp !== false;
          document.getElementById('spawn-monsters').checked = payload.spawn_monsters !== false;
          document.getElementById('allow-flight').checked = Boolean(payload.allow_flight);
          document.getElementById('nether-mode').checked = payload.allow_nether !== false;
          document.getElementById('whitelist-mode').checked = Boolean(payload.white_list);
          document.getElementById('command-blocks').checked = payload.enable_command_block !== false;
          await loadVersions(loader.value);
          if (payload.version) {
            version.value = payload.version;
          }
        } catch (error) {
          // keep defaults
        }
      }

      async function refreshStatus() {
        try {
          const payload = await apiRequest('/api/status');
          renderStatus(payload);
        } catch (error) {
          document.getElementById('status-panel').textContent = 'Status unavailable: ' + error.message;
        }
      }

      async function refreshConsole() {
        try {
          const payload = await apiRequest('/api/console');
          renderConsole(payload && Array.isArray(payload.lines) ? payload.lines : []);
        } catch (error) {
          document.getElementById('console-output').textContent = 'Console unavailable: ' + error.message;
        }
      }

      async function refreshPlayItStatus() {
        try {
          const payload = await apiRequest('/api/playit/status');
          const panel = document.getElementById('playit-status-panel');
          if (!payload.configured) {
            panel.textContent = 'PlayIt not configured yet.';
            return;
          }
          const address = payload.address ? ' -> ' + payload.address : ' (waiting for tunnel address)';
          panel.textContent = 'PlayIt ' + payload.state + address;
        } catch (error) {
          document.getElementById('playit-status-panel').textContent = 'PlayIt unavailable: ' + error.message;
        }
      }

      function renderModList(items) {
        const list = document.getElementById('mod-installed-list');
        list.innerHTML = '';
        for (const item of items || []) {
          const option = document.createElement('option');
          option.value = item.path || item.name;
          option.textContent = item.name || item.path;
          list.appendChild(option);
        }
      }

      function renderModSearchResults(items) {
        const list = document.getElementById('mod-search-results');
        list.innerHTML = '';
        for (const item of items || []) {
          const option = document.createElement('option');
          option.value = item.slug || item.title;
          option.textContent = item.title || item.slug;
          list.appendChild(option);
        }
      }

      function renderBackupList(items) {
        const list = document.getElementById('backup-list');
        list.innerHTML = '';
        for (const item of items || []) {
          const option = document.createElement('option');
          option.value = item;
          option.textContent = item.split(/[\\/]/).pop();
          list.appendChild(option);
        }
      }

      async function refreshMods() {
        try {
          const payload = await apiRequest('/api/server/mods');
          renderModList(payload.mods || []);
        } catch (error) {
          renderModList([]);
        }
      }

      async function refreshBackups() {
        try {
          const payload = await apiRequest('/api/server/backups');
          renderBackupList(payload.backups || []);
        } catch (error) {
          renderBackupList([]);
        }
      }

      document.getElementById('auth-form').addEventListener('submit', (event) => {
        event.preventDefault();
        state.username = document.getElementById('username').value;
        state.password = document.getElementById('password').value;
        refreshStatus();
        refreshConsole();
      });

      document.getElementById('loader-select').addEventListener('change', async (event) => {
        await loadVersions(event.target.value);
      });

      document.getElementById('setup-form').addEventListener('submit', async (event) => {
        event.preventDefault();
        const payload = readConfigPayload();
        try {
          await apiRequest('/api/config', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
          await refreshStatus();
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('create-button').addEventListener('click', async () => {
        try {
          await apiRequest('/api/server/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(readConfigPayload()),
          });
          await refreshStatus();
          await refreshConsole();
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('start-button').addEventListener('click', async () => {
        try {
          await apiRequest('/api/server/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(readConfigPayload()),
          });
          await refreshStatus();
          await refreshConsole();
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('stop-button').addEventListener('click', async () => {
        try {
          await apiRequest('/api/server/stop', { method: 'POST' });
          await refreshStatus();
          await refreshConsole();
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('restart-button').addEventListener('click', async () => {
        try {
          await apiRequest('/api/server/restart', { method: 'POST' });
          await refreshStatus();
          await refreshConsole();
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('backup-button').addEventListener('click', async () => {
        try {
          const payload = await apiRequest('/api/server/backup', { method: 'POST' });
          const archive = payload && payload.archive ? payload.archive : 'backup created';
          alert('Backup created: ' + archive);
          await refreshStatus();
          await refreshConsole();
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('command-form').addEventListener('submit', async (event) => {
        event.preventDefault();
        const input = document.getElementById('command-input');
        const command = input.value.trim();
        if (!command) return;
        try {
          await apiRequest('/api/server/command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command })
          });
          input.value = '';
          await refreshConsole();
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('playit-config-button').addEventListener('click', async () => {
        try {
          await apiRequest('/api/playit/configure', { method: 'POST' });
          await refreshPlayItStatus();
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('playit-start-button').addEventListener('click', async () => {
        try {
          await apiRequest('/api/playit/start', { method: 'POST' });
          await refreshPlayItStatus();
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('playit-stop-button').addEventListener('click', async () => {
        try {
          await apiRequest('/api/playit/stop', { method: 'POST' });
          await refreshPlayItStatus();
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('mod-search-button').addEventListener('click', async () => {
        const query = document.getElementById('mod-search-query').value.trim();
        if (!query) return;
        try {
          const payload = await apiRequest('/api/modrinth/search?query=' + encodeURIComponent(query));
          renderModSearchResults(payload.items || []);
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('mod-install-button').addEventListener('click', async () => {
        const select = document.getElementById('mod-search-results');
        const slug = select.value;
        if (!slug) return;
        try {
          await apiRequest('/api/modrinth/install', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ slug })
          });
          await refreshMods();
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('mod-refresh-button').addEventListener('click', async () => {
        await refreshMods();
      });

      document.getElementById('mod-upload-button').addEventListener('click', async () => {
        const input = document.getElementById('mod-upload-input');
        if (!input.files || input.files.length === 0) return;
        const form = new FormData();
        for (const file of input.files) {
          form.append('files', file);
        }
        try {
          await apiRequest('/api/server/mods/upload', { method: 'POST', body: form });
          input.value = '';
          await refreshMods();
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('backup-restore-button').addEventListener('click', async () => {
        const backup = document.getElementById('backup-list').value;
        if (!backup) return;
        try {
          await apiRequest('/api/server/backups/restore', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ backup_path: backup })
          });
          alert('Backup restored.');
        } catch (error) {
          alert(error.message);
        }
      });

      const adminActions = {
        'admin-op': 'op {player}',
        'admin-deop': 'deop {player}',
        'admin-kick': 'kick {player}',
        'admin-ban': 'ban {player}',
        'admin-day': 'time set day',
        'admin-night': 'time set night',
        'admin-clear': 'weather clear',
        'admin-rain': 'weather rain',
        'admin-save': 'save-all'
      };
      for (const [id, commandTemplate] of Object.entries(adminActions)) {
        document.getElementById(id).addEventListener('click', async () => {
          const player = document.getElementById('admin-player').value.trim();
          const command = commandTemplate.includes('{player}')
            ? commandTemplate.replace('{player}', player || 'PlayerName')
            : commandTemplate;
          try {
            await apiRequest('/api/server/command', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ command })
            });
            await refreshConsole();
          } catch (error) {
            alert(error.message);
          }
        });
      }

      refreshConfig();
      refreshStatus();
      refreshConsole();
      refreshPlayItStatus();
      refreshMods();
      refreshBackups();
      setInterval(() => {
        refreshStatus();
        refreshConsole();
        refreshPlayItStatus();
        refreshMods();
        refreshBackups();
      }, 2000);
"""

DASHBOARD_HTML_TEMPLATE = """<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>EZ Minecraft Server Dashboard</title>
    <style>
      body { font-family: Arial, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 2rem; }
      .wrap { max-width: 980px; margin: 0 auto; }
      h1 { margin-bottom: 0.25rem; }
      .card { background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
      .row { display: flex; gap: 0.75rem; flex-wrap: wrap; align-items: center; }
      .row input, .row button, .row textarea { font: inherit; }
      .row input, .row textarea { background: #0b1120; color: #e2e8f0; border: 1px solid #475569; border-radius: 8px; padding: 0.65rem 0.75rem; }
      button { background: #2563eb; color: white; border: none; border-radius: 8px; padding: 0.7rem 1rem; cursor: pointer; }
      button.secondary { background: #374151; }
      button.danger { background: #dc2626; }
      pre { white-space: pre-wrap; word-break: break-word; background: #020817; border-radius: 8px; padding: 1rem; min-height: 120px; }
      label { display: block; margin-bottom: 0.45rem; color: #cbd5e1; }
      .status { font-weight: 600; }
    </style>
  </head>
  <body>
    <div class=\"wrap\">
      <h1>EZ Minecraft Server</h1>
      <h2>Dashboard</h2>

      <div class=\"card\">
        <form id=\"auth-form\" class=\"row\">
          <div>
            <label for=\"username\">Username</label>
            <input id=\"username\" name=\"username\" value=\"__DEFAULT_USERNAME__\" />
          </div>
          <div>
            <label for=\"password\">Password</label>
            <input id=\"password\" name=\"password\" type=\"password\" value=\"__DEFAULT_PASSWORD__\" />
          </div>
          <button type=\"submit\">Use credentials</button>
        </form>
      </div>

      <div class=\"card\">        <form id="setup-form" class="row">
          <div>
            <label for="loader-select">Loader</label>
            <select id="loader-select" name="loader">
              <option value="vanilla">vanilla</option>
              <option value="paper">paper</option>
              <option value="fabric">fabric</option>
              <option value="forge">forge</option>
              <option value="neoforge">neoforge</option>
            </select>
          </div>
          <div>
            <label for="version-select">Version</label>
            <select id="version-select" name="version"></select>
          </div>
          <div>
            <label for="ram-input">RAM (MB)</label>
            <input id="ram-input" name="ram_mb" type="number" min="512" step="256" value="2048" />
          </div>
          <div>
            <label for="port-input">Port</label>
            <input id="port-input" name="server_port" type="number" min="1024" max="65535" value="25565" />
          </div>
          <button type="submit">Save config</button>
          <button id="create-button" type="button">Create server</button>
        </form>
      </div>
      <div class="card">
        <h3>Advanced settings</h3>
        <div class="row">
          <div>
            <label for="motd-input">MOTD</label>
            <input id="motd-input" value="EZ Minecraft Server" />
          </div>
          <div>
            <label for="maxplayers-input">Max players</label>
            <input id="maxplayers-input" type="number" min="1" max="1000" value="10" />
          </div>
          <div>
            <label for="gamemode-input">Game mode</label>
            <select id="gamemode-input">
              <option value="survival">survival</option>
              <option value="creative">creative</option>
              <option value="adventure">adventure</option>
              <option value="spectator">spectator</option>
            </select>
          </div>
          <div>
            <label for="difficulty-input">Difficulty</label>
            <select id="difficulty-input">
              <option value="peaceful">peaceful</option>
              <option value="easy">easy</option>
              <option value="normal">normal</option>
              <option value="hard">hard</option>
            </select>
          </div>
        </div>
        <div class="row" style="margin-top:0.75rem;">
          <div>
            <label for="leveltype-input">World type</label>
            <select id="leveltype-input">
              <option value="normal">normal</option>
              <option value="flat">flat</option>
              <option value="largebiomes">largebiomes</option>
              <option value="amplified">amplified</option>
            </select>
          </div>
          <div>
            <label for="viewdist-input">View distance</label>
            <input id="viewdist-input" type="number" min="3" max="32" value="10" />
          </div>
          <div>
            <label for="simdist-input">Simulation distance</label>
            <input id="simdist-input" type="number" min="3" max="32" value="10" />
          </div>
          <div>
            <label for="seed-input">Seed</label>
            <input id="seed-input" value="" placeholder="empty = random" />
          </div>
        </div>
        <div class="row" style="margin-top:0.75rem;">
          <label><input id="online-mode" type="checkbox" checked /> Online mode</label>
          <label><input id="pvp-mode" type="checkbox" checked /> PvP</label>
          <label><input id="spawn-monsters" type="checkbox" checked /> Spawn monsters</label>
          <label><input id="allow-flight" type="checkbox" /> Allow flight</label>
          <label><input id="nether-mode" type="checkbox" checked /> Nether</label>
          <label><input id="whitelist-mode" type="checkbox" /> Whitelist</label>
          <label><input id="command-blocks" type="checkbox" checked /> Command blocks</label>
        </div>
      </div>
      <div class="card">        <div class=\"row\">
          <button id=\"start-button\">Start server</button>
          <button id=\"stop-button\" class=\"danger\">Stop server</button>          <button id=\"restart-button\" class=\"secondary\">Restart server</button>
          <button id=\"backup-button\" class=\"secondary\">Backup world</button>        </div>
      </div>

      <div class=\"card\">
        <div class=\"status\" id=\"status-panel\">Loading status...</div>
      </div>

      <div class=\"card\">
        <form id=\"command-form\" class=\"row\">
          <div style=\"flex:1\">
            <label for=\"command-input\">Console command</label>
            <input id=\"command-input\" name=\"command\" placeholder=\"say hello\" />
          </div>
          <button type=\"submit\">Send</button>
        </form>
      </div>

      <div class=\"card\">        <h3>Remote access (PlayIt)</h3>
        <div class="row">
          <button id="playit-config-button" class="secondary" type="button">Configure tunnel</button>
          <button id="playit-start-button" class="secondary" type="button">Start tunnel</button>
          <button id="playit-stop-button" class="danger" type="button">Stop tunnel</button>
        </div>
        <div class="status" id="playit-status-panel">PlayIt not configured.</div>
      </div>

      <div class="card">
        <h3>Mods / Plugins</h3>
        <div class="row">
          <input id="mod-search-query" placeholder="Search Modrinth" style="flex:1; min-width: 220px;" />
          <button id="mod-search-button" class="secondary" type="button">Search</button>
          <button id="mod-refresh-button" class="secondary" type="button">Refresh list</button>
        </div>
        <div class="row" style="margin-top:0.75rem;">
          <input id="mod-upload-input" type="file" multiple accept=".jar,application/java-archive" />
          <button id="mod-upload-button" class="secondary" type="button">Upload jars</button>
        </div>
        <div class="row" style="margin-top:0.75rem;">
          <select id="mod-search-results" size="6" style="flex:1; min-width: 300px; background:#0b1120; color:#e2e8f0; border:1px solid #475569; border-radius:8px; padding:0.5rem;"></select>
          <button id="mod-install-button" type="button">Install selected</button>
        </div>
        <div class="row" style="margin-top:0.75rem;">
          <select id="mod-installed-list" size="6" style="flex:1; min-width: 300px; background:#0b1120; color:#e2e8f0; border:1px solid #475569; border-radius:8px; padding:0.5rem;"></select>
        </div>
      </div>

      <div class="card">
        <h3>Admin commands</h3>
        <div class="row">
          <input id="admin-player" placeholder="Player name" style="min-width: 180px;" />
          <button id="admin-op" class="secondary" type="button">OP</button>
          <button id="admin-deop" class="secondary" type="button">De-OP</button>
          <button id="admin-kick" class="secondary" type="button">Kick</button>
          <button id="admin-ban" class="secondary" type="button">Ban</button>
        </div>
        <div class="row" style="margin-top:0.75rem;">
          <button id="admin-day" class="secondary" type="button">Day</button>
          <button id="admin-night" class="secondary" type="button">Night</button>
          <button id="admin-clear" class="secondary" type="button">Clear weather</button>
          <button id="admin-rain" class="secondary" type="button">Rain</button>
          <button id="admin-save" class="secondary" type="button">Save all</button>
        </div>
      </div>

      <div class="card">
        <h3>Backups</h3>
        <div class="row">
          <select id="backup-list" size="5" style="flex:1; min-width: 280px; background:#0b1120; color:#e2e8f0; border:1px solid #475569; border-radius:8px; padding:0.5rem;"></select>
          <button id="backup-restore-button" class="secondary" type="button">Restore selected</button>
        </div>
      </div>

      <div class="card">
        <h3>Console</h3>
        <pre id="console-output">Waiting for logs...</pre>
      </div>
    </div>

    <script>
      const state = {
        username: '__DEFAULT_USERNAME__',
        password: '__DEFAULT_PASSWORD__'
      };

      function getAuthHeader() {
        const user = state.username || '';
        const pass = state.password || '';
        return 'Basic ' + btoa(user + ':' + pass);
      }

      async function apiRequest(path, options = {}) {
        const headers = Object.assign({}, options.headers || {});
        headers.Authorization = getAuthHeader();
        const response = await fetch(path, Object.assign({ headers }, options));
        const text = await response.text();
        let data = null;
        try { data = text ? JSON.parse(text) : null; } catch (error) { data = text; }
        if (!response.ok) {
          throw new Error(data && data.detail ? data.detail : 'Request failed');
        }
        return data;
      }

      function renderStatus(payload) {
        const status = payload || {};
        const lines = [
          'State: ' + (status.state || 'unknown'),
          'Operation: ' + (status.operation || 'unknown'),
          'Install: ' + (status.install_status || 'ready') + ' (' + ((status.install_progress ?? 0) | 0) + '%)',
          'Loader: ' + (status.loader || 'unknown'),
          'Version: ' + (status.version || 'unknown'),
          'Players: ' + (status.players ?? 0),
          'LAN: ' + (status.lan_address || 'unknown'),
          'Public: ' + (status.public_address || 'unknown'),
        ];
        if (status.install_message) {
          lines.push('Message: ' + status.install_message);
        }
        document.getElementById('status-panel').textContent = lines.join('\\n');
      }

      function renderConsole(lines) {
        const output = Array.isArray(lines) && lines.length ? lines.join('\\n') : 'No console output yet.';
        document.getElementById('console-output').textContent = output;
      }

      function readConfigPayload() {
        return {
          loader: document.getElementById('loader-select').value,
          version: document.getElementById('version-select').value,
          ram_mb: Number(document.getElementById('ram-input').value || 2048),
          server_port: Number(document.getElementById('port-input').value || 25565),
          motd: document.getElementById('motd-input').value || 'EZ Minecraft Server',
          max_players: Number(document.getElementById('maxplayers-input').value || 10),
          gamemode: document.getElementById('gamemode-input').value || 'survival',
          difficulty: document.getElementById('difficulty-input').value || 'normal',
          level_type: document.getElementById('leveltype-input').value || 'normal',
          view_distance: Number(document.getElementById('viewdist-input').value || 10),
          simulation_distance: Number(document.getElementById('simdist-input').value || 10),
          level_seed: document.getElementById('seed-input').value || '',
          online_mode: document.getElementById('online-mode').checked,
          pvp: document.getElementById('pvp-mode').checked,
          spawn_monsters: document.getElementById('spawn-monsters').checked,
          allow_flight: document.getElementById('allow-flight').checked,
          allow_nether: document.getElementById('nether-mode').checked,
          white_list: document.getElementById('whitelist-mode').checked,
          enable_command_block: document.getElementById('command-blocks').checked,
        };
      }

      async function loadVersions(loader) {
        try {
          const payload = await apiRequest('/api/versions/' + loader);
          const versionSelect = document.getElementById('version-select');
          versionSelect.innerHTML = '';
          for (const version of payload.versions || []) {
            const option = document.createElement('option');
            option.value = version;
            option.textContent = version;
            versionSelect.appendChild(option);
          }
        } catch (error) {
          const versionSelect = document.getElementById('version-select');
          versionSelect.innerHTML = '<option value="">Unavailable</option>';
        }
      }

      async function refreshConfig() {
        try {
          const payload = await apiRequest('/api/config');
          const loader = document.getElementById('loader-select');
          const version = document.getElementById('version-select');
          const ram = document.getElementById('ram-input');
          const port = document.getElementById('port-input');
          const motd = document.getElementById('motd-input');
          const maxPlayers = document.getElementById('maxplayers-input');
          const gamemode = document.getElementById('gamemode-input');
          const difficulty = document.getElementById('difficulty-input');
          const levelType = document.getElementById('leveltype-input');
          const viewDist = document.getElementById('viewdist-input');
          const simDist = document.getElementById('simdist-input');
          const seed = document.getElementById('seed-input');
          loader.value = payload.loader || 'vanilla';
          ram.value = payload.ram_mb || 2048;
          port.value = payload.server_port || 25565;
          motd.value = payload.motd || 'EZ Minecraft Server';
          maxPlayers.value = payload.max_players || 10;
          gamemode.value = payload.gamemode || 'survival';
          difficulty.value = payload.difficulty || 'normal';
          levelType.value = payload.level_type || 'normal';
          viewDist.value = payload.view_distance || 10;
          simDist.value = payload.simulation_distance || 10;
          seed.value = payload.level_seed || '';
          document.getElementById('online-mode').checked = payload.online_mode !== false;
          document.getElementById('pvp-mode').checked = payload.pvp !== false;
          document.getElementById('spawn-monsters').checked = payload.spawn_monsters !== false;
          document.getElementById('allow-flight').checked = Boolean(payload.allow_flight);
          document.getElementById('nether-mode').checked = payload.allow_nether !== false;
          document.getElementById('whitelist-mode').checked = Boolean(payload.white_list);
          document.getElementById('command-blocks').checked = payload.enable_command_block !== false;
          await loadVersions(loader.value);
          if (payload.version) {
            version.value = payload.version;
          }
        } catch (error) {
          // keep defaults
        }
      }

      async function refreshStatus() {
        try {
          const payload = await apiRequest('/api/status');
          renderStatus(payload);
        } catch (error) {
          document.getElementById('status-panel').textContent = 'Status unavailable: ' + error.message;
        }
      }

      async function refreshConsole() {
        try {
          const payload = await apiRequest('/api/console');
          renderConsole(payload && Array.isArray(payload.lines) ? payload.lines : []);
        } catch (error) {
          document.getElementById('console-output').textContent = 'Console unavailable: ' + error.message;
        }
      }

      async function refreshPlayItStatus() {
        try {
          const payload = await apiRequest('/api/playit/status');
          const panel = document.getElementById('playit-status-panel');
          if (!payload.configured) {
            panel.textContent = 'PlayIt not configured yet.';
            return;
          }
          const address = payload.address ? ' -> ' + payload.address : ' (waiting for tunnel address)';
          panel.textContent = 'PlayIt ' + payload.state + address;
        } catch (error) {
          document.getElementById('playit-status-panel').textContent = 'PlayIt unavailable: ' + error.message;
        }
      }

      function renderModList(items) {
        const list = document.getElementById('mod-installed-list');
        list.innerHTML = '';
        for (const item of items || []) {
          const option = document.createElement('option');
          option.value = item.path || item.name;
          option.textContent = item.name || item.path;
          list.appendChild(option);
        }
      }

      function renderModSearchResults(items) {
        const list = document.getElementById('mod-search-results');
        list.innerHTML = '';
        for (const item of items || []) {
          const option = document.createElement('option');
          option.value = item.slug || item.title;
          option.textContent = item.title || item.slug;
          list.appendChild(option);
        }
      }

      function renderBackupList(items) {
        const list = document.getElementById('backup-list');
        list.innerHTML = '';
        for (const item of items || []) {
          const option = document.createElement('option');
          option.value = item;
          option.textContent = item.split(/[\\/]/).pop();
          list.appendChild(option);
        }
      }

      async function refreshMods() {
        try {
          const payload = await apiRequest('/api/server/mods');
          renderModList(payload.mods || []);
        } catch (error) {
          renderModList([]);
        }
      }

      async function refreshBackups() {
        try {
          const payload = await apiRequest('/api/server/backups');
          renderBackupList(payload.backups || []);
        } catch (error) {
          renderBackupList([]);
        }
      }

      document.getElementById('auth-form').addEventListener('submit', (event) => {
        event.preventDefault();
        state.username = document.getElementById('username').value;
        state.password = document.getElementById('password').value;
        refreshStatus();
        refreshConsole();
      });

      document.getElementById('loader-select').addEventListener('change', async (event) => {
        await loadVersions(event.target.value);
      });

      document.getElementById('setup-form').addEventListener('submit', async (event) => {
        event.preventDefault();
        const payload = readConfigPayload();
        try {
          await apiRequest('/api/config', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
          await refreshStatus();
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('create-button').addEventListener('click', async () => {
        try {
          await apiRequest('/api/server/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(readConfigPayload()),
          });
          await refreshStatus();
          await refreshConsole();
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('start-button').addEventListener('click', async () => {
        try {
          await apiRequest('/api/server/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(readConfigPayload()),
          });
          await refreshStatus();
          await refreshConsole();
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('stop-button').addEventListener('click', async () => {
        try {
          await apiRequest('/api/server/stop', { method: 'POST' });
          await refreshStatus();
          await refreshConsole();
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('restart-button').addEventListener('click', async () => {
        try {
          await apiRequest('/api/server/restart', { method: 'POST' });
          await refreshStatus();
          await refreshConsole();
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('backup-button').addEventListener('click', async () => {
        try {
          const payload = await apiRequest('/api/server/backup', { method: 'POST' });
          const archive = payload && payload.archive ? payload.archive : 'backup created';
          alert('Backup created: ' + archive);
          await refreshStatus();
          await refreshConsole();
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('command-form').addEventListener('submit', async (event) => {
        event.preventDefault();
        const input = document.getElementById('command-input');
        const command = input.value.trim();
        if (!command) return;
        try {
          await apiRequest('/api/server/command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command })
          });
          input.value = '';
          await refreshConsole();
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('playit-config-button').addEventListener('click', async () => {
        try {
          await apiRequest('/api/playit/configure', { method: 'POST' });
          await refreshPlayItStatus();
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('playit-start-button').addEventListener('click', async () => {
        try {
          await apiRequest('/api/playit/start', { method: 'POST' });
          await refreshPlayItStatus();
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('playit-stop-button').addEventListener('click', async () => {
        try {
          await apiRequest('/api/playit/stop', { method: 'POST' });
          await refreshPlayItStatus();
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('mod-search-button').addEventListener('click', async () => {
        const query = document.getElementById('mod-search-query').value.trim();
        if (!query) return;
        try {
          const payload = await apiRequest('/api/modrinth/search?query=' + encodeURIComponent(query));
          renderModSearchResults(payload.items || []);
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('mod-install-button').addEventListener('click', async () => {
        const select = document.getElementById('mod-search-results');
        const slug = select.value;
        if (!slug) return;
        try {
          await apiRequest('/api/modrinth/install', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ slug })
          });
          await refreshMods();
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('mod-refresh-button').addEventListener('click', async () => {
        await refreshMods();
      });

      document.getElementById('mod-upload-button').addEventListener('click', async () => {
        const input = document.getElementById('mod-upload-input');
        if (!input.files || input.files.length === 0) return;
        const form = new FormData();
        for (const file of input.files) {
          form.append('files', file);
        }
        try {
          await apiRequest('/api/server/mods/upload', { method: 'POST', body: form });
          input.value = '';
          await refreshMods();
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('backup-restore-button').addEventListener('click', async () => {
        const backup = document.getElementById('backup-list').value;
        if (!backup) return;
        try {
          await apiRequest('/api/server/backups/restore', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ backup_path: backup })
          });
          alert('Backup restored.');
        } catch (error) {
          alert(error.message);
        }
      });

      const adminActions = {
        'admin-op': 'op {player}',
        'admin-deop': 'deop {player}',
        'admin-kick': 'kick {player}',
        'admin-ban': 'ban {player}',
        'admin-day': 'time set day',
        'admin-night': 'time set night',
        'admin-clear': 'weather clear',
        'admin-rain': 'weather rain',
        'admin-save': 'save-all'
      };
      for (const [id, commandTemplate] of Object.entries(adminActions)) {
        document.getElementById(id).addEventListener('click', async () => {
          const player = document.getElementById('admin-player').value.trim();
          const command = commandTemplate.includes('{player}')
            ? commandTemplate.replace('{player}', player || 'PlayerName')
            : commandTemplate;
          try {
            await apiRequest('/api/server/command', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ command })
            });
            await refreshConsole();
          } catch (error) {
            alert(error.message);
          }
        });
      }

      refreshConfig();
      refreshStatus();
      refreshConsole();
      refreshPlayItStatus();
      refreshMods();
      refreshBackups();
      setInterval(() => {
        refreshStatus();
        refreshConsole();
        refreshPlayItStatus();
        refreshMods();
        refreshBackups();
      }, 2000);
    </script>
  </body>
</html>
"""


def _dashboard_html() -> str:
    username = os.getenv("EZ_WEBAPP_USERNAME", "admin")
    password = os.getenv("EZ_WEBAPP_PASSWORD", "admin")
    body = DASHBOARD_BODY_HTML.replace("__DEFAULT_USERNAME__", username).replace("__DEFAULT_PASSWORD__", password)
    script = DASHBOARD_SCRIPT.replace("__DEFAULT_USERNAME__", username).replace("__DEFAULT_PASSWORD__", password)
    return (
        "<!doctype html>\n"
        "<html lang=\"en\">\n"
        "  <head>\n"
        "    <meta charset=\"utf-8\" />\n"
        "    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />\n"
        "    <title>EZ Minecraft Server Dashboard</title>\n"
        "    <style>\n"
        + DASHBOARD_CSS + "\n"
        "    </style>\n"
        "  </head>\n"
        "  <body>\n"
        + body + "\n"
        "    <script>\n"
        + script + "\n"
        "    </script>\n"
        "  </body>\n"
        "</html>\n"
    )


def require_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    return verify_credentials(credentials)


def _server_properties_from_config(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "server-port": int(config.get("server_port", 25565)),
        "online-mode": bool(config.get("online_mode", True)),
        "max-players": int(config.get("max_players", 10)),
        "motd": str(config.get("motd", "EZ Minecraft Server")),
        "level-seed": str(config.get("level_seed", "")),
        "gamemode": str(config.get("gamemode", "survival")),
        "difficulty": str(config.get("difficulty", "normal")),
        "level-type": str(config.get("level_type", "normal")),
        "pvp": bool(config.get("pvp", True)),
        "view-distance": int(config.get("view_distance", 10)),
        "simulation-distance": int(config.get("simulation_distance", 10)),
        "spawn-monsters": bool(config.get("spawn_monsters", True)),
        "allow-nether": bool(config.get("allow_nether", True)),
        "allow-flight": bool(config.get("allow_flight", False)),
        "white-list": bool(config.get("white_list", False)),
        "enable-command-block": bool(config.get("enable_command_block", True)),
    }


def _default_config() -> dict[str, Any]:
    return {
        "loader": "vanilla",
        "version": "1.21.7",
        "ram_mb": 2048,
        "server_port": 25565,
        "motd": "EZ Minecraft Server",
        "max_players": 10,
        "gamemode": "survival",
        "difficulty": "normal",
        "level_type": "normal",
        "view_distance": 10,
        "simulation_distance": 10,
        "level_seed": "",
        "online_mode": True,
        "pvp": True,
        "spawn_monsters": True,
        "allow_flight": False,
        "allow_nether": True,
        "white_list": False,
        "enable_command_block": True,
    }


def _effective_config() -> dict[str, Any]:
    config = config_store.load()
    merged = _default_config()
    if isinstance(config, dict):
        merged.update(config)
    return merged


def _system_metrics_snapshot() -> dict[str, Any]:
    if psutil is not None:
        mem = psutil.virtual_memory()
        return {
            "cpu": float(psutil.cpu_percent(interval=None)),
            "ram": int(mem.used),
            "ramTotal": int(mem.total),
            "uptime": int(time.time() - _APP_START_TIME),
        }

    total = 1
    used = 0
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(stat)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            total = int(stat.ullTotalPhys)
            used = int(total - stat.ullAvailPhys)
    except Exception:
        total = 1
        used = 0

    return {
        "cpu": 0.0,
        "ram": int(used),
        "ramTotal": int(total),
        "uptime": 0,
    }


def _server_dir_for_config(config: dict[str, Any]) -> str:
    loader = str(config.get("loader", "vanilla"))
    version = str(config.get("version", "1.21.7"))
    return paths.server_dir(loader, version)


def _playit_on_exit(console_manager: Any, runtime: RuntimeState, code: int) -> None:
    runtime.playit_state = "stopped"
    runtime.playit_agent = None
    console_manager.publish(f"[playit] exited with code {code}")


def _server_is_installed(target_dir: str, loader: str) -> bool:
    if loader in {"vanilla", "paper", "fabric"}:
        return os.path.exists(os.path.join(target_dir, "server.jar"))

    if loader in {"forge", "neoforge"}:
        return bool(
            glob.glob(os.path.join(target_dir, f"{loader}-*.jar"))
            or glob.glob(os.path.join(target_dir, f"{loader}*.jar"))
            or glob.glob(os.path.join(target_dir, "libraries", "**", "win_args.txt"), recursive=True)
        )
    return False


_APP_START_TIME = time.time()


def create_app() -> FastAPI:
    validate_auth_settings()
    app = FastAPI(title="EZ Minecraft Server Launcher", version="0.1.0")
    runtime = RuntimeState()
    app.state.runtime = runtime
    app.state.console_manager = WebSocketManager()

    def append_console_line(line: str) -> None:
        app.state.console_manager.publish(line)

    def update_install_state(status: str, progress: float, message: str) -> None:
        runtime.install_status = status
        runtime.install_progress = max(0.0, min(float(progress), 100.0))
        runtime.install_message = message

    def ensure_server_installed(config: dict[str, Any]) -> str:
        loader = str(config.get("loader", "vanilla"))
        version = str(config.get("version", "1.21.7"))
        target_dir = _server_dir_for_config(config)
        if _server_is_installed(target_dir, loader):
            update_install_state("ready", 100.0, f"{loader} {version} ready.")
            return target_dir

        runtime.state = "installing"
        runtime.operation = "installing"
        update_install_state("installing", 0.0, f"Installing {loader} {version}...")

        def install_log(message: str) -> None:
            if message:
                append_console_line(f"[install] {message}")

        def install_progress(done: int, total: int) -> None:
            if total > 0:
                update_install_state("installing", (done / total) * 100.0, f"Downloading {loader} {version} ({done}/{total} bytes)")
            else:
                update_install_state("installing", 100.0, f"Installing {loader} {version}...")

        try:
            java_exe = java_manager.ensure_java(java_manager.required_java(version))
            downloaders.prepare(loader, version, target_dir, java_exe, log=install_log, progress=install_progress)
            accept_eula(target_dir)
        except Exception as exc:
            runtime.state = "stopped"
            runtime.operation = "idle"
            update_install_state("failed", 0.0, str(exc))
            raise

        runtime.state = "stopped"
        runtime.operation = "idle"
        update_install_state("ready", 100.0, f"{loader} {version} installed.")
        return target_dir

    @app.get("/")
    def root() -> HTMLResponse:
        return dashboard()

    @app.get("/login")
    def login() -> HTMLResponse:
        with open(os.path.join(os.path.dirname(__file__), "login.html"), "r", encoding="utf-8") as handle:
            return HTMLResponse(handle.read())

    @app.get("/dashboard")
    def dashboard() -> HTMLResponse:
        with open(os.path.join(os.path.dirname(__file__), "dashboard.html"), "r", encoding="utf-8") as handle:
            return HTMLResponse(handle.read())

    @app.get("/dashboard.css")
    def dashboard_css() -> FileResponse:
        return FileResponse(os.path.join(os.path.dirname(__file__), "dashboard.css"))

    @app.get("/dashboard.js")
    def dashboard_js() -> FileResponse:
        return FileResponse(os.path.join(os.path.dirname(__file__), "dashboard.js"))

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "service": "ez-server-webapp"}

    @app.get("/api/system/metrics")
    @app.get("/api/system")
    @app.get("/api/stats")
    @app.get("/api/metrics")
    @app.get("/metrics")
    def system_metrics(_: str = Depends(require_auth)) -> dict[str, Any]:
        return _system_metrics_snapshot()

    @app.get("/api/status")
    def status(_: str = Depends(require_auth)) -> dict[str, Any]:
        config = _effective_config()
        runtime.loader = str(config.get("loader", runtime.loader))
        runtime.version = str(config.get("version", runtime.version))
        runtime.lan_address = netinfo.lan_ip()
        runtime.public_address = netinfo.public_ip()
        playit_cfg = playit.load_config()
        runtime.playit_state = "configured" if playit_cfg.get("secret") else "not-configured"
        runtime.playit_address = playit_cfg.get("address") or runtime.public_address
        return runtime.snapshot()

    @app.get("/api/console")
    def console_history(_: str = Depends(require_auth)) -> dict[str, Any]:
        return {"lines": list(app.state.console_manager.history)}

    @app.get("/api/config")
    def get_config(_: str = Depends(require_auth)) -> dict[str, Any]:
        return _effective_config()

    @app.put("/api/config")
    def save_config(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        config = _effective_config()
        config.update(payload)
        config_store.save(config)
        if "loader" in config:
            runtime.loader = str(config["loader"])
        if "version" in config:
            runtime.version = str(config["version"])
        return config

    @app.get("/api/versions/{loader}")
    def versions(loader: str, _: str = Depends(require_auth)) -> dict[str, Any]:
        if loader not in KNOWN_LOADERS:
            raise HTTPException(status_code=400, detail=f"Unsupported loader: {loader}")

        try:
            versions = downloaders.list_versions(loader, include_snapshots=False)
        except Exception:
            versions = FALLBACK_VERSIONS.get(loader, [])

        if not versions:
            raise HTTPException(status_code=502, detail=f"No versions available for loader: {loader}")

        return {"loader": loader, "versions": versions}

    @app.post("/api/server/create")
    def create_server(payload: dict[str, Any] | None = None, _: str = Depends(require_auth)) -> dict[str, Any]:
        config = _effective_config()
        if payload:
            config.update(payload)
            config_store.save(config)
        loader = str(config.get("loader", "vanilla"))
        version = str(config.get("version", "1.21.7"))
        runtime.loader = loader
        runtime.version = version
        runtime.operation = "creating"
        runtime.state = "ready"
        try:
            target_dir = ensure_server_installed(config)
            update_properties(target_dir, **_server_properties_from_config(config))
        except Exception as exc:
            runtime.operation = "idle"
            runtime.state = "stopped"
            raise HTTPException(status_code=500, detail=f"Server creation failed: {exc}") from exc

        runtime.operation = "idle"
        runtime.state = "stopped"
        return {"state": runtime.state, "operation": runtime.operation, "loader": loader, "version": version, "server_dir": target_dir}

    @app.post("/api/server/start")
    def start_server(payload: dict[str, Any] | None = None, _: str = Depends(require_auth)) -> dict[str, Any]:
        if runtime.process is not None and runtime.process.is_running():
            return {"state": runtime.state, "operation": runtime.operation, "message": "Server already running."}

        config = _effective_config()
        if payload:
            config.update(payload)
            config_store.save(config)
        loader = str(config.get("loader", runtime.loader))
        version = str(config.get("version", runtime.version))
        runtime.loader = loader
        runtime.version = version
        runtime.state = "starting"
        runtime.operation = "starting"

        def on_exit(code: int) -> None:
            runtime.state = "stopped"
            runtime.operation = "idle"
            runtime.process = None
            append_console_line(f"[server] exited with code {code}")

        target_dir = ensure_server_installed(config)
        update_properties(target_dir, **_server_properties_from_config(config))

        java_exe = java_manager.ensure_java(java_manager.required_java(version))
        args = downloaders.launch_args(loader, java_exe, target_dir, int(config.get("ram_mb", 2048)))
        runtime.process = ServerProcess(
            args,
            cwd=target_dir,
            on_output=append_console_line,
            on_exit=on_exit,
        )
        runtime.process.start()
        runtime.state = "running"
        runtime.operation = "idle"
        append_console_line(f"[server] started with {loader} {version}")
        return {"state": runtime.state, "operation": runtime.operation, "message": "Server started."}

    @app.post("/api/server/stop")
    def stop_server(_: str = Depends(require_auth)) -> dict[str, Any]:
        if runtime.process is None or not runtime.process.is_running():
            runtime.state = "stopped"
            runtime.operation = "idle"
            return {"state": runtime.state, "operation": runtime.operation, "message": "Server is already stopped."}

        runtime.state = "stopping"
        runtime.operation = "stopping"
        runtime.process.stop()
        return {"state": runtime.state, "operation": runtime.operation, "message": "Stop requested."}

    @app.post("/api/server/restart")
    def restart_server(_: str = Depends(require_auth)) -> dict[str, Any]:
        stop_server(_)
        return start_server(_)

    @app.post("/api/server/command")
    def send_command(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        command = str(payload.get("command", "")).strip()
        if not command:
            raise HTTPException(status_code=400, detail="A command is required.")
        if runtime.process is None or not runtime.process.is_running():
            raise HTTPException(status_code=409, detail="Server is not running.")
        runtime.process.send(command)
        append_console_line(f"> {command}")
        return {"ok": True, "command": command}

    @app.post("/api/server/backup")
    def backup_server(_: str = Depends(require_auth)) -> dict[str, Any]:
        config = _effective_config()
        target_dir = _server_dir_for_config(config)
        world_dir = os.path.join(target_dir, "world")
        if not os.path.isdir(world_dir):
            return {"ok": False, "message": "No world directory found for backup."}
        archive = os.path.join(target_dir, f"world-backup-{__import__('time').strftime('%Y%m%d-%H%M%S')}.zip")
        import zipfile
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(world_dir):
                for name in files:
                    abs_path = os.path.join(root, name)
                    rel_path = os.path.relpath(abs_path, target_dir)
                    zf.write(abs_path, rel_path)
        return {"ok": True, "archive": archive}

    @app.get("/api/server/backups")
    def list_backups(_: str = Depends(require_auth)) -> dict[str, Any]:
        config = _effective_config()
        target_dir = _server_dir_for_config(config)
        backups = []
        if os.path.isdir(target_dir):
            for entry in os.listdir(target_dir):
                if entry.startswith("world-backup-") and entry.endswith(".zip"):
                    backups.append(os.path.join(target_dir, entry))
        return {"backups": backups}

    @app.post("/api/server/backups/restore")
    def restore_backup(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        backup_path = str(payload.get("backup_path", "")).strip()
        if not backup_path or not os.path.exists(backup_path):
            raise HTTPException(status_code=400, detail="A valid backup path is required.")
        config = _effective_config()
        target_dir = _server_dir_for_config(config)
        os.makedirs(target_dir, exist_ok=True)
        world_dir = os.path.join(target_dir, "world")
        if os.path.isdir(world_dir):
            backup_name = f"world-backup-pre-restore-{__import__('time').strftime('%Y%m%d-%H%M%S')}"
            os.rename(world_dir, os.path.join(target_dir, backup_name))
        import zipfile
        with zipfile.ZipFile(backup_path, "r") as zf:
            zf.extractall(target_dir)
        return {"ok": True, "message": f"Backup restored from {backup_path}"}

    @app.get("/api/server/mods")
    def list_mods(_: str = Depends(require_auth)) -> dict[str, Any]:
        config = _effective_config()
        target_dir = _server_dir_for_config(config)
        mods_dir = os.path.join(target_dir, "mods")
        plugins_dir = os.path.join(target_dir, "plugins")
        items = []
        for base_dir in (mods_dir, plugins_dir):
            if not os.path.isdir(base_dir):
                continue
            for name in sorted(os.listdir(base_dir)):
                if name.lower().endswith(".jar"):
                    items.append({"name": name, "path": os.path.join(base_dir, name)})
        return {"mods": items}

    @app.post("/api/server/mods/upload")
    def upload_mods(files: list[UploadFile] = File(default=[]), _: str = Depends(require_auth)) -> dict[str, Any]:
        if not files:
            raise HTTPException(status_code=400, detail="No mod files were uploaded.")
        config = _effective_config()
        target_dir = _server_dir_for_config(config)
        mod_target = os.path.join(target_dir, "mods") if str(config.get("loader", "vanilla")) in {"forge", "fabric", "neoforge"} else os.path.join(target_dir, "plugins")
        os.makedirs(mod_target, exist_ok=True)
        saved = []
        for upload in files:
            name = os.path.basename(upload.filename or "")
            if not name.lower().endswith(".jar"):
                continue
            dest = os.path.join(mod_target, name)
            with open(dest, "wb") as handle:
                while True:
                    chunk = upload.file.read(65536)
                    if not chunk:
                        break
                    handle.write(chunk)
            saved.append(dest)
        return {"ok": True, "saved": saved}

    @app.get("/api/modrinth/search")
    def modrinth_search(query: str, _: str = Depends(require_auth)) -> dict[str, Any]:
        term = (query or "").strip()
        if not term:
            raise HTTPException(status_code=400, detail="A Modrinth search query is required.")
        config = _effective_config()
        loader = str(config.get("loader", "vanilla"))
        version = str(config.get("version", "1.21.7"))
        items = modrinth.search(term, loader, version, limit=10)
        return {"items": items}

    @app.post("/api/modrinth/install")
    def modrinth_install(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        slug = str(payload.get("slug", "")).strip()
        if not slug:
            raise HTTPException(status_code=400, detail="A Modrinth project slug is required.")
        config = _effective_config()
        target_dir = _server_dir_for_config(config)
        loader = str(config.get("loader", "vanilla"))
        version = str(config.get("version", "1.21.7"))
        target_folder = os.path.join(target_dir, "mods") if loader in {"forge", "fabric", "neoforge"} else os.path.join(target_dir, "plugins")
        os.makedirs(target_folder, exist_ok=True)
        installed = modrinth.install(slug, loader, version, target_folder, log=append_console_line)
        return {"ok": True, "filename": installed}

    @app.get("/api/playit/status")
    def playit_status(_: str = Depends(require_auth)) -> dict[str, Any]:
        cfg = playit.load_config()
        state = runtime.playit_state if runtime.playit_agent and runtime.playit_agent.is_running() else ("running" if cfg.get("secret") and cfg.get("address") else "configured" if cfg.get("secret") else "not-configured")
        return {
            "configured": bool(cfg.get("secret")),
            "state": state,
            "address": cfg.get("address") or "",
            "secret_present": bool(cfg.get("secret")),
        }

    @app.post("/api/playit/configure")
    def configure_playit(_: str = Depends(require_auth)) -> dict[str, Any]:
        cfg = playit.load_config()
        if cfg.get("secret"):
            runtime.playit_state = "configured"
            runtime.playit_address = cfg.get("address") or runtime.playit_address
            return {
                "configured": True,
                "state": "configured",
                "address": cfg.get("address") or "",
                "message": "PlayIt already configured.",
            }

        secret = playit.claim_interactive(append_console_line, lambda: False)
        playit.ensure_agent(log=append_console_line)
        runtime.playit_agent = playit.PlayitAgent(on_output=append_console_line, on_exit=lambda code: _playit_on_exit(app.state.console_manager, runtime, code))
        runtime.playit_agent.start(secret)
        local_port = int(_effective_config().get("server_port", 25565))
        address = playit.ensure_tunnel(secret, local_port, append_console_line)
        playit.save_config(secret, address)
        runtime.playit_state = "running"
        runtime.playit_address = address
        return {
            "configured": True,
            "state": "running",
            "address": address,
            "message": "PlayIt tunnel configured and running.",
        }

    @app.post("/api/playit/start")
    def start_playit(_: str = Depends(require_auth)) -> dict[str, Any]:
        cfg = playit.load_config()
        if not cfg.get("secret"):
            return {"configured": False, "state": "not-configured", "address": "", "message": "PlayIt is not configured yet. Click Configure tunnel first."}
        if runtime.playit_agent and runtime.playit_agent.is_running():
            runtime.playit_state = "running"
            return {"configured": True, "state": "running", "address": cfg.get("address") or "", "message": "PlayIt tunnel is already running."}

        playit.ensure_agent(log=append_console_line)
        runtime.playit_agent = playit.PlayitAgent(on_output=append_console_line, on_exit=lambda code: _playit_on_exit(app.state.console_manager, runtime, code))
        runtime.playit_agent.start(cfg["secret"])
        local_port = int(_effective_config().get("server_port", 25565))
        address = playit.ensure_tunnel(cfg["secret"], local_port, append_console_line)
        playit.save_config(cfg["secret"], address)
        runtime.playit_state = "running"
        runtime.playit_address = address
        return {"configured": True, "state": "running", "address": address, "message": "PlayIt tunnel started."}

    @app.post("/api/playit/stop")
    def stop_playit(_: str = Depends(require_auth)) -> dict[str, Any]:
        if runtime.playit_agent and runtime.playit_agent.is_running():
            runtime.playit_agent.stop()
        runtime.playit_agent = None
        cfg = playit.load_config()
        runtime.playit_state = "stopped"
        runtime.playit_address = cfg.get("address") or runtime.playit_address
        return {"configured": bool(cfg.get("secret")), "state": "stopped", "address": cfg.get("address") or "", "message": "PlayIt tunnel stopped."}

    @app.get("/api/playit/config")
    def playit_config(_: str = Depends(require_auth)) -> dict[str, Any]:
        return playit.load_config()

    @app.websocket("/ws/console")
    async def console_websocket(websocket: WebSocket) -> None:
        auth_header = websocket.headers.get("authorization")
        try:
            verify_basic_auth_header(auth_header)
        except HTTPException:
            await websocket.close(code=1008)
            return

        await websocket.accept()
        await app.state.console_manager.connect(websocket)
        try:
            while True:
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                except asyncio.TimeoutError:
                    continue
                except WebSocketDisconnect:
                    break
        finally:
            await app.state.console_manager.disconnect(websocket)

    return app


app = create_app()
