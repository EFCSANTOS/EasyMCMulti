"""Page section: Players (whitelist, operators, bans, recently seen)."""
from __future__ import annotations

from webapp.pages.widgets import _icon, _switch

# Same rule as webapp/validation.py::require_player (optional "." for Bedrock players).
_NAME_PATTERN = r"[.]?[A-Za-z0-9_]{3,16}"

_SECTION_PLAYERS = f"""
        <section id="players" class="stack">
          <div class="card">
            <div class="card-header">
              <div>
                <h2>Whitelist</h2>
                <p class="card-sub">Only these players can join when the whitelist is on. Works with the server on or off.</p>
              </div>
              <div class="card-tools">
                <button id="players-refresh-button" class="icon-btn" type="button" title="Refresh" aria-label="Refresh player lists">{_icon("restart")}</button>
              </div>
            </div>
            {_switch("players-whitelist-enabled", "Only listed players can join", "Turn this on so only your friends can get in.", False)}
            <p id="players-whitelist-off" class="callout callout-warning" hidden>
              <strong>The whitelist is off.</strong> Anyone who has your server address can join.
            </p>
            <form id="whitelist-add-form" class="form-grid">
              <div class="field">
                <label for="whitelist-name">Player name</label>
                <input id="whitelist-name" maxlength="17" pattern="{_NAME_PATTERN}" title="3 to 16 letters, digits or _" placeholder="e.g. Steve" autocomplete="off" required />
              </div>
              <div class="field field-action">
                <button class="btn btn-primary" type="submit">Add to whitelist</button>
              </div>
            </form>
            <div id="whitelist-list" class="item-list"></div>
          </div>
          <div class="card">
            <div class="card-header">
              <div>
                <h2>Recently seen</h2>
                <p class="card-sub">Players who joined, or tried to and were refused by the whitelist, since the dashboard started.</p>
              </div>
            </div>
            <div id="recent-players-list" class="item-list"></div>
          </div>
          <div class="card">
            <div class="card-header">
              <div>
                <h2>Operators</h2>
                <p class="card-sub">Operators can use admin commands in the game. Level 4 can do everything.</p>
              </div>
            </div>
            <form id="ops-add-form" class="form-grid">
              <div class="field">
                <label for="ops-name">Player name</label>
                <input id="ops-name" maxlength="17" pattern="{_NAME_PATTERN}" title="3 to 16 letters, digits or _" placeholder="e.g. Alex" autocomplete="off" required />
              </div>
              <div class="field">
                <label for="ops-level">Level</label>
                <select id="ops-level">
                  <option value="4">4 · everything</option>
                  <option value="3">3 · moderation</option>
                  <option value="2">2 · cheats</option>
                  <option value="1">1 · spawn protection</option>
                </select>
              </div>
              <div class="field field-action">
                <button class="btn btn-primary" type="submit">Make operator</button>
              </div>
            </form>
            <div id="ops-list" class="item-list"></div>
          </div>
          <div class="card">
            <div class="card-header">
              <div>
                <h2>Banned</h2>
                <p class="card-sub">Ban players from the Console page or in game; unban them here.</p>
              </div>
            </div>
            <div id="bans-list" class="item-list"></div>
          </div>
        </section>
"""
