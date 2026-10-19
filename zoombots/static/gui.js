// Interface multi-utilisateurs : une session par onglet. Tout contenu dynamique passe par textContent
// (jamais innerHTML). Fermer l'onglet ferme la session côté serveur, donc retire les bots.
const $ = id => document.getElementById(id);
const enc = encodeURIComponent;
const STORE = 'zoombots.sid';

const S = { sid: null, config: null, snap: { bots: [], sounds: [], library: [], jobs: [] },
            selected: new Set(), open: null, uploads: [], ws: null, ping: null, shotUrl: null, dragging: false };

// -- utilitaires -----------------------------------------------------------

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === 'on') for (const [ev, fn] of Object.entries(v)) node.addEventListener(ev, fn);
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else node[k] = v;
  }
  node.append(...children.filter(c => c !== null && c !== undefined && c !== false));
  return node;
}

let toastTimer;
function toast(message, bad = false) {
  const t = $('toast');
  t.textContent = message;
  t.className = 'toast' + (bad ? ' bad' : '');
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, bad ? 6000 : 3000);
}

function errorText(data, status) {
  const d = data && data.detail;
  if (Array.isArray(d)) return d.map(e => e.msg).join(' · ');
  return d || `Erreur ${status}`;
}

async function api(path, method = 'GET', body, raw) {
  const headers = { 'X-Zoombots': '1' };
  if (S.sid) headers['X-Session'] = S.sid;
  let payload = body;
  if (body !== undefined && !(body instanceof Blob)) { headers['content-type'] = 'application/json'; payload = JSON.stringify(body); }
  let r;
  try { r = await fetch(path, { method, headers, body: payload }); }
  catch (e) { throw new Error('serveur injoignable'); }
  if (raw && r.ok) return r;
  const data = await r.json().catch(() => null);
  if (r.status === 401 && S.sid && !path.endsWith('/session')) { await newSession('Session expirée, nouvelle session démarrée.'); throw new Error('session expirée'); }
  if (!r.ok) throw new Error(errorText(data, r.status));
  return data;
}

// Désactive un bouton le temps d'une requête (un double clic ne déploie pas deux fois).
async function busy(button, fn) {
  if (button.disabled) return null;
  button.disabled = true;
  try { return await fn(); } finally { button.disabled = false; }
}

// Exécute une action utilisateur en affichant l'erreur éventuelle.
async function run(fn, okMessage) {
  try { const r = await fn(); if (okMessage) toast(okMessage); return r; }
  catch (e) { toast(e.message, true); return null; }
}

const fmtDuration = s => s >= 60 ? `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}` : `${Math.round(s)} s`;
const activeBots = () => S.snap.bots.filter(b => !['ended', 'denied', 'error', 'left'].includes(b.status));

// -- session et connexion --------------------------------------------------

let pendingSession = null;
// Un seul démarrage de session à la fois : le 401 d'une requête et la fermeture 4401 du WebSocket arrivent ensemble.
function newSession(message) {
  if (!pendingSession) pendingSession = startSession(message).finally(() => { pendingSession = null; });
  return pendingSession;
}

async function startSession(message) {
  S.selected.clear(); S.open = null; closeDrawer();
  sessionStorage.removeItem(STORE);
  let code = null;
  for (;;) {
    try {
      const r = await fetch('/api/session', { method: 'POST', headers: { 'X-Zoombots': '1', 'content-type': 'application/json' },
        body: JSON.stringify({ code }) });
      const data = await r.json().catch(() => null);
      if (r.status === 401 && S.config.access_code_required) { code = await askCode(code !== null); continue; }
      if (!r.ok) throw new Error(errorText(data, r.status));
      S.sid = data.sid;
      sessionStorage.setItem(STORE, S.sid); // un rechargement de page reprend la session ; un nouvel onglet en démarre une autre
      break;
    } catch (e) { toast(e.message, true); await new Promise(res => setTimeout(res, 3000)); }
  }
  if (message) toast(message);
  connect();
}

