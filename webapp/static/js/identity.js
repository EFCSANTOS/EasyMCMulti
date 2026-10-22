// EZ Minecraft dashboard, part 7 of 8: Server icon, MOTD editor and the All-properties table (Settings page).
// Plain scripts that share one global scope, loaded in order by webapp/pages/layout.py::SCRIPTS.

// ---------- MOTD editor ----------
const MOTD_COLORS = {
  0: '#000000', 1: '#0000aa', 2: '#00aa00', 3: '#00aaaa', 4: '#aa0000', 5: '#aa00aa', 6: '#ffaa00', 7: '#aaaaaa',
  8: '#555555', 9: '#5555ff', a: '#55ff55', b: '#55ffff', c: '#ff5555', d: '#ff55ff', e: '#ffff55', f: '#ffffff',
};

// Renders §-codes like the multiplayer list does; "\n" starts the second line.
function renderMotdPreview() {
  const input = document.getElementById('motd-input');
  const preview = document.getElementById('motd-preview');
  if (!input || !preview) return;
  const fragment = document.createDocumentFragment();
  let style = { color: MOTD_COLORS[7], bold: false, italic: false };
  input.value.split('\\n').slice(0, 2).forEach((line, index) => {
    if (index) fragment.appendChild(document.createTextNode('\n'));
    line.split('§').forEach((part, partIndex) => {
      let text = part;
      if (partIndex > 0 && part.length) {
        const code = part[0].toLowerCase();
        text = part.slice(1);
        if (MOTD_COLORS[code]) style = { color: MOTD_COLORS[code], bold: false, italic: false };
        else if (code === 'l') style = { ...style, bold: true };
        else if (code === 'o') style = { ...style, italic: true };
        else if (code === 'r') style = { color: MOTD_COLORS[7], bold: false, italic: false };
      }
      if (!text) return;
      const span = el('span', null, text);
      span.style.color = style.color;
      if (style.bold) span.style.fontWeight = '700';
      if (style.italic) span.style.fontStyle = 'italic';
      fragment.appendChild(span);
    });
  });
  preview.replaceChildren(fragment);
  const visible = input.value.replace(/§./g, '').split('\\n').map((line) => line.length);
  document.getElementById('motd-length').textContent =
    `${visible.join(' + ')} visible character(s); about 45 fit on one line.`;
}

function insertMotdText(text) {
  const input = document.getElementById('motd-input');
  const start = input.selectionStart ?? input.value.length;
  const end = input.selectionEnd ?? input.value.length;
  input.value = input.value.slice(0, start) + text + input.value.slice(end);
  input.focus();
  input.setSelectionRange(start + text.length, start + text.length);
  renderMotdPreview();
}

// ---------- Server icon ----------
function loadServerIcon() {
  const preview = document.getElementById('server-icon-preview');
  if (!preview) return;
  preview.onload = () => {
    preview.hidden = false;
    document.getElementById('server-icon-empty').hidden = true;
    document.getElementById('server-icon-remove').hidden = false;
  };
  preview.onerror = () => {
    preview.hidden = true;
    document.getElementById('server-icon-empty').hidden = false;
    document.getElementById('server-icon-remove').hidden = true;
  };
  preview.src = `/api/server/icon?t=${Date.now()}`;
}

// Crop the picture to a centred square and scale it to 64×64 in the browser (no server-side imaging).
function iconBlob(file) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => {
      const side = Math.min(image.naturalWidth, image.naturalHeight);
      const canvas = el('canvas');
      canvas.width = 64;
      canvas.height = 64;
      const context = canvas.getContext('2d');
      context.imageSmoothingQuality = 'high';
      context.drawImage(image, (image.naturalWidth - side) / 2, (image.naturalHeight - side) / 2, side, side, 0, 0, 64, 64);
      URL.revokeObjectURL(image.src);
      canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('Could not convert the picture.'))), 'image/png');
    };
    image.onerror = () => reject(new Error('That file is not a picture the browser can open.'));
    image.src = URL.createObjectURL(file);
  });
}

