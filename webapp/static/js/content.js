// EZ Minecraft dashboard, part 3 of 7: Backups, mods (Modrinth browser) and worlds.
// Plain scripts that share one global scope, loaded in order by webapp/pages/layout.py::SCRIPTS.

// ---------- Backups ----------
function describeBackup(path) {
  const name = path.split(/[\\/]/).pop();
  const match = name.match(/(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/);
  const date = match ? new Date(match[1], match[2] - 1, match[3], match[4], match[5], match[6]) : null;
  return { name, when: date ? date.toLocaleString() : name };
}

async function loadBackups() {
  const list = document.getElementById('backup-list');
  if (!list) return;
  try {
    const result = await fetchJson('/api/server/backups');
    renderItems(list, result.backups || [], (path) => {
      const { name, when } = describeBackup(path);
      return itemRow(when, null, name, rowButton('Restore', 'btn-ghost', (button) => restoreBackup(path, when, button)));
    }, 'No backups yet. Create one before making big changes.');
  } catch (error) {
    setStatus(`Backup list unavailable: ${error.message}`, 'error');
  }
}

async function backupServer(button) {
  await runAction(button, 'Creating backup…', async () => {
    const response = await fetchJson('/api/server/backup', 'POST');
    await loadBackups();
    if (response.ok === false) throw new Error(response.message);
    return response.message || 'Backup created.';
  });
}

async function restoreBackup(path, when, button) {
  if (!window.confirm(`Restore the backup from ${when}?\n\nThe current world is moved aside first, so it is not lost.`)) return;
  await runAction(button, 'Restoring backup…', async () => {
    const response = await fetchJson('/api/server/backups/restore', 'POST', { backup_path: path });
    await loadBackups();
    return response.message || 'Backup restored.';
  });
}

// ---------- Mods ----------
const modState = { offset: 0, total: 0, installedIds: new Set(), loading: false };

async function loadInstalledMods() {
  const list = document.getElementById('mod-installed-list');
  if (!list) return;
  try {
    const result = await fetchJson('/api/server/mods');
    modState.installedIds = new Set(result.project_ids || []);
    renderItems(list, result.mods || [], (mod) => itemRow(
      mod.title || mod.name,
      mod.title ? mod.name : null,
      `${mod.folder}/${mod.version ? ` · ${mod.version}` : ''}${mod.project_id ? ' · from Modrinth' : ' · uploaded'}`,
      rowButton('Remove', 'btn-danger', (button) => removeMod(mod, button)),
    ), 'Nothing installed yet.');
    refreshInstalledMarks();
  } catch (error) {
    setStatus(`Installed list unavailable: ${error.message}`, 'error');
  }
}

async function removeMod(mod, button) {
  if (!window.confirm(`Remove ${mod.title || mod.name}?`)) return;
  await runAction(button, null, async () => {
    const response = await fetchJson(`/api/server/mods/${encodeURIComponent(mod.name)}`, 'DELETE');
    await loadInstalledMods();
    return `${response.message} Restart the server to apply.`;
  });
}

function modIcon(hit, size) {
  if (hit.icon_url) {
    const img = el('img', 'mod-icon');
    img.src = hit.icon_url;
    img.alt = '';
    img.loading = 'lazy';
    img.width = size;
    img.height = size;
    return img;
  }
  return el('div', 'mod-icon mod-icon-empty', (hit.title || '?').charAt(0).toUpperCase());
}

function installButton(hit, extraClass = '') {
  const installed = modState.installedIds.has(hit.id);
  const button = el('button', `btn btn-sm ${installed ? 'btn-ghost' : 'btn-primary'} ${extraClass}`, installed ? 'Installed' : 'Install');
  button.type = 'button';
  button.dataset.projectId = hit.id || '';
  button.disabled = installed;
  button.addEventListener('click', (event) => {
    event.stopPropagation();
    installMod(hit, button);
  });
  return button;
}

function refreshInstalledMarks() {
  document.querySelectorAll('button[data-project-id]').forEach((button) => {
    if (modState.installedIds.has(button.dataset.projectId)) {
      button.textContent = 'Installed';
      button.disabled = true;
      button.className = button.className.replace('btn-primary', 'btn-ghost');
    }
  });
}

function modCard(hit) {
  const card = el('article', 'mod-card');
  card.tabIndex = 0;
  card.setAttribute('role', 'button');
  card.setAttribute('aria-label', `${hit.title}: show details`);
  const head = el('div', 'mod-card-head');
  const titles = el('div', 'mod-titles');
  titles.append(el('div', 'mod-title', hit.title), el('div', 'mod-author', hit.author ? `by ${hit.author}` : ''));
  head.append(modIcon(hit, 48), titles);
  const meta = el('div', 'mod-meta',
    `${compactNumber(hit.downloads)} downloads · ${compactNumber(hit.follows)} followers · updated ${relativeTime(hit.updated)}`);
  const chips = el('div', 'chips');
  (hit.categories || []).slice(0, 3).forEach((name) => chips.appendChild(el('span', 'chip', capitalize(name))));
  const footer = el('div', 'mod-card-foot');
  footer.append(chips, installButton(hit));
  card.append(head, el('p', 'mod-desc', hit.description), meta, footer);
  const open = () => openModDetails(hit);
  card.addEventListener('click', open);
  card.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      open();
    }
  });
  return card;
}

