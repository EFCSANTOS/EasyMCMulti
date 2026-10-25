"""Page sections: crash diagnosis (Overview and Console) and the log viewer (Console)."""
from __future__ import annotations

from webapp.pages.widgets import _icon

_SECTION_DIAGNOSIS = """
        <section id="diagnosis" class="card callout-card" data-owner-only hidden>
          <div class="card-header">
            <div>
              <h2>Why the server stopped</h2>
              <p class="card-sub">Found in the last server output. Fix it, then press Start.</p>
            </div>
          </div>
          <div id="diagnosis-list" class="item-list"></div>
        </section>
"""

# Phones: the device fell into deep sleep and the server was unreachable (webapp/routes/power.py).
_SECTION_SLEEP = """
        <section id="sleep-warning" class="card callout-card" data-owner-only hidden>
          <div class="card-header">
            <div>
              <h2 id="sleep-title">The phone fell asleep</h2>
              <p id="sleep-detail" class="card-sub"></p>
            </div>
            <div class="card-tools">
              <button id="sleep-dismiss-button" class="btn btn-ghost btn-sm" type="button">Dismiss</button>
            </div>
          </div>
          <div id="sleep-phone-steps">
            <p>Android paused Termux to save battery, so the server, the tunnel and this dashboard stopped answering
              until the screen came on. Fix it once on the phone:</p>
            <ol class="steps">
              <li>Settings → Apps → Termux → Battery → <strong>Unrestricted</strong> (or "Don't optimize").</li>
              <li>Xiaomi, Samsung, Huawei, OnePlus…: also allow <strong>Autostart</strong> and turn off "Put unused
                apps to sleep" / "Deep sleeping apps" for Termux.</li>
              <li>Keep the phone on the charger, and leave Termux's notification alone ("Release wakelock" lets it sleep).</li>
            </ol>
            <button id="sleep-battery-button" class="btn btn-primary btn-sm" type="button">Open the setting on the phone</button>
          </div>
        </section>
"""

_SECTION_LOGS = f"""
        <section id="logs" class="card">
          <div class="card-header">
            <div>
              <h2>Log files</h2>
              <p class="card-sub">The server's own logs and crash reports, newest first.</p>
            </div>
            <div class="card-tools">
              <button id="logs-refresh-button" class="icon-btn" type="button" title="Refresh" aria-label="Refresh log files">{_icon("restart")}</button>
            </div>
          </div>
          <div id="log-list" class="item-list"></div>
          <p id="log-view-title" class="hint" hidden></p>
          <pre id="log-view" class="console console-tall" hidden></pre>
        </section>
"""