async function uploadServerIcon(input) {
  const file = input.files[0];
  if (!file) return;
  await runAction(null, 'Saving icon…', async () => {
    const form = new FormData();
    form.append('file', await iconBlob(file), 'server-icon.png');
    const response = await fetchJson('/api/server/icon', 'POST', form);
    loadServerIcon();
    return response.message;
  });
  input.value = '';
}

async function removeServerIcon(button) {
  await runAction(button, null, async () => {
    const response = await fetchJson('/api/server/icon', 'DELETE');
    loadServerIcon();
    return response.message;
  });
}

// ---------- All properties ----------
let propertyRows = [];

function propertyInput(row) {
  let input;
  if (row.type === 'bool') {
    input = el('input', 'switch');
    input.type = 'checkbox';
    input.checked = row.value === 'true';
  } else if (row.type === 'enum') {
    input = el('select');
    (row.options || []).forEach((option) => input.appendChild(el('option', null, option)).setAttribute('value', option));
    input.value = row.value;
  } else {
    input = el('input');
    input.type = row.type === 'int' ? 'number' : 'text';
    input.value = row.value;
  }
  input.dataset.key = row.key;
  input.disabled = row.managed;
  input.setAttribute('aria-label', row.key);
  return input;
}

function renderProperties(data) {
  const list = document.getElementById('properties-list');
  if (!list) return;
  propertyRows = data.properties || [];
  list.replaceChildren();
  propertyRows.forEach((row) => {
    const item = el('div', 'property-row');
    item.dataset.key = row.key;
    item.append(el('code', null, row.key), propertyInput(row));
    const hint = row.managed ? `${row.description} Set on the Settings or Server page.`.trim() : row.description;
    if (hint) item.appendChild(el('p', 'hint', hint));
    list.appendChild(item);
  });
  filterProperties();
}

function filterProperties() {
  const filter = document.getElementById('properties-filter');
  const term = filter ? filter.value.trim().toLowerCase() : '';
  document.querySelectorAll('.property-row').forEach((row) => {
    row.hidden = Boolean(term) && !row.textContent.toLowerCase().includes(term);
  });
}

async function loadProperties() {
  if (!document.getElementById('properties-list')) return;
  try {
    renderProperties(await fetchJson('/api/server/properties'));
  } catch (error) {
    setStatus(`Properties unavailable: ${error.message}`, 'error');
  }
}

async function saveProperties(button) {
  const changes = {};
  document.querySelectorAll('#properties-list [data-key]:not(.property-row)').forEach((input) => {
    const row = propertyRows.find((item) => item.key === input.dataset.key);
    if (!row || row.managed) return;
    const value = input.type === 'checkbox' ? String(input.checked) : input.value.trim();
    if (value !== row.value) changes[row.key] = value === '' && row.custom ? null : value;
  });
  if (!Object.keys(changes).length) {
    setStatus('Nothing changed.');
    return;
  }
  await runAction(button, 'Saving…', async () => {
    const data = await fetchJson('/api/server/properties', 'PUT', changes);
    renderProperties(data);
    return data.message;
  });
}

function bindIdentity() {
  on('motd-input', 'input', () => renderMotdPreview());
  document.querySelectorAll('[data-motd-code]').forEach((button) => {
    button.addEventListener('click', () => insertMotdText(`§${button.dataset.motdCode}`));
  });
  document.querySelectorAll('[data-motd-newline]').forEach((button) => {
    button.addEventListener('click', () => insertMotdText('\\n'));
  });
  on('server-icon-input', 'change', (event, input) => uploadServerIcon(input));
  on('server-icon-remove', 'click', (event, button) => removeServerIcon(button));
  on('properties-filter', 'input', () => filterProperties());
  on('properties-save-button', 'click', (event, button) => saveProperties(button));
}