async function browseMods(reset = true) {
  const grid = document.getElementById('mod-search-results');
  if (!grid || modState.loading) return;
  if (reset) {
    modState.offset = 0;
    grid.replaceChildren(el('p', 'empty', 'Loading…'));
  }
  const params = new URLSearchParams({
    query: document.getElementById('mod-search-query').value.trim(),
    sort: document.getElementById('mod-sort').value,
    category: document.getElementById('mod-category').value,
    offset: String(modState.offset),
  });
  const loadMore = document.getElementById('mod-load-more');
  modState.loading = true;
  loadMore.disabled = true;
  try {
    const result = await fetchJson(`/api/modrinth/search?${params}`);
    if (reset) grid.replaceChildren();
    document.getElementById('mod-context').textContent =
      `Mods and plugins that run on your server (${capitalize(result.loader)} ${result.version}). Client-only mods are hidden.`;
    (result.hits || []).forEach((hit) => grid.appendChild(modCard(hit)));
    modState.offset += (result.hits || []).length;
    modState.total = result.total || 0;
    if (!grid.children.length) grid.appendChild(el('p', 'empty', 'No compatible results. Try another search or category.'));
    document.getElementById('mod-result-count').textContent = modState.total
      ? `Showing ${modState.offset} of ${modState.total.toLocaleString()}` : '';
    loadMore.hidden = modState.offset >= modState.total;
  } catch (error) {
    grid.replaceChildren(el('p', 'empty', error.message));
    loadMore.hidden = true;
  } finally {
    modState.loading = false;
    loadMore.disabled = false;
  }
}

async function loadModCategories() {
  const select = document.getElementById('mod-category');
  if (!select) return;
  try {
    const result = await fetchJson('/api/modrinth/categories');
    (result.categories || []).forEach((name) => {
      const option = el('option', '', capitalize(name));
      option.value = name;
      select.appendChild(option);
    });
  } catch (error) {
    // the filter just stays at "All categories"
  }
}

function detailLink(label, href) {
  const link = el('a', 'link', label);
  link.href = href;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  return link;
}

