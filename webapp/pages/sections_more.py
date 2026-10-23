"""Page sections: mods, worlds, remote access, backups, console."""
from __future__ import annotations

from webapp.pages.sections_main import _COMMAND_FORM
from webapp.pages.widgets import _copy_button, _icon, _switch

_SECTION_MODS = f"""
        <section id="mods" class="stack">
          <div id="modpack-card" class="card" hidden>
            <div class="card-header">
              <div>
                <h2>Mod pack for friends</h2>
                <p class="card-sub">One file with every mod players need. They import it in the Modrinth App or
                  Prism Launcher (Add instance → Import) and can join right away.</p>
              </div>
              <div class="card-tools">
                <button id="modpack-export-button" class="btn btn-primary btn-sm" type="button">{_icon("archive")} Export pack (.mrpack)</button>
              </div>
            </div>
            <p class="hint">Server-only mods are left out. With the public status page on, friends can download the
              pack there too, so you don't have to send it.</p>
          </div>
          <div class="card">
            <div class="card-header">
              <div>
                <h2>Browse Modrinth</h2>
                <p id="mod-context" class="card-sub">Mods and plugins that work on your server.</p>
              </div>
            </div>
            <form id="mod-search-form" class="mod-toolbar">
              <div class="search-form">
                {_icon("search")}
                <input id="mod-search-query" type="search" placeholder="Search, or just browse below" aria-label="Search Modrinth" />
                <button id="mod-search-button" class="btn btn-primary btn-sm" type="submit">Search</button>
              </div>
              <select id="mod-category" aria-label="Category"><option value="">All categories</option></select>
              <select id="mod-sort" aria-label="Sort by">
                <option value="downloads">Most downloaded</option>
                <option value="relevance">Best match</option>
                <option value="follows">Most followed</option>
                <option value="updated">Recently updated</option>
                <option value="newest">Newest</option>
              </select>
            </form>
            <p id="mod-result-count" class="hint"></p>
            <div id="mod-search-results" class="mod-grid" aria-live="polite"></div>
            <div class="load-more-row">
              <button id="mod-load-more" class="btn btn-ghost" type="button" hidden>Load more</button>
            </div>
          </div>
          <div class="card">
            <div class="card-header">
              <div>
                <h2>Installed</h2>
                <p class="card-sub">Jars in the server's mods or plugins folder.</p>
              </div>
              <div class="card-tools">
                <button id="mod-refresh-button" class="icon-btn" type="button" title="Refresh" aria-label="Refresh installed list">{_icon("restart")}</button>
                <label class="btn btn-ghost btn-sm" for="mod-upload-input">{_icon("upload")} Upload .jar</label>
                <input id="mod-upload-input" class="visually-hidden" type="file" multiple accept=".jar,application/java-archive" />
              </div>
            </div>
            <div id="mod-installed-list" class="item-list"></div>
            <p class="hint">Restart the server after installing or removing mods. Removing needs the server stopped.</p>
          </div>
          <div id="mod-updates-card" class="card">
            <div class="card-header">
              <div>
                <h2>Updates</h2>
                <p id="mod-updates-sub" class="card-sub">Newer versions of your mods and plugins for this Minecraft version.
                  Checked once a day; updating needs the server stopped and makes a backup first.</p>
              </div>
              <div class="card-tools">
                <button id="mod-updates-check" class="btn btn-ghost btn-sm" type="button">Check for updates</button>
                <button id="mod-updates-all" class="btn btn-primary btn-sm" type="button" hidden>Update all</button>
              </div>
            </div>
            <div id="mod-updates-list" class="item-list"></div>
            <div id="mod-updates-undo" class="form-actions" hidden>
              <p class="hint">Something broke after the update? Put the previous versions back.</p>
              <button id="mod-updates-rollback" class="btn btn-ghost btn-sm" type="button">Undo last update</button>
            </div>
          </div>
        </section>

        <dialog id="mod-details" class="dialog" aria-labelledby="mod-details-title">
          <button id="mod-details-close" class="icon-btn dialog-close" type="button" aria-label="Close">{_icon("close")}</button>
          <div id="mod-details-body"></div>
        </dialog>
"""

