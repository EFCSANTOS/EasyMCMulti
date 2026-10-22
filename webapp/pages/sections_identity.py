"""Page sections: server icon and the All-properties editor (Settings page, FEAT-14)."""
from __future__ import annotations

from webapp.pages.widgets import _icon

# Minecraft formatting codes for the MOTD palette: (code, label, css colour or None for a style).
MOTD_CODES = (
    ("0", "Black", "#000000"), ("1", "Dark blue", "#0000aa"), ("2", "Dark green", "#00aa00"),
    ("3", "Dark aqua", "#00aaaa"), ("4", "Dark red", "#aa0000"), ("5", "Purple", "#aa00aa"),
    ("6", "Gold", "#ffaa00"), ("7", "Gray", "#aaaaaa"), ("8", "Dark gray", "#555555"), ("9", "Blue", "#5555ff"),
    ("a", "Green", "#55ff55"), ("b", "Aqua", "#55ffff"), ("c", "Red", "#ff5555"), ("d", "Pink", "#ff55ff"),
    ("e", "Yellow", "#ffff55"), ("f", "White", "#ffffff"),
)
_SWATCHES = "".join(
    f'<button class="motd-swatch" type="button" data-motd-code="{code}" title="{label}" aria-label="{label}" '
    f'style="background:{color}"></button>'
    for code, label, color in MOTD_CODES
)

# Inserted under the MOTD input on the Settings page.
MOTD_TOOLS = f"""
                <div class="motd-tools" aria-label="MOTD colours and styles">
                  {_SWATCHES}
                  <button class="btn btn-ghost btn-sm" type="button" data-motd-code="l" title="Bold"><b>B</b></button>
                  <button class="btn btn-ghost btn-sm" type="button" data-motd-code="o" title="Italic"><i>I</i></button>
                  <button class="btn btn-ghost btn-sm" type="button" data-motd-code="r" title="Reset style">Reset</button>
                  <button class="btn btn-ghost btn-sm" type="button" data-motd-newline title="Second line">New line</button>
                </div>
                <div class="motd-preview" aria-live="polite">
                  <span class="motd-preview-icon" aria-hidden="true"></span>
                  <div><div class="motd-preview-name">Minecraft Server</div><div id="motd-preview"></div></div>
                </div>
                <p id="motd-length" class="hint"></p>
"""

_SECTION_IDENTITY = f"""
        <section id="identity" class="card">
          <div class="card-header">
            <div>
              <h2>Server icon</h2>
              <p class="card-sub">Shown next to your server in the multiplayer list. Any picture works: it is cropped to a
                square and resized to 64×64. Applies after the next restart.</p>
            </div>
          </div>
          <div class="icon-row">
            <img id="server-icon-preview" class="server-icon" alt="Server icon" hidden />
            <span id="server-icon-empty" class="server-icon server-icon-empty">No icon</span>
            <label class="btn btn-primary btn-sm" for="server-icon-input">{_icon("upload")} Choose picture</label>
            <input id="server-icon-input" class="visually-hidden" type="file" accept="image/*" />
            <button id="server-icon-remove" class="btn btn-ghost btn-sm" type="button" hidden>Remove</button>
          </div>
        </section>
"""

_SECTION_ALL_PROPERTIES = """
        <section id="all-properties" class="card">
          <div class="card-header">
            <div>
              <h2>All properties</h2>
              <p class="card-sub">Every setting in server.properties. Greyed-out ones are set with the form above or on the
                Server page. Applies after the next restart.</p>
            </div>
          </div>
          <input id="properties-filter" type="search" placeholder="Filter, e.g. spawn" aria-label="Filter properties" />
          <div id="properties-list" class="properties-list"></div>
          <div class="form-actions">
            <button id="properties-save-button" class="btn btn-primary" type="button">Save properties</button>
          </div>
        </section>
"""