async function openModDetails(hit) {
  const dialog = document.getElementById('mod-details');
  const body = document.getElementById('mod-details-body');
  const head = el('div', 'mod-card-head');
  const titles = el('div', 'mod-titles');
  const title = el('h2', 'mod-title', hit.title);
  title.id = 'mod-details-title';
  titles.append(title, el('div', 'mod-author', hit.author ? `by ${hit.author}` : ''));
  head.append(modIcon(hit, 64), titles, installButton(hit, 'details-install'));
  const content = el('div', 'mod-details-content', 'Loading details…');
  body.replaceChildren(head, el('p', 'mod-desc-full', hit.description), content);
  dialog.showModal();
  try {
    const details = await fetchJson(`/api/modrinth/project/${encodeURIComponent(hit.slug)}`);
    if (!hit.icon_url && details.icon_url) head.replaceChild(modIcon(details, 64), head.firstChild);
    const stats = el('div', 'mod-meta',
      `${compactNumber(details.downloads)} downloads · ${compactNumber(details.follows)} followers · updated ${relativeTime(details.updated)}`
      + `${details.license ? ` · ${details.license}` : ''}`);
    const compat = el('p', details.compatible_versions ? 'hint' : 'callout callout-error',
      details.compatible_versions
        ? `${details.compatible_versions} version(s) for your server; Install picks ${details.latest_version}. Required dependencies are installed too.`
        : 'No version of this project matches your loader and Minecraft version.');
    const chips = el('div', 'chips');
    (details.categories || []).forEach((name) => chips.appendChild(el('span', 'chip', capitalize(name))));
    const gallery = el('div', 'mod-gallery');
    (details.gallery || []).forEach((image) => {
      const img = el('img');
      img.src = image.url;
      img.alt = image.title || '';
      img.loading = 'lazy';
      gallery.appendChild(img);
    });
    const links = el('div', 'mod-links');
    links.appendChild(detailLink('Modrinth page', details.page_url));
    const names = { source_url: 'Source', wiki_url: 'Wiki', discord_url: 'Discord', issues_url: 'Issues' };
    Object.entries(details.links || {}).forEach(([key, href]) => links.appendChild(detailLink(names[key] || key, href)));
    const description = el('div', 'mod-body', details.body || '');
    content.replaceChildren(stats, chips, compat, ...(gallery.children.length ? [gallery] : []), description, links);
  } catch (error) {
    content.replaceChildren(el('p', 'callout callout-error', error.message));
  }
}

async function installMod(hit, button) {
  await runAction(button, `Installing ${hit.title}…`, async () => {
    const response = await fetchJson('/api/modrinth/install', 'POST', { slug: hit.slug });
    await loadInstalledMods();
    return `${response.message || 'Installed.'} Restart the server to load it.`;
  });
}

function initModBrowser() {
  if (!document.getElementById('mod-search-results')) return;
  const dialog = document.getElementById('mod-details');
  document.getElementById('mod-details-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) dialog.close(); // click on the backdrop
  });
  on('mod-category', 'change', () => browseMods(true));
  on('mod-sort', 'change', () => browseMods(true));
  on('mod-load-more', 'click', () => browseMods(false));
  loadModCategories();
  browseMods(true);
}

// ---------- Worlds ----------
function formatSize(bytes) {
  return bytes ? formatBytes(bytes) : '';
}

function worldTitle(world) {
  const title = el('div', 'item-title');
  title.appendChild(document.createTextNode(world.name));
  if (world.active) title.appendChild(el('span', 'badge badge-live world-badge', 'Active'));
  return title;
}

function worldMeta(world) {
  const parts = [`folder ${world.folder}`];
  if (!world.exists) parts.push(world.seed ? `not generated yet · seed ${world.seed}` : 'not generated yet');
  if (world.size_bytes) parts.push(formatSize(world.size_bytes));
  if (world.last_played) parts.push(`played ${relativeTime(world.last_played)}`);
  if (world.source === 'upload') parts.push('uploaded');
  return parts.join(' · ');
}

function renderWorlds(result) {
  const list = document.getElementById('world-list');
  renderItems(list, result.worlds || [], (world) => {
    const buttons = [];
    if (!world.active) {
      buttons.push(rowButton(result.server_running ? 'Switch & restart' : 'Switch to', 'btn-primary', (b) => activateWorld(world, result.server_running, b)));
    }
    buttons.push(rowButton('Rename', 'btn-ghost', (b) => renameWorld(world, b)));
    if (world.exists) buttons.push(rowButton('Download', 'btn-ghost', (b) => downloadWorld(world, b)));
    if (!world.active) buttons.push(rowButton('Delete', 'btn-danger', (b) => deleteWorld(world, b)));
    return itemRow(worldTitle(world), null, worldMeta(world), buttons);
  }, 'No worlds yet. Start the server once, upload a world, or create a new one.');
}

async function loadWorlds() {
  if (!document.getElementById('world-list')) return;
  try {
    renderWorlds(await fetchJson('/api/worlds'));
  } catch (error) {
    setStatus(`Worlds unavailable: ${error.message}`, 'error');
  }
}

