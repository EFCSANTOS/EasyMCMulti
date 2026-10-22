"""Page sections: overview, setup, settings, alerts, updates."""
from __future__ import annotations

from webapp.pages.widgets import _copy_button, _icon, _switch

_SECTION_OVERVIEW_TILES = f"""
        <section id="overview" class="tiles">
          <article class="card tile">
            <div class="tile-label">{_icon("setup")} Server</div>
            <div class="tile-value" data-field="server_name">—</div>
            <div class="tile-sub" data-field="server_meta">—</div>
            <div class="tile-actions">
              <button id="backup-button" class="btn btn-ghost btn-sm" type="button">{_icon("archive")} Backup world</button>
            </div>
          </article>
          <article class="card tile">
            <div class="tile-label">{_icon("users")} Players online</div>
            <div class="tile-value" data-field="players">0</div>
            <div class="tile-sub" data-field="player_names">No one online</div>
          </article>
          <article class="card tile">
            <div class="tile-label">{_icon("remote")} Join address</div>
            <div class="address-row"><span class="address-kind">LAN</span>
              <code data-field="lan_join">—</code>{_copy_button("lan_join", "LAN address")}</div>
            <div class="address-row"><span class="address-kind">Internet</span>
              <code data-field="public_join">—</code>{_copy_button("public_join", "internet address")}</div>
          </article>
        </section>
"""

_SECTION_METRICS = """
        <section id="metrics" class="card">
          <div class="card-header">
            <div>
              <h2>System resources</h2>
              <p class="card-sub">Host machine, sampled every 2 seconds.</p>
            </div>
            <div class="card-tools">
              <span id="metrics-badge" class="badge badge-live">live</span>
              <button id="metrics-toggle" class="btn btn-ghost btn-sm" type="button">Pause</button>
            </div>
          </div>
          <div class="metrics-grid">
            <div class="metric-card">
              <div class="metric-head">
                <div class="metric-label">CPU usage</div>
                <div id="cpu-sub" class="metric-sub">waiting</div>
              </div>
              <div id="cpu-value" class="metric-value">—</div>
              <div class="chart-wrap">
                <canvas id="cpu-chart" height="120" aria-label="CPU usage over the last two minutes" role="img"></canvas>
                <div class="chart-tip" hidden></div>
              </div>
              <div class="metric-foot">
                <span>min <strong id="cpu-min">—</strong></span>
                <span>avg <strong id="cpu-avg">—</strong></span>
                <span>max <strong id="cpu-max">—</strong></span>
              </div>
            </div>
            <div class="metric-card">
              <div class="metric-head">
                <div class="metric-label">Memory used</div>
                <div id="ram-sub" class="metric-sub">waiting</div>
              </div>
              <div id="ram-value" class="metric-value">—</div>
              <div class="chart-wrap">
                <canvas id="ram-chart" height="120" aria-label="Memory usage over the last two minutes" role="img"></canvas>
                <div class="chart-tip" hidden></div>
              </div>
              <div class="metric-foot">
                <span>min <strong id="ram-min">—</strong></span>
                <span>avg <strong id="ram-avg">—</strong></span>
                <span>max <strong id="ram-max">—</strong></span>
              </div>
            </div>
          </div>
        </section>
"""

_COMMAND_FORM = """
          <form id="command-form" class="command-form">
            <span class="prompt" aria-hidden="true">&gt;</span>
            <input id="command-input" name="command" placeholder="Type a server command, e.g. say hello" autocomplete="off" aria-label="Server command" />
            <button type="submit" class="btn btn-primary btn-sm">Send</button>
          </form>
"""

_SECTION_CONSOLE_PREVIEW = f"""
        <section id="console" class="card">
          <div class="card-header">
            <h2>Console</h2>
            <a class="link" href="/dashboard/console">Open full console {_icon("arrow")}</a>
          </div>
          <pre id="console-output" class="console console-short">Waiting for logs...</pre>
          {_COMMAND_FORM}
        </section>
"""

