// Tout le contenu dynamique passe par textContent / addEventListener : aucun innerHTML, donc pas de XSS.
const $ = id => document.getElementById(id);
const enc = encodeURIComponent;

async function api(path, method = 'GET', body) {
  const headers = { 'X-Zoombots': '1' };
  if (body !== undefined) headers['content-type'] = 'application/json';
  try {
    const r = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    const data = await r.json().catch(() => null);
    if (!r.ok) {
      const d = data && data.detail;
      alert(Array.isArray(d) ? d.map(e => e.msg).join('\n') : d || `Erreur ${r.status}`);
      return null;
    }
    return data;
  } catch (e) {
    return null; // serveur injoignable : on réessaiera au prochain rafraîchissement
  }
}

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) k === 'onclick' ? node.addEventListener('click', v) : (node[k] = v);
  node.append(...children);
  return node;
}

const button = (label, onclick, title) => el('button', { onclick, title: title || '' }, label);
let last = '';

async function refresh() {
  const [bots, sounds] = await Promise.all([api('/bots'), api('/sounds')]);
  if (!bots || !sounds) return;
  const signature = JSON.stringify([bots, sounds]);
  if (signature === last) return; // rien n'a changé : on ne touche pas au DOM
  last = signature;

  $('sounds').replaceChildren(...(sounds.length
    ? sounds.map(s => button(s, () => api(`/broadcast/sounds/${enc(s)}`, 'POST')))
    : [el('i', {}, 'Dépose des fichiers audio dans le dossier sounds/')]));

  $('bots').replaceChildren(...(bots.length ? bots.map(b => el('div', { className: 'bot' },
    el('b', {}, b.name),
    el('span', { className: `status ${b.status}` }, b.status + (b.reconnects ? ` (↻${b.reconnects})` : '')),
    button('🎤', () => api(`/bots/${b.id}/mic`, 'POST', { on: true })),
    button('🔇', () => api(`/bots/${b.id}/mic`, 'POST', { on: false })),
    button('⏹', () => api(`/bots/${b.id}/stop`, 'POST')),
    ...sounds.map(s => button(s, () => api(`/bots/${b.id}/sounds/${enc(s)}`, 'POST'))),
    el('a', { href: `/bots/${b.id}/screenshot`, target: '_blank', rel: 'noopener', textContent: '📷' }),
    button('Quitter', async () => { await api(`/bots/${b.id}`, 'DELETE'); last = ''; refresh(); }),
    b.error ? el('div', { className: 'err' }, b.error) : '',
  )) : [el('i', {}, 'Aucun bot')]));
}

async function refreshStats() {
  const s = await api('/stats');
  if (!s) return;
  $('stats').textContent = `RAM bots: ${s.total_rss_mb} Mo`
    + (s.avg_rss_mb_per_bot ? ` (~${s.avg_rss_mb_per_bot} Mo/bot, capacité estimée ${s.estimated_capacity})` : '')
    + ` · RAM dispo: ${s.system.mem_available_mb} Mo · limite: ${s.max_bots} bots actifs`;
}

$('spawn').addEventListener('click', async () => {
  await api('/bots', 'POST', { meeting: $('meeting').value, passcode: $('passcode').value || null,
    name: $('name').value || 'Bot', count: +$('count').value, auto_reconnect: $('reconnect').checked });
  last = ''; refresh();
});
$('purge').addEventListener('click', async () => { await api('/bots?finished=true', 'DELETE'); last = ''; refresh(); });

refresh(); setInterval(refresh, 3000);
refreshStats(); setInterval(refreshStats, 10000);
