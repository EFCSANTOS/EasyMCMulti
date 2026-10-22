"""Page sections: crash diagnosis (Overview and Console) and the log viewer (Console)."""
from __future__ import annotations

from webapp.pages.widgets import _icon

_SECTION_DIAGNOSIS = """
        <section id="diagnosis" class="card callout-card" hidden>
          <div class="card-header">
            <div>
              <h2>Why the server stopped</h2>
              <p class="card-sub">Found in the last server output. Fix it, then press Start.</p>
            </div>
          </div>
          <div id="diagnosis-list" class="item-list"></div>
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