_SECTION_SETUP = """
        <section id="setup" class="card">
          <form id="setup-form">
            <div class="form-grid">
              <div class="field">
                <label for="loader-select">Loader</label>
                <select id="loader-select" name="loader">
                  <option value="vanilla">Vanilla</option>
                  <option value="paper">Paper (plugins)</option>
                  <option value="fabric">Fabric (mods)</option>
                  <option value="forge">Forge (mods)</option>
                  <option value="neoforge">NeoForge (mods)</option>
                </select>
              </div>
              <div class="field">
                <label for="version-select">Minecraft version</label>
                <select id="version-select" name="version"></select>
              </div>
              <div class="field">
                <label for="ram-input">Memory (MB)</label>
                <input id="ram-input" name="ram_mb" type="number" min="512" step="256" value="2048" />
                <p class="hint">2048–4096 MB suits most small servers.</p>
              </div>
              <div class="field">
                <label for="port-input">Port</label>
                <input id="port-input" name="server_port" type="number" min="1024" max="65535" value="25565" />
                <p class="hint">Default Minecraft port is 25565.</p>
              </div>
            </div>
            <div class="form-actions">
              <p class="hint">Creating downloads Java and the server files if they are missing. This can take a few minutes.</p>
              <button type="submit" class="btn btn-ghost">Save</button>
              <button id="create-button" type="button" class="btn btn-primary">Create server</button>
            </div>
          </form>
        </section>
"""


_SECTION_ADVANCED = f"""
        <section id="advanced" class="settings-layout">
          <div class="card">
            <div class="card-header"><h2>World</h2></div>
            <div class="form-grid">
              <div class="field field-wide">
                <label for="motd-input">Server name (MOTD)</label>
                <input id="motd-input" maxlength="59" value="EZ Minecraft Server" />
              </div>
              <div class="field">
                <label for="gamemode-input">Game mode</label>
                <select id="gamemode-input">
                  <option value="survival">Survival</option>
                  <option value="creative">Creative</option>
                  <option value="adventure">Adventure</option>
                  <option value="spectator">Spectator</option>
                </select>
              </div>
              <div class="field">
                <label for="difficulty-input">Difficulty</label>
                <select id="difficulty-input">
                  <option value="peaceful">Peaceful</option>
                  <option value="easy">Easy</option>
                  <option value="normal">Normal</option>
                  <option value="hard">Hard</option>
                </select>
              </div>
              <div class="field">
                <label for="leveltype-input">World type</label>
                <select id="leveltype-input">
                  <option value="normal">Normal</option>
                  <option value="flat">Flat</option>
                  <option value="largebiomes">Large biomes</option>
                  <option value="amplified">Amplified</option>
                </select>
              </div>
              <div class="field">
                <label for="seed-input">Seed</label>
                <input id="seed-input" value="" placeholder="Random" />
              </div>
              <div class="field">
                <label for="maxplayers-input">Max players</label>
                <input id="maxplayers-input" type="number" min="1" max="100" value="10" />
              </div>
              <div class="field">
                <label for="viewdist-input">View distance (chunks)</label>
                <input id="viewdist-input" type="number" min="2" max="32" value="10" />
              </div>
              <div class="field">
                <label for="simdist-input">Simulation distance (chunks)</label>
                <input id="simdist-input" type="number" min="2" max="32" value="10" />
              </div>
            </div>
          </div>

          <div class="card">
            <div class="card-header"><h2>Rules</h2></div>
            <div class="switch-list">
              {_switch("online-mode", "Online mode", "Verify accounts with Mojang. Turn off only for offline LAN play.", True)}
              {_switch("pvp-mode", "PvP", "Players can damage each other.", True)}
              {_switch("spawn-monsters", "Spawn monsters", "Hostile mobs appear at night and in the dark.", True)}
              {_switch("nether-mode", "Nether", "Allow travel to the Nether.", True)}
              {_switch("allow-flight", "Allow flight", "Don't kick players who fly (needed by some mods).", False)}
              {_switch("whitelist-mode", "Whitelist", "Only whitelisted players can join.", False)}
              {_switch("command-blocks", "Command blocks", "Enable command blocks in the world.", True)}
            </div>
          </div>

          <div class="save-bar">
            <p class="hint">Changes apply the next time the server starts.</p>
            <button id="advanced-save-button" class="btn btn-primary" type="button">Save settings</button>
          </div>
        </section>
"""

