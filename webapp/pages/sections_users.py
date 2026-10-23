"""Page section: friend accounts (FEAT-10), owner only."""
from __future__ import annotations

from webapp.pages.widgets import _icon, _switch

_SECTION_USERS = f"""
        <section id="users" class="stack">
          <div class="card">
            <div class="card-header">
              <div>
                <h2>Friend accounts</h2>
                <p class="card-sub">Friends sign in with their own password and can start the server, see who is online,
                  chat and make a backup. They can't change settings, mods, worlds or anything else, and never see your
                  password.</p>
              </div>
            </div>
            <form id="user-add-form" class="form-grid">
              <div class="field">
                <label for="user-add-name">Friend's username</label>
                <input id="user-add-name" maxlength="32" pattern="[A-Za-z0-9_.\\-]{{2,32}}" autocomplete="off" required
                       placeholder="e.g. alex" />
              </div>
              <div class="field field-action">
                <button class="btn btn-primary" type="submit">Create account</button>
              </div>
            </form>
            <div id="user-new-password" class="callout callout-info" hidden>
              <strong>Send this to your friend</strong> with the dashboard link. It is shown only once.
              <div class="address-row"><code data-field="new_user_credentials"></code>
                <button class="icon-btn" type="button" data-copy-field="new_user_credentials" title="Copy" aria-label="Copy the sign-in details">{_icon("copy")}</button>
              </div>
            </div>
            <div id="user-list" class="item-list"></div>
          </div>
          <div class="card">
            {_switch("friends-can-stop", "Friends can stop and restart the server",
                     "Off: friends can only start it. The daily restart and idle stop still work.", False)}
          </div>
        </section>
"""
