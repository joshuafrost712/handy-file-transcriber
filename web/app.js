'use strict';

const $ = (sel) => document.querySelector(sel);
const api = () => window.pywebview.api;

let settings = { default_disposition: 'ask', auto_delete_days: 7, library_path: '' };
let pendingPaths = null;       // paths awaiting a Move/Copy decision
let lastResults = [];          // ids from the most recent batch

// ---- tabs -----------------------------------------------------------
document.querySelectorAll('.tab').forEach((t) => {
  t.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((x) => x.classList.remove('active'));
    document.querySelectorAll('.panel').forEach((x) => x.classList.remove('active'));
    t.classList.add('active');
    $('#tab-' + t.dataset.tab).classList.add('active');
    if (t.dataset.tab === 'library') refreshLibrary();
  });
});

// ---- events from Python --------------------------------------------
window.pyEvent = (type, payload) => {
  if (type === 'dropped_files') handleIncoming(payload.paths);
  else if (type === 'status') showStatus(payload.msg);
  else if (type === 'progress') onProgress(payload);
  else if (type === 'batch_done') onBatchDone(payload);
  else if (type === 'item_error') console.warn('item error', payload);
  else if (type === 'error') showStatus(payload.msg, true);
};

// Run an async UI action and surface any error in the status banner instead
// of failing silently (the bug that hid the broken file picker for so long).
async function guard(fn) {
  try {
    await fn();
  } catch (err) {
    showStatus('Something went wrong: ' + (err && err.message ? err.message : err), true);
    console.error(err);
  }
}

function showStatus(msg, isError) {
  const el = $('#status');
  if (!msg) { el.classList.add('hidden'); return; }
  el.textContent = msg;
  el.style.background = isError ? '#fdecea' : '#eef2ff';
  el.style.color = isError ? '#b3261e' : '#3b6ef0';
  el.classList.remove('hidden');
}

// ---- drag visual feedback ------------------------------------------
const dz = $('#dropzone');
['dragenter', 'dragover'].forEach((e) =>
  document.addEventListener(e, (ev) => { ev.preventDefault(); dz.classList.add('drag'); }));
['dragleave', 'drop'].forEach((e) =>
  document.addEventListener(e, () => dz.classList.remove('drag')));

// ---- adding files ---------------------------------------------------
$('#pickBtn').addEventListener('click', () => guard(async () => {
  const paths = await api().pick_files();
  if (paths && paths.length) handleIncoming(paths);
}));

function handleIncoming(paths) {
  if (!paths || !paths.length) return;
  const disp = settings.default_disposition;
  if (disp === 'move' || disp === 'copy') {
    addPaths(paths, disp);
  } else {
    pendingPaths = paths;
    $('#dispRemember').checked = false;
    $('#dispModal').classList.remove('hidden');
  }
}

$('#dispCopy').addEventListener('click', () => chooseDisposition('copy'));
$('#dispMove').addEventListener('click', () => chooseDisposition('move'));

async function chooseDisposition(disp) {
  $('#dispModal').classList.add('hidden');
  if ($('#dispRemember').checked) {
    settings = await api().set_settings({ default_disposition: disp });
  }
  if (pendingPaths) { addPaths(pendingPaths, disp); pendingPaths = null; }
}

async function addPaths(paths, disp) {
  const r = await api().add_paths(paths, disp);
  renderQueue(r.queue);
}

function renderQueue(names) {
  const card = $('#queueCard');
  const list = $('#queueList');
  if (!names || !names.length) { card.classList.add('hidden'); list.innerHTML = ''; return; }
  list.innerHTML = names.map((n) => `<li><span class="fname">${esc(n)}</span></li>`).join('');
  card.classList.remove('hidden');
  $('#resultsCard').classList.add('hidden');
}

$('#clearQueue').addEventListener('click', async () => {
  await api().clear_queue();
  renderQueue([]);
});