_SECTION_WORLDS = f"""
        <section id="worlds" class="stack">
          <div class="card">
            <div class="card-header">
              <div>
                <h2>Your worlds</h2>
                <p class="card-sub">Every world stays on this device. Switching only changes which one the server loads.</p>
              </div>
              <div class="card-tools">
                <button id="world-refresh-button" class="icon-btn" type="button" title="Refresh" aria-label="Refresh worlds">{_icon("restart")}</button>
                <label class="btn btn-primary btn-sm" for="world-upload-input">{_icon("upload")} Upload world (.zip)</label>
                <input id="world-upload-input" class="visually-hidden" type="file" accept=".zip,application/zip" />
              </div>
            </div>
            <div id="world-list" class="item-list"></div>
            <p class="hint">Upload a zipped world folder: from singleplayer it's in <code>.minecraft/saves/&lt;world&gt;</code>.
              Through the internet link, uploads are limited to about 100 MB; use the LAN address for bigger worlds.</p>
          </div>
          <div class="card">
            <div class="card-header">
              <div>
                <h2>New world</h2>
                <p class="card-sub">Generated the first time the server starts on it.</p>
              </div>
            </div>
            <form id="world-create-form" class="form-grid">
              <div class="field">
                <label for="world-name">Name</label>
                <input id="world-name" maxlength="80" placeholder="e.g. Survival 2" required />
              </div>
              <div class="field">
                <label for="world-seed">Seed (optional)</label>
                <input id="world-seed" placeholder="Random" />
              </div>
              <div class="field field-action">
                <button class="btn btn-primary" type="submit">Create world</button>
              </div>
            </form>
          </div>
        </section>
"""

