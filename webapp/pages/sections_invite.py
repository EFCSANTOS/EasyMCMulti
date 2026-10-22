"""Page section: Invite friends (copyable invite + public status page switch), on Overview and Remote."""
from __future__ import annotations

from webapp.pages.widgets import _copy_button, _icon, _switch

_SECTION_INVITE = f"""
        <section id="invite" class="card">
          <div class="card-header">
            <div>
              <h2>Invite friends</h2>
              <p class="card-sub">A ready-to-paste message with the address and version for your group chat.</p>
            </div>
            <div class="card-tools">
              <button id="invite-copy-button" class="btn btn-primary btn-sm" type="button">{_icon("copy")} Copy invite</button>
            </div>
          </div>
          <pre id="invite-text" class="invite-text">Loading…</pre>
          {_switch("status-page-enabled", "Public status page",
                   "A page friends can open without signing in: online or not, players, address and version. "
                   "It shows your PlayIt address, or your public IP if you don't use PlayIt.", False)}
          <div id="status-page-options" class="stack-sm" hidden>
            <div class="address-row">
              <span class="address-kind">Link</span>
              <a id="status-page-link" class="link" href="#" target="_blank" rel="noopener noreferrer"
                 data-field="status_page_url">—</a>{_copy_button("status_page_url", "status page link")}
            </div>
            <p id="status-page-lan-note" class="hint" hidden>This link only works on your Wi-Fi. Turn on
              "Share this dashboard" on the Remote access page to get a link that works anywhere.</p>
            {_switch("status-page-show-names", "Show player names", "Who is online right now.", True)}
            {_switch("status-page-show-lan", "Show the same-Wi-Fi address", "For friends at your place.", False)}
            <div class="form-actions">
              <button id="status-page-regenerate" class="btn btn-ghost btn-sm" type="button">New link</button>
            </div>
          </div>
        </section>
"""