function askCode(wrong) {
  $('gate').hidden = false;
  $('gate-err').textContent = wrong ? 'Code incorrect' : '';
  return new Promise(resolve => {
    $('gate-form').onsubmit = ev => { ev.preventDefault(); $('gate').hidden = true; resolve($('gate-code').value); };
  });
}

function setConn(state, text) { const c = $('conn'); c.className = 'conn ' + state; c.textContent = text; }

function connect() {
  clearInterval(S.ping);
  if (S.ws) { S.ws.onclose = null; S.ws.close(); }
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/ws`);
  S.ws = ws;
  ws.onopen = () => { ws.send(JSON.stringify({ sid: S.sid })); setConn('on', 'connecté'); };
  ws.onmessage = ev => { S.snap = JSON.parse(ev.data); render(); };
  ws.onclose = ev => {
    clearInterval(S.ping);
    if (ev.code === 4401) return newSession('Session expirée, nouvelle session démarrée.');
    if (ev.code === 4403 || ev.code === 4429) {  // refus définitifs : inutile de réessayer en boucle
      return setConn('off', ev.code === 4403 ? 'accès refusé (nom d\'hôte ou origine)' : 'trop de connexions pour cette session');
    }
    setConn('off', 'déconnecté, reconnexion…');
    setTimeout(() => { if (S.ws === ws) connect(); }, 1500);
  };
  S.ping = setInterval(() => { if (ws.readyState === 1) ws.send('ping'); }, 5000);
}

// -- rendu -----------------------------------------------------------------

const signatures = {};
// Ne reconstruit une zone que si ses données ont changé : un clic ou un glisser en cours n'est pas perdu
// parce qu'un instantané identique arrive, et le focus clavier reste en place.
function changed(zone, value) {
  const signature = JSON.stringify(value);
  if (signatures[zone] === signature) return false;
  signatures[zone] = signature;
  return true;
}

function render() {
  if (!S.dragging) {  // pas de reconstruction pendant qu'on glisse un son : le glisser serait annulé
    if (changed('bots', [S.snap.bots, [...S.selected]])) renderBots();
    if (changed('sounds', [S.snap.sounds, S.snap.library, S.snap.jobs, S.uploads.map(u => u.name)])) renderSounds();
    renderSoundSelect();
  }
  $('sel-label').textContent = `sélection (${S.selected.size})`;
  if (S.open) renderDrawer();
}

function renderBots() {
  const ids = new Set(S.snap.bots.map(b => b.id));
  for (const id of [...S.selected]) if (!ids.has(id)) S.selected.delete(id);
  if (S.open && !ids.has(S.open)) closeDrawer();
  $('bots').replaceChildren(...(S.snap.bots.length ? S.snap.bots.map(botCard)
    : [el('p', { className: 'muted' }, 'Aucun bot. Déploie-en depuis le panneau 1.')]));
}

function botCard(b) {
  const a = b.action;
  const card = el('div', { className: 'card' + (S.selected.has(b.id) ? ' sel' : ''), dataset: { id: b.id }, on: {
    click: () => openDrawer(b.id),
    dragover: ev => { if (ev.dataTransfer.types.includes('application/x-zb-sound')) { ev.preventDefault(); card.classList.add('drop'); } },
    dragleave: () => card.classList.remove('drop'),
    drop: ev => {
      card.classList.remove('drop');
      const ref = ev.dataTransfer.getData('application/x-zb-sound');
      if (ref) { ev.preventDefault(); ev.stopPropagation(); S.dragging = false; startAction([b.id], ref, 1); }
    } } },
    el('div', { className: 'card-top' },
      el('input', { type: 'checkbox', checked: S.selected.has(b.id), on: { click: ev => {
        ev.stopPropagation();
        ev.target.checked ? S.selected.add(b.id) : S.selected.delete(b.id);
        render();
      } } }),
      el('b', { title: b.name }, b.name),
      el('span', { className: `badge ${b.status}` }, b.status + (b.reconnects ? ` ↻${b.reconnects}` : '')),
    ),
    el('div', { className: 'muted' }, b.link),
    b.error ? el('div', { className: 'err' }, b.error.split('\n')[0].slice(0, 120)) : null,
    a ? el('div', {},
      el('div', { className: 'muted' }, `${a.sound} · ${a.done}/${a.total} ${a.status === 'error' ? '· ' + a.error : a.status === 'stopped' ? '· arrêté' : ''}`),
      el('div', { className: 'bar' }, el('i', { style: `width:${Math.round(100 * a.done / a.total)}%` }))) : null,
    el('div', { className: 'row' },
      el('button', { className: 'small warn', title: "Arrêter l'action en cours", on: { click: ev => { ev.stopPropagation(); run(() => api('/api/actions/stop', 'POST', { bots: [b.id] })); } } }, '⏹'),
      el('button', { className: 'small danger', title: 'Retirer ce bot de la réunion', on: { click: ev => { ev.stopPropagation(); run(() => api(`/api/bots/${b.id}`, 'DELETE')); } } }, '✕ Retirer'),
    ));
  return card;
}

function soundChip(ref, label, extra, removable) {
  return el('span', { className: 'chip', draggable: true, title: 'Glisser sur un bot pour le jouer', on: {
    dragstart: ev => { S.dragging = true; ev.dataTransfer.setData('application/x-zb-sound', ref); ev.dataTransfer.effectAllowed = 'copy'; },
    dragend: () => { S.dragging = false; render(); } } },
    `🔊 ${label}`, extra ? el('span', { className: 'muted' }, extra) : null,
    removable ? el('span', { className: 'x', title: 'Supprimer', on: { click: () => run(() => api(`/api/sounds/${ref}`, 'DELETE')) } }, '✕') : null);
}

function renderSounds() {
  const chips = [];
  for (const u of S.uploads) chips.push(el('span', { className: 'chip pending' }, `⏳ ${u.name}`));
  for (const j of S.snap.jobs) {
    if (j.status === 'downloading') chips.push(el('span', { className: 'chip pending' }, '⏳ YouTube…'));
    else if (j.status === 'error') chips.push(el('span', { className: 'chip pending err', title: j.url }, `YouTube : ${j.error || 'échec'}`));
  }
  for (const s of S.snap.sounds) chips.push(soundChip(s.id, s.name, fmtDuration(s.duration), true));
  for (const name of S.snap.library) chips.push(soundChip('lib:' + name, name, 'serveur', false));
  $('sounds').replaceChildren(...(chips.length ? chips : [el('span', { className: 'muted' }, 'Aucun son pour le moment.')]));
}

function renderSoundSelect() {
  const sel = $('act-sound');
  const options = [...S.snap.sounds.map(s => [s.id, s.name]), ...S.snap.library.map(n => ['lib:' + n, n + ' (serveur)'])];
  const signature = JSON.stringify(options);
  if (sel.dataset.sig === signature) return;
  const previous = sel.value;
  sel.dataset.sig = signature;
  sel.replaceChildren(...(options.length ? options.map(([v, t]) => el('option', { value: v }, t)) : [el('option', { value: '' }, '(aucun son)')]));
  if (options.some(([v]) => v === previous)) sel.value = previous;
}

// -- actions ---------------------------------------------------------------

function actionParams() {
  return { count: +$('act-count').value || 1, gap: +$('act-gap').value || 0,
           volume: $('act-volume').value === '' ? 1 : +$('act-volume').value, stagger: +$('act-stagger').value || 0 };
}

async function startAction(bots, sound, countOverride) {
  if (!sound) return toast('Choisis un son (panneau 2).', true);
  const p = actionParams();
  if (countOverride) { p.count = countOverride; p.gap = 0; p.stagger = 0; }
  await run(() => api('/api/actions', 'POST', { bots, sound, ...p }), 'Action lancée');
}

$('act-btn').addEventListener('click', () => {
  const mode = document.querySelector('input[name=target]:checked').value;
  if (mode === 'selected' && !S.selected.size) return toast('Aucun bot sélectionné.', true);
  startAction(mode === 'selected' ? [...S.selected] : null, $('act-sound').value);
});

$('stop-all').addEventListener('click', () => run(() => api('/api/actions/stop', 'POST', {}), 'Tout est stoppé'));
$('leave-all').addEventListener('click', async () => {
  if (S.snap.bots.length && !confirm('Retirer tous vos bots de leurs réunions ?')) return;
  await run(() => api('/api/bots', 'DELETE'), 'Bots retirés');
});
$('clean').addEventListener('click', () => run(() => api('/api/bots?finished=true', 'DELETE')));
$('sel-all').addEventListener('click', () => {
  const all = S.snap.bots.every(b => S.selected.has(b.id));
  S.selected = new Set(all ? [] : S.snap.bots.map(b => b.id));
  render();
});

// -- déploiement -----------------------------------------------------------

$('deploy-btn').addEventListener('click', ev => busy(ev.currentTarget, deploy));
async function deploy() {
  const links = $('links').value.split('\n').map(l => l.trim()).filter(Boolean).map(line => {
    const [meeting, ...rest] = line.split('|');
    return { meeting: meeting.trim(), passcode: rest.join('|').trim() || null };
  });
  if (!links.length) return toast('Ajoute au moins un lien ou ID de réunion.', true);
  const body = { links, count: +$('count').value || 1, name: $('name').value.trim() || 'Bot',
                 mode: document.querySelector('input[name=mode]:checked').value, auto_reconnect: $('reconnect').checked };
  const r = await run(() => api('/api/deploy', 'POST', body));
  if (r) toast(`${r.bots.length} bot(s) en cours de connexion`);
}

// -- sons : glisser-déposer, parcourir, YouTube -----------------------------

const AUDIO_EXTS = ['.mp3', '.wav', '.ogg', '.flac', '.m4a', '.opus', '.aac', '.mp4', '.webm', '.mkv'];

async function uploadFiles(files) {
  const limit = S.config.limits.upload_mb * 1024 * 1024;
  for (const file of files) {
    const ext = file.name.includes('.') ? file.name.slice(file.name.lastIndexOf('.')).toLowerCase() : '';
    if (!AUDIO_EXTS.includes(ext)) { toast(`${file.name} : format non géré (${ext || 'sans extension'})`, true); continue; }
    if (file.size > limit) { toast(`${file.name} : trop gros (max ${S.config.limits.upload_mb} Mo)`, true); continue; }
    const entry = { name: file.name };
    S.uploads.push(entry); renderSounds();
    try { await api(`/api/sounds?name=${enc(file.name)}`, 'PUT', file); }
    catch (e) { toast(`${file.name} : ${e.message}`, true); }
    S.uploads = S.uploads.filter(u => u !== entry);
    try { S.snap = await api('/api/state'); } catch (e) { /* le WebSocket rattrapera */ }
    render();
  }
}

let dragDepth = 0;
const hasFiles = ev => ev.dataTransfer && [...ev.dataTransfer.types].includes('Files');
window.addEventListener('dragenter', ev => { if (hasFiles(ev)) { dragDepth++; $('overlay').hidden = false; } });
window.addEventListener('dragleave', ev => { if (hasFiles(ev) && --dragDepth <= 0) { dragDepth = 0; $('overlay').hidden = true; } });
window.addEventListener('dragover', ev => { if (hasFiles(ev)) ev.preventDefault(); });
window.addEventListener('drop', ev => {
  dragDepth = 0; $('overlay').hidden = true;
  if (hasFiles(ev)) { ev.preventDefault(); uploadFiles([...ev.dataTransfer.files]); }
});
$('dropzone').addEventListener('click', () => $('file-input').click());
$('dropzone').addEventListener('keydown', ev => { if (ev.key === 'Enter' || ev.key === ' ') $('file-input').click(); });
$('file-input').addEventListener('change', ev => { uploadFiles([...ev.target.files]); ev.target.value = ''; });

$('yt-btn').addEventListener('click', ev => busy(ev.currentTarget, async () => {
  const url = $('yt-url').value.trim();
  if (!url) return;
  if (await run(() => api('/api/youtube', 'POST', { url }), 'Téléchargement lancé')) $('yt-url').value = '';
}));
$('yt-url').addEventListener('keydown', ev => { if (ev.key === 'Enter') $('yt-btn').click(); });

// -- panneau d'un bot ------------------------------------------------------

let drawerTimer;
let drawerBusy = false;
function openDrawer(id) {
  if (S.open !== id) {  // pas d'état de l'ancien bot affiché pendant le chargement du nouveau
    $('d-events').replaceChildren();
    $('d-shot').removeAttribute('src');
  }
  S.open = id;
  $('drawer').hidden = false;
  renderDrawer();
  refreshDrawer();
  clearInterval(drawerTimer);
  drawerTimer = setInterval(refreshDrawer, 3000);
}

function closeDrawer() {
  S.open = null;
  $('drawer').hidden = true;
  clearInterval(drawerTimer);
  if (S.shotUrl) { URL.revokeObjectURL(S.shotUrl); S.shotUrl = null; }
}

function renderDrawer() {
  const b = S.snap.bots.find(x => x.id === S.open);
  if (!b) return closeDrawer();
  $('d-name').textContent = b.name;
  $('d-status').replaceChildren(...[el('span', { className: `badge ${b.status}` }, b.status),
    b.error ? el('div', { className: 'err' }, b.error.split('\n')[0]) : null].filter(Boolean));
  $('d-link').textContent = b.link;
  const a = b.action;
  $('d-action').textContent = a ? `Action : ${a.sound} · ${a.done}/${a.total} (${a.status})` : 'Aucune action en cours';
}

async function refreshDrawer() {
  const id = S.open;
  if (!id || drawerBusy) return;  // une capture lente ne s'empile pas
  drawerBusy = true;
  try { await refreshDrawerOnce(id); } finally { drawerBusy = false; }
}

async function refreshDrawerOnce(id) {
  try {
    const events = await api(`/api/bots/${id}/events`);
    if (S.open !== id) return;
    $('d-events').replaceChildren(...events.slice().reverse().map(e =>
      el('li', {}, `${new Date(e.ts * 1000).toLocaleTimeString()} · ${e.status}${e.error ? ' · ' + e.error.split('\n')[0] : ''}`)));
  } catch (e) { /* bot retiré entre-temps */ }
  try {
    const r = await api(`/api/bots/${id}/screenshot`, 'GET', undefined, true);
    const url = URL.createObjectURL(await r.blob());
    if (S.open !== id) return URL.revokeObjectURL(url);
    if (S.shotUrl) URL.revokeObjectURL(S.shotUrl);
    S.shotUrl = url;
    $('d-shot').src = url;
  } catch (e) { $('d-shot').removeAttribute('src'); /* pas de page pour l'instant */ }
}

$('d-close').addEventListener('click', closeDrawer);
$('d-mic-on').addEventListener('click', () => run(() => api(`/api/bots/${S.open}/mic`, 'POST', { on: true }), 'Micro activé'));
$('d-mic-off').addEventListener('click', () => run(() => api(`/api/bots/${S.open}/mic`, 'POST', { on: false }), 'Micro coupé'));
$('d-play').addEventListener('click', () => startAction([S.open], $('act-sound').value));
$('d-stop').addEventListener('click', () => run(() => api('/api/actions/stop', 'POST', { bots: [S.open] }), 'Arrêté'));
$('d-remove').addEventListener('click', () => run(() => api(`/api/bots/${S.open}`, 'DELETE'), 'Bot retiré'));

// -- démarrage -------------------------------------------------------------

(async function init() {
  try { S.config = await (await fetch('/api/config')).json(); }
  catch (e) { return setConn('off', 'serveur injoignable'); }
  const l = S.config.limits;
  $('limits').textContent = `max ${l.session_bots} bots actifs par session, ${l.sounds} sons de ${l.upload_mb} Mo / ${Math.round(l.sound_seconds / 60)} min`;
  $('count').max = l.deploy;
  const saved = sessionStorage.getItem(STORE);
  if (saved) {
    // session de cet onglet encore valide (rechargement de page dans le délai de grâce) ?
    const r = await fetch('/api/state', { headers: { 'X-Session': saved } }).catch(() => null);
    if (r && r.ok) { S.sid = saved; S.snap = await r.json(); render(); connect(); return; }
  }
  await newSession();
})();