$('#goBtn').addEventListener('click', () => guard(async () => {
  const r = await api().transcribe_queue();
  if (r.started) {
    $('#queueCard').classList.add('hidden');
    $('#progressCard').classList.remove('hidden');
    $('#progressBar').style.width = '0%';
    $('#progressText').textContent = `0 / ${r.count}`;
  }
}));

function onProgress(p) {
  const pct = Math.round(((p.i - 1) / p.n) * 100);
  $('#progressBar').style.width = pct + '%';
  $('#progressText').textContent = `Transcribing ${p.i} / ${p.n}: ${p.name}`;
}

function onBatchDone(payload) {
  $('#progressCard').classList.add('hidden');
  lastResults = payload.results.map((r) => r.id);
  $('#retentionNote').textContent =
    `Audio auto-deletes in ${payload.auto_delete_days} day(s) unless you save it.`;
  const list = $('#resultsList');
  list.innerHTML = payload.results.map((r) => `
    <li>
      <div>
        <div class="fname">${esc(r.name)}</div>
        <div class="meta">${fmtDur(r.duration_sec)}</div>
      </div>
      <label class="toggle">
        <input type="checkbox" class="saveChk" data-id="${r.id}" /> Save audio
      </label>
    </li>`).join('');
  $('#resultsCard').classList.remove('hidden');
  list.querySelectorAll('.saveChk').forEach((chk) =>
    chk.addEventListener('change', (e) =>
      api().set_saved([Number(e.target.dataset.id)], e.target.checked)));
}

$('#saveAll').addEventListener('click', async () => {
  await api().set_saved(lastResults, true);
  document.querySelectorAll('.saveChk').forEach((c) => (c.checked = true));
});
$('#saveNone').addEventListener('click', async () => {
  await api().set_saved(lastResults, false);
  document.querySelectorAll('.saveChk').forEach((c) => (c.checked = false));
});

// ---- library / search ----------------------------------------------
let searchTimer = null;
$('#searchInput').addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(refreshLibrary, 200);
});
$('#weekFilter').addEventListener('change', refreshLibrary);
$('#thisWeek').addEventListener('click', () => selectWeekOffset(0));
$('#lastWeek').addEventListener('click', () => selectWeekOffset(1));
$('#cleanBtn').addEventListener('click', async () => {
  const r = await api().clean_expired();
  showStatus(`Cleaned ${r.count} expired audio file(s).`);
  setTimeout(() => showStatus(''), 2500);
  refreshLibrary();
});

async function loadWeeks() {
  const weeks = await api().weeks();
  const sel = $('#weekFilter');
  sel.innerHTML = '<option value="">All weeks</option>' +
    weeks.map((w) => `<option value="${w.iso_year}-${w.iso_week}">${esc(w.label)} · ${w.count}</option>`).join('');
}

function selectWeekOffset(weeksAgo) {
  const d = new Date();
  d.setDate(d.getDate() - weeksAgo * 7);
  const { year, week } = isoWeek(d);
  $('#weekFilter').value = `${year}-${week}`;
  refreshLibrary();
}

async function refreshLibrary() {
  await loadWeeks();
  const q = $('#searchInput').value.trim();
  const wf = $('#weekFilter').value;
  let iso_year = null, iso_week = null;
  if (wf) { const [y, w] = wf.split('-'); iso_year = Number(y); iso_week = Number(w); }
  const rows = await api().search(q, iso_year, iso_week, null, null);
  renderLibrary(rows);
}