_SECTION_REMOTE = f"""
        <section id="remote" class="two-col">
          <div class="card">
            <div class="card-header">
              <div>
                <h2>PlayIt tunnel</h2>
                <p class="card-sub">Free tunnel through playit.gg — no router port forwarding needed.</p>
              </div>
              <span id="playit-status-panel" class="badge">PlayIt not configured.</span>
            </div>
            <div class="address-row address-row-lg">
              <span class="address-kind">Address</span>
              <code data-field="playit_address">Not available yet</code>{_copy_button("playit_address", "PlayIt address")}
            </div>
            <div id="playit-claim" class="callout callout-info" hidden>
              <strong>Approve this launcher on playit.gg</strong>
              <p>Open the link on any device, sign in to playit.gg and click <em>Allow</em>. This page updates by itself.</p>
              <div class="address-row">
                <a id="playit-claim-link" class="link" href="#" target="_blank" rel="noopener noreferrer"
                   data-field="playit_claim_url">—</a>{_copy_button("playit_claim_url", "approval link")}
              </div>
            </div>
            <p id="playit-error" class="callout callout-error" hidden></p>
            <ol class="steps">
              <li><strong>Configure</strong> once: approve the link that appears here.</li>
              <li><strong>Start</strong> the tunnel whenever the server is running.</li>
              <li>Share the address above with your friends.</li>
            </ol>
            <details id="playit-json" class="advanced-smtp">
              <summary>Edit config (JSON)</summary>
              <div class="field">
                <label for="playit-json-input">playit_config.json</label>
                <textarea id="playit-json-input" rows="5" spellcheck="false" autocomplete="off"></textarea>
                <p class="hint">Paste a secret key from playit.gg into "secret", or leave it empty to keep the saved key
                  (it is never shown here). Saving cancels a pending approval.</p>
              </div>
              <div class="form-actions">
                <button id="playit-json-reload" class="btn btn-ghost" type="button">Reload</button>
                <button id="playit-json-save" class="btn btn-primary" type="button">Save config</button>
              </div>
            </details>
            <div class="form-actions">
              <button id="playit-config-button" class="btn btn-ghost" type="button">Configure</button>
              <button id="playit-stop-button" class="btn btn-ghost" type="button">Stop tunnel</button>
              <button id="playit-start-button" class="btn btn-primary" type="button">Start tunnel</button>
            </div>
          </div>
          <div class="card">
            <div class="card-header">
              <div>
                <h2>Dashboard over the internet</h2>
                <p class="card-sub">A private https:// link through Cloudflare, free and without an account.</p>
              </div>
              <span id="remote-dashboard-badge" class="badge">Off</span>
            </div>
            <label class="switch-row" for="remote-dashboard-toggle">
              <span><span class="switch-label">Share this dashboard</span>
                <span class="hint">Stays on after reboots while this is enabled.</span></span>
              <input id="remote-dashboard-toggle" type="checkbox" class="switch" />
            </label>
            <div class="address-row address-row-lg">
              <span class="address-kind">Link</span>
              <a id="remote-dashboard-link" class="link" href="#" target="_blank" rel="noopener noreferrer"
                 data-field="remote_dashboard_url">Off</a>{_copy_button("remote_dashboard_url", "dashboard link")}
            </div>
            <p id="remote-dashboard-error" class="callout callout-error" hidden></p>
            <p id="remote-dashboard-weak-password" class="callout callout-error" hidden>
              <strong>Your dashboard password is short.</strong> Anyone with the link can try to guess it. Set
              EZ_WEBAPP_PASSWORD to 12 or more characters and restart the dashboard.
            </p>
            <div class="callout callout-info">
              <strong>Encrypted, with limits.</strong> The link changes every time the tunnel restarts, so
              copy it again after a reboot, and a new link can take up to a minute to open everywhere.
              Anyone with the link can try to sign in; after 5 wrong passwords that address is locked out
              for 15 minutes. Use a long, unique password.
            </div>
            <p class="hint">On a phone, open the link and choose <strong>Install app</strong> (Android) or
              <strong>Add to Home Screen</strong> (iPhone) to get the dashboard as an app. Installing needs this
              https link; it doesn't work on the plain http:// LAN address.</p>
            <details id="named-tunnel" class="advanced-smtp">
              <summary>Use your own domain (same address after every restart)</summary>
              <ol class="plain-list">
                <li>Put a domain on a free Cloudflare account.</li>
                <li>In Cloudflare: Zero Trust → Networks → Tunnels → Create a tunnel (Cloudflared). Copy the token
                  from the install command (the long text starting with <code>eyJ</code>).</li>
                <li>Add a public hostname, e.g. <code>mc.example.com</code>, pointing to
                  <code>http://localhost:8000</code> (the dashboard's port).</li>
                <li>Paste both here and save.</li>
              </ol>
              <form id="named-tunnel-form" class="form-grid">
                <div class="field">
                  <label for="remote-mode">Link type</label>
                  <select id="remote-mode">
                    <option value="quick">Random link (no account)</option>
                    <option value="named">My own domain</option>
                  </select>
                </div>
                <div class="field">
                  <label for="remote-hostname">Public hostname</label>
                  <input id="remote-hostname" placeholder="mc.example.com" autocomplete="off" />
                </div>
                <div class="field field-wide">
                  <label for="remote-token">Tunnel token</label>
                  <input id="remote-token" type="password" autocomplete="off" placeholder="eyJ…" />
                </div>
                <div class="field field-action">
                  <button class="btn btn-primary" type="submit">Save</button>
                </div>
              </form>
            </details>
          </div>
          <div class="card">
            <div class="card-header">
              <div>
                <h2>Direct connection</h2>
                <p class="card-sub">Same Wi-Fi, or internet with the port forwarded on your router.</p>
              </div>
            </div>
            <div class="address-row address-row-lg"><span class="address-kind">LAN</span>
              <code data-field="lan_join">—</code>{_copy_button("lan_join", "LAN address")}</div>
            <div class="address-row address-row-lg"><span class="address-kind">Internet</span>
              <code data-field="public_join">—</code>{_copy_button("public_join", "internet address")}</div>
          </div>
        </section>
"""