_SECTION_ALWAYS_ON = f"""
        <section id="always-on" class="card">
          <div class="card-header">
            <div>
              <h2>Always-on</h2>
              <p class="card-sub">For a 24/7 server: what happens when the dashboard starts or the server crashes.</p>
            </div>
          </div>
          <div class="switch-list">
            {_switch("autostart-server", "Start Minecraft with the dashboard", "Starts the server automatically after a reboot or dashboard restart.", False)}
            {_switch("auto-restart", "Restart after a crash", "Waits 10 seconds, then restarts. Pauses after 3 crashes in 10 minutes.", True)}
            {_switch("autostart-playit", "Start the PlayIt tunnel with the dashboard", "Friends can reconnect after a reboot without you doing anything.", False)}
          </div>
          <div class="form-actions">
            <p class="hint">To start the dashboard itself on boot, see "Run 24/7" in the README.</p>
            <button id="always-on-save-button" class="btn btn-primary" type="button">Save</button>
          </div>
        </section>
"""

_SECTION_ALERTS = f"""
        <section id="alerts" class="card">
          <div class="card-header">
            <div>
              <h2>Alerts</h2>
              <p class="card-sub">Get an email or a Discord message when the server crashes, the dashboard link changes
                or a tunnel has a problem, and let your friends' Discord know when the server is up.</p>
            </div>
          </div>
          <form id="alerts-form" autocomplete="off">
            <div class="switch-list">
              {_switch("alerts-email-enabled", "Email me", "Uses your Gmail (or any SMTP) account to send the alerts.", False)}
            </div>
            <div class="form-grid alerts-grid">
              <div class="field">
                <label for="alerts-email-to">Send alerts to</label>
                <input id="alerts-email-to" type="email" placeholder="you@example.com" />
              </div>
              <div class="field">
                <label for="alerts-smtp-user">Send from (Gmail address)</label>
                <input id="alerts-smtp-user" type="email" placeholder="yourname@gmail.com" />
              </div>
              <div class="field">
                <label for="alerts-smtp-password">App password</label>
                <input id="alerts-smtp-password" type="password" autocomplete="new-password" placeholder="16-character app password" />
              </div>
            </div>
            <p class="hint">Gmail: create an app password at myaccount.google.com/apppasswords (needs 2-step verification).
              It is not your normal password and only works for sending mail.</p>
            <details class="advanced-smtp">
              <summary>Other email provider</summary>
              <div class="form-grid">
                <div class="field">
                  <label for="alerts-smtp-host">SMTP server</label>
                  <input id="alerts-smtp-host" value="smtp.gmail.com" />
                </div>
                <div class="field">
                  <label for="alerts-smtp-port">SMTP port</label>
                  <input id="alerts-smtp-port" type="number" min="1" max="65535" value="587" />
                  <p class="hint">587 (STARTTLS) or 465 (SSL).</p>
                </div>
              </div>
            </details>
            <div class="switch-list alerts-events">
              {_switch("alerts-notify-server", "Server crashes and outages", "Crash, auto-restart paused, back online.", True)}
              {_switch("alerts-notify-link", "New dashboard link", "Emails the new https link after every restart or reboot.", True)}
              {_switch("alerts-notify-tunnels", "Tunnel problems", "PlayIt or the dashboard link stopped unexpectedly.", True)}
              {_switch("alerts-notify-updates", "Updates", "A new version is available, or an update failed.", True)}
              {_switch("alerts-notify-security", "Sign-in lockouts", "Someone entered a wrong dashboard password too many times.", True)}
              {_switch("alerts-notify-backup", "Backup problems", "A scheduled or safety backup failed, or storage is running out.", True)}
            </div>
            <h3 class="subhead">Discord</h3>
            <p class="hint">In your Discord server: channel settings → Integrations → Webhooks → New Webhook → Copy
              Webhook URL. Use a private channel for yourself: it gets the dashboard link.</p>
            <div class="form-grid alerts-grid">
              <div class="field">
                <label for="alerts-discord-owner">Your channel (same alerts as email)</label>
                <input id="alerts-discord-owner" type="password" autocomplete="off" placeholder="https://discord.com/api/webhooks/…" />
                <button id="alerts-discord-owner-remove" class="btn btn-ghost btn-sm" type="button" hidden>Remove</button>
              </div>
              <div class="field">
                <label for="alerts-discord-friends">Friends' channel</label>
                <input id="alerts-discord-friends" type="password" autocomplete="off" placeholder="https://discord.com/api/webhooks/…" />
                <button id="alerts-discord-friends-remove" class="btn btn-ghost btn-sm" type="button" hidden>Remove</button>
              </div>
            </div>
            <div class="switch-list alerts-events">
              {_switch("alerts-friends-status", "Friends: server online / offline", "Posted when the server starts, stops or crashes.", True)}
              {_switch("alerts-friends-players", "Friends: who joins and leaves", "Grouped every 30 seconds.", True)}
              {_switch("alerts-friends-link", "Friends: new status page link", "Only the public status page link, never the dashboard.", True)}
            </div>
            <div class="field heartbeat-field">
              <label for="alerts-heartbeat-url">Heartbeat URL (optional, for when the whole phone goes down)</label>
              <input id="alerts-heartbeat-url" type="url" placeholder="https://hc-ping.com/your-check-id" />
              <p class="hint">Create a free check at healthchecks.io and paste its ping URL. The dashboard checks in every
                minute; if it stops (phone off, no internet, app killed), healthchecks.io emails you.</p>
            </div>
            <div class="form-actions">
              <p class="hint">Each alert is sent at most once every 10 minutes.</p>
              <button id="alerts-test-button" class="btn btn-ghost" type="button">Send test</button>
              <button id="alerts-save-button" class="btn btn-primary" type="submit">Save</button>
            </div>
          </form>
        </section>
"""