function renderLibrary(rows) {
  const list = $('#libList');
  $('#libEmpty').classList.toggle('hidden', rows.length > 0);
  list.innerHTML = rows.map((r) => {
    const date = (r.transcribed_at || '').slice(0, 10);
    const status = audioStatus(r);
    return `
    <li>
      <div class="rec-top">
        <div class="rec-name" data-md="${r.id}">${esc(r.original_name)}</div>
        <span class="badge ${status.cls}">${status.text}</span>
      </div>
      <div class="rec-sub">${date} · ${r.iso_year}-W${String(r.iso_week).padStart(2, '0')} · ${fmtDur(r.duration_sec)}</div>
      <div class="rec-actions">
        <button class="btn ghost small" data-open="${esc(r.md_path || '')}">Open transcript</button>
        <button class="btn ghost small" data-open="${esc(r.srt_path || '')}">Open SRT</button>
        ${r.media_path ? `<button class="btn ghost small" data-play="${esc(r.media_path)}">Play audio</button>` : ''}
        ${r.media_path ? `<button class="btn ghost small" data-reveal="${esc(r.media_path)}">Reveal</button>` : `<button class="btn ghost small" data-reveal="${esc(r.md_path || '')}">Reveal</button>`}
        ${r.deleted ? '' : `<button class="btn ghost small" data-save="${r.id}" data-keep="${r.retention === 'keep' ? 0 : 1}">${r.retention === 'keep' ? 'Unsave' : 'Save audio'}</button>`}
      </div>
    </li>`;
  }).join('');

  list.querySelectorAll('[data-md]').forEach((el) =>
    el.addEventListener('click', () => openTranscript(Number(el.dataset.md), el.textContent)));
  list.querySelectorAll('[data-open]').forEach((el) =>
    el.addEventListener('click', () => el.dataset.open && api().open_path(el.dataset.open)));
  list.querySelectorAll('[data-play]').forEach((el) =>
    el.addEventListener('click', () => api().open_path(el.dataset.play)));
  list.querySelectorAll('[data-reveal]').forEach((el) =>
    el.addEventListener('click', () => el.dataset.reveal && api().reveal(el.dataset.reveal)));
  list.querySelectorAll('[data-save]').forEach((el) =>
    el.addEventListener('click', async () => {
      await api().set_saved([Number(el.dataset.save)], el.dataset.keep === '1');
      refreshLibrary();
    }));
}

function audioStatus(r) {
  if (r.deleted) return { cls: 'gone', text: 'Audio deleted' };
  if (r.retention === 'keep') return { cls: 'keep', text: 'Saved' };
  if (r.delete_after) {
    const days = Math.ceil((new Date(r.delete_after) - new Date()) / 86400000);
    return { cls: 'auto', text: days <= 0 ? 'Deletes today' : `Deletes in ${days}d` };
  }
  return { cls: 'auto', text: 'Auto-delete' };
}

async function openTranscript(id, title) {
  const text = await api().read_md(id);
  $('#viewerTitle').textContent = title;
  $('#viewerBody').textContent = text || '(empty)';
  $('#viewer').classList.remove('hidden');
}
$('#viewerClose').addEventListener('click', () => $('#viewer').classList.add('hidden'));

// ---- settings -------------------------------------------------------
$('#chooseLib').addEventListener('click', () => guard(async () => {
  settings = await api().choose_library_folder();
  fillSettings();
}));
$('#saveSettings').addEventListener('click', () => guard(async () => {
  settings = await api().set_settings({
    default_disposition: $('#setDisposition').value,
    auto_delete_days: Number($('#setDays').value),
  });
  $('#settingsSaved').textContent = 'Saved.';
  setTimeout(() => ($('#settingsSaved').textContent = ''), 2000);
}));

function fillSettings() {
  $('#setLibrary').value = settings.library_path || '';
  $('#setDisposition').value = settings.default_disposition || 'ask';
  $('#setDays').value = settings.auto_delete_days ?? 7;
}

// ---- helpers --------------------------------------------------------
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function fmtDur(sec) {
  if (!sec && sec !== 0) return '';
  const m = Math.floor(sec / 60), s = Math.round(sec % 60);
  return m ? `${m}m ${s}s` : `${s}s`;
}
function isoWeek(date) {
  const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  const day = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const week = Math.ceil((((d - yearStart) / 86400000) + 1) / 7);
  return { year: d.getUTCFullYear(), week };
}

// ---- boot -----------------------------------------------------------
window.addEventListener('pywebviewready', async () => {
  settings = await api().get_settings();
  fillSettings();
});