_SECTION_BACKUPS = f"""
        <section id="backups" class="stack">
          <div class="card">
            <div class="card-header">
              <div>
                <h2>World backups</h2>
                <p class="card-sub">Restoring moves the current world aside first, so nothing is lost. Stop the server before restoring.</p>
              </div>
              <button id="backup-button" class="btn btn-primary btn-sm" type="button">{_icon("archive")} Backup world</button>
            </div>
            <p id="backup-total" class="hint"></p>
            <div id="backup-list" class="item-list"></div>
          </div>
          <div id="storage" class="card">
            <div class="card-header">
              <div>
                <h2>Storage</h2>
                <p id="storage-free" class="card-sub">Measuring…</p>
              </div>
              <div class="card-tools">
                <button id="storage-refresh-button" class="icon-btn" type="button" title="Measure again" aria-label="Measure storage again">{_icon("restart")}</button>
              </div>
            </div>
            <div id="storage-list" class="item-list"></div>
            <p class="hint">Deleting needs the server stopped. The server folder in use can't be deleted here.</p>
          </div>
          <div class="card">
            <div class="card-header">
              <div>
                <h2>Automatic backups</h2>
                <p class="card-sub">Backs up the world on a schedule while it is being played, and before deleting a
                  world or updating. Old automatic backups are removed so storage doesn't fill up.</p>
              </div>
            </div>
            <form id="backup-schedule-form" class="form-grid">
              <div class="field">
                <label for="backup-interval">Back up every</label>
                <select id="backup-interval">
                  <option value="0">Never (off)</option>
                  <option value="1">Hour</option>
                  <option value="3">3 hours</option>
                  <option value="6">6 hours</option>
                  <option value="12">12 hours</option>
                  <option value="24">Day</option>
                </select>
              </div>
              <div class="field">
                <label for="backup-keep-recent">Keep the latest</label>
                <input id="backup-keep-recent" type="number" min="1" max="100" value="8" />
              </div>
              <div class="field">
                <label for="backup-keep-daily">Plus one per day for (days)</label>
                <input id="backup-keep-daily" type="number" min="0" max="60" value="7" />
              </div>
              <div class="field field-action">
                <button class="btn btn-primary" type="submit">Save</button>
              </div>
            </form>
            {_switch("backup-prune-manual", "Also remove old manual backups", "Off: backups you made with the button are kept until you delete them.", False)}
          </div>
        </section>
"""

_SECTION_CONSOLE_FULL = f"""
        <section id="console" class="card console-card">
          <div class="card-header">
            <h2>Server output</h2>
            <div class="card-tools">
              <select id="console-filter" aria-label="Show">
                <option value="all">Everything</option>
                <option value="chat">Chat only</option>
                <option value="problems">Warnings &amp; errors</option>
              </select>
              <input id="console-search" type="search" placeholder="Search" aria-label="Search the console" />
              <label class="inline-check"><input id="console-autoscroll" type="checkbox" checked /> Auto-scroll</label>
            </div>
          </div>
          <pre id="console-output" class="console console-tall">Waiting for logs...</pre>
          {_COMMAND_FORM}
        </section>

        <section id="admin" class="two-col">
          <div class="card">
            <div class="card-header"><h2>Players</h2></div>
            <div class="player-form">
              <input id="admin-player" placeholder="Player name" aria-label="Player name" />
              <div class="btn-row">
                <button id="admin-op" class="btn btn-ghost btn-sm" type="button">Make operator</button>
                <button id="admin-deop" class="btn btn-ghost btn-sm" type="button">Remove operator</button>
                <button id="admin-kick" class="btn btn-ghost btn-sm" type="button">Kick</button>
                <button id="admin-ban" class="btn btn-danger btn-sm" type="button">Ban</button>
              </div>
            </div>
          </div>
          <div class="card">
            <div class="card-header"><h2>World</h2></div>
            <div class="btn-row">
              <button id="admin-day" class="btn btn-ghost btn-sm" type="button">Set day</button>
              <button id="admin-night" class="btn btn-ghost btn-sm" type="button">Set night</button>
              <button id="admin-clear" class="btn btn-ghost btn-sm" type="button">Clear weather</button>
              <button id="admin-rain" class="btn btn-ghost btn-sm" type="button">Rain</button>
              <button id="admin-save" class="btn btn-ghost btn-sm" type="button">Save world</button>
            </div>
          </div>
        </section>
"""