_SECTION_UPDATES = """
        <section id="updates" class="card">
          <div class="card-header">
            <div>
              <h2>Updates</h2>
              <p class="card-sub">Get the latest version from the git repository this app was installed from.</p>
            </div>
            <span id="update-badge" class="badge">Checking…</span>
          </div>
          <div class="address-row address-row-lg">
            <span class="address-kind">Installed</span>
            <code data-field="update_current">—</code>
          </div>
          <p id="update-summary" class="hint"></p>
          <ul id="update-commits" class="commit-list" hidden></ul>
          <p id="update-error" class="callout callout-error" hidden></p>
          <div class="switch-list">
            <label class="switch-row" for="update-auto-check">
              <span><span class="switch-label">Check for updates automatically</span>
                <span class="hint">Every 6 hours; emails you when an update is available (see Alerts).</span></span>
              <input id="update-auto-check" type="checkbox" class="switch" checked />
            </label>
            <label class="switch-row" for="update-auto-install">
              <span><span class="switch-label">Install updates automatically</span>
                <span class="hint">Only when no one is online. Minecraft restarts for about a minute.</span></span>
              <input id="update-auto-install" type="checkbox" class="switch" />
            </label>
          </div>
          <div class="form-actions">
            <p class="hint">Updating restarts the dashboard; Minecraft and PlayIt come back if they were running.</p>
            <button id="update-check-button" class="btn btn-ghost" type="button">Check now</button>
            <button id="update-apply-button" class="btn btn-primary" type="button" disabled>Update &amp; restart</button>
          </div>
        </section>
"""