async function activateWorld(world, running, button) {
  if (running && !window.confirm(`Switch to "${world.name}"? The server restarts and players are disconnected briefly.`)) return;
  await runAction(button, running ? 'Switching world and restarting…' : null, async () => {
    const result = await fetchJson(`/api/worlds/${encodeURIComponent(world.folder)}/activate`, 'POST');
    renderWorlds(result);
    if (running) trackOperation({ operation: 'switching world' });
    else await loadStatus();
    return result.message;
  });
}

async function renameWorld(world, button) {
  const name = window.prompt('New name for this world:', world.name);
  if (!name || name.trim() === world.name) return;
  await runAction(button, null, async () => {
    const result = await fetchJson(`/api/worlds/${encodeURIComponent(world.folder)}/rename`, 'POST', { name: name.trim() });
    renderWorlds(result);
    return result.message;
  });
}

async function deleteWorld(world, button) {
  const typed = window.prompt(`Delete "${world.name}" permanently? Download it first if you want a copy.\n\nType the world name to confirm:`);
  if (typed === null) return;
  if (typed.trim() !== world.name) {
    setStatus('The name did not match; nothing was deleted.', 'error');
    return;
  }
  await runAction(button, null, async () => {
    const result = await fetchJson(`/api/worlds/${encodeURIComponent(world.folder)}`, 'DELETE');
    renderWorlds(result);
    return result.message;
  });
}

async function downloadWorld(world, button) {
  await runAction(button, `Preparing ${world.name}.zip…`, async () => {
    const response = await fetch(`/api/worlds/${encodeURIComponent(world.folder)}/download`, {
      credentials: 'same-origin',
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || `HTTP ${response.status}`);
    const url = URL.createObjectURL(await response.blob());
    const link = el('a');
    link.href = url;
    link.download = `${world.folder}.zip`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    return `Downloaded ${world.name}.`;
  });
}

function uploadWorld(input) {
  const file = input.files[0];
  if (!file) return;
  // XMLHttpRequest instead of fetch: it reports upload progress for big worlds.
  const form = new FormData();
  form.append('file', file);
  form.append('name', file.name.replace(/\.zip$/i, ''));
  const request = new XMLHttpRequest();
  request.open('POST', '/api/worlds/upload');
  request.upload.onprogress = (event) => {
    if (event.lengthComputable) {
      const percent = Math.round((event.loaded / event.total) * 100);
      setStatus(percent < 100 ? `Uploading ${file.name}: ${percent}%` : `Importing ${file.name}…`, 'pending');
    }
  };
  request.onload = () => {
    let data = {};
    try {
      data = JSON.parse(request.responseText);
    } catch (error) {
      data = { detail: request.status === 413 ? 'The file is too big for this connection.' : request.responseText };
    }
    if (request.status >= 200 && request.status < 300) {
      renderWorlds(data);
      setStatus(`${data.message} Use "Switch to" to play it.`);
    } else {
      setStatus(`Upload failed: ${data.detail || `HTTP ${request.status}`}`, 'error');
    }
  };
  request.onerror = () => setStatus('Upload failed: connection lost.', 'error');
  setStatus(`Uploading ${file.name}…`, 'pending');
  request.send(form);
  input.value = '';
}

async function createWorld(event) {
  event.preventDefault();
  const name = document.getElementById('world-name');
  const seed = document.getElementById('world-seed');
  await runAction(event.submitter, null, async () => {
    const result = await fetchJson('/api/worlds', 'POST', { name: name.value.trim(), seed: seed.value.trim() });
    renderWorlds(result);
    name.value = '';
    seed.value = '';
    return result.message;
  });
}

async function uploadMods(input) {
  if (!input.files.length) return;
  const formData = new FormData();
  Array.from(input.files).forEach((file) => formData.append('files', file));
  await runAction(null, `Uploading ${input.files.length} file(s)…`, async () => {
    const response = await fetchJson('/api/server/mods/upload', 'POST', formData);
    await loadInstalledMods();
    return `${response.message || 'Uploaded.'} Restart the server to load them.`;
  });
  input.value = '';
}
