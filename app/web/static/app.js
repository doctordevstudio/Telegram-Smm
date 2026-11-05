'use strict';
/* Telegram SMM admin panel - vanilla JS SPA. All user-controlled text goes through esc(). */
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = n => Number(n || 0).toLocaleString('en-IN');
const when = ts => ts ? new Date(ts * 1000).toLocaleString(undefined, { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
const SV = { subscribers: ['👥', 'Subscribers'], views: ['👁', 'Views'], reactions: ['❤️', 'Reactions'], mute: ['🔇', 'Mute Actions'], poll: ['📊', 'Poll Voting'], stream: ['🔴', 'Live Stream'] };
const SV_ORDER = Object.keys(SV);
const view = $('#view'), A = {};
let me = null, timer = null;

/* ───────── core ───────── */
async function api(path, { method = 'GET', json, form } = {}) {
  const o = { method, headers: { 'X-Requested-With': 'smm' }, credentials: 'same-origin' };
  if (json !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(json); }
  if (form) o.body = form;
  const r = await fetch('/api' + path, o);
  if (r.status === 401 && path !== '/login') { showLogin(); throw new Error('Please log in again'); }
  let d = null; try { d = await r.json(); } catch { /* empty */ }
  if (!r.ok) {
    const det = d && d.detail;
    throw new Error(typeof det === 'string' ? det : det ? 'Check the values you entered' : 'Request failed (' + r.status + ')');
  }
  return d;
}
function toast(msg, bad) {
  const t = document.createElement('div'); t.className = 'toast' + (bad ? ' bad' : ''); t.textContent = msg;
  $('#toasts').appendChild(t); setTimeout(() => t.remove(), bad ? 6000 : 3200);
}
function modal(html, wide) {
  closeModal();
  const o = document.createElement('div'); o.className = 'ov';
  o.innerHTML = `<div class="modal${wide ? ' wide' : ''}" role="dialog" aria-modal="true">${html}</div>`;
  o.addEventListener('mousedown', e => { if (e.target === o) closeModal(); });
  document.body.appendChild(o); return o;
}
const closeModal = () => $$('.ov').forEach(x => x.remove());
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });
document.addEventListener('click', e => {
  const el = e.target.closest('[data-act]'); if (!el) return;
  const f = A[el.dataset.act]; if (!f) return;
  if (el.type !== 'checkbox') e.preventDefault();
  const b = el.tagName === 'BUTTON' ? el : null; if (b) b.disabled = true;
  Promise.resolve(f(el, e)).catch(err => toast(err.message, true)).finally(() => { if (b) b.disabled = false; });
});
const loading = () => { view.innerHTML = '<div class="grid g4"><div class="skel"></div><div class="skel"></div><div class="skel"></div><div class="skel"></div></div><div class="spacer"></div><div class="skel" style="height:260px"></div>'; };
const pill = s => ({ active: 'green', dead: 'red', flood: 'yellow', limited: 'orange', new: 'gray', enabled: 'green', disabled: 'gray', removed: 'gray', yes: 'green', no: 'gray' }[s] || 'gray');
const P = (s, label) => `<span class="pill ${pill(s)}"><i class="dot"></i>${esc(label || s)}</span>`;
const pager = (page, pages, act, extraAttr = '') => pages <= 1 ? '' : `<div class="pager"><button class="btn sm" data-act="${act}" data-page="${page - 1}" ${extraAttr} ${page <= 1 ? 'disabled' : ''}>← Prev</button><span class="muted">Page ${page} of ${pages}</span><button class="btn sm" data-act="${act}" data-page="${page + 1}" ${extraAttr} ${page >= pages ? 'disabled' : ''}>Next →</button></div>`;
const table = (cols, rows, empty = 'Nothing here yet') => rows.length ? `<div class="tw"><table><thead><tr>${cols.map(c => `<th>${c}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>` : `<div class="card empty">${empty}</div>`;

/* ───────── auth / shell ───────── */
function showLogin() { $('#app').hidden = true; $('#login').hidden = false; clearInterval(timer); }
$('#loginForm').addEventListener('submit', async e => {
  e.preventDefault(); const f = e.target, b = $('button', f); b.disabled = true; $('#loginErr').textContent = '';
  try { await api('/login', { method: 'POST', json: { username: f.username.value, password: f.password.value } }); f.password.value = ''; await boot(); }
  catch (err) { $('#loginErr').textContent = err.message; }
  b.disabled = false;
});
A.logout = async () => { await api('/logout', { method: 'POST' }); showLogin(); };
A.menu = () => document.body.classList.toggle('menu');
A.close = closeModal;
A.reload = () => location.reload();

const PAGES = { home: ['Dashboard', pageHome], channels: ['Channels', pageChannels], channel: ['Channel', pageChannelDetail], sessions: ['Sessions', pageSessions], admins: ['Admins', pageAdmins], logs: ['Logs', pageLogs], settings: ['Setting', pageSettings] };
async function route() {
  clearInterval(timer); document.body.classList.remove('menu'); closeModal();
  const parts = (location.hash.replace('#/', '') || 'home').split('/');
  const p = parts[0], arg = /^[A-Za-z0-9_-]{1,64}$/.test(parts[1] || '') ? parts[1] : undefined;
  const pg = PAGES[p] || PAGES.home;
  $('#pageTitle').textContent = p === 'channel' ? 'Channel' : pg[0];
  document.title = pg[0] + ' · Telegram SMM';
  $$('#nav a').forEach(a => a.classList.toggle('on', a.dataset.p === (p === 'channel' ? 'channels' : p)));
  try { await pg[1](arg); view.focus({ preventScroll: true }); }
  catch (err) { if (err.message !== 'Please log in again') view.innerHTML = `<div class="card empty">😵 ${esc(err.message)}<br><br><button class="btn" data-act="reload">Reload</button></div>`; }
}
async function boot() {
  try { me = await api('/me'); } catch { return showLogin(); }
  $('#login').hidden = true; $('#app').hidden = false; $('#who').textContent = '👑 ' + me.username;
  route();
}
window.addEventListener('hashchange', route);

/* ───────── DASHBOARD ───────── */
async function pageHome(auto) {
  if (auto !== true) loading();
  const d = await api('/dashboard');
  if (!/^(#\/?(home)?)?$/.test(location.hash)) return;  // user navigated away while the request was in flight
  const c = d.channels, s = d.engine.sessions || {}, st = d.engine.stream || {};
  view.innerHTML = `
  <div class="grid g4">
    <a class="stat" href="#/channels"><span>Channels</span><b>${num(c.total)}</b><small>${num(c.active_services)} active service switches</small></a>
    <a class="stat" href="#/sessions"><span>Sessions</span><b>${num(s.active)}</b><small>of ${num(s.total)} · ${num(s.dead)} dead · ${num(s.flood)} resting</small></a>
    <div class="stat"><span>Live-stream</span><b>${num(st.connections)}</b><small>${st.available === false ? '⚠️ pytgcalls unavailable' : (num(st.channels) + ' channel(s)')}</small></div>
    <a class="stat" href="#/admins"><span>Admins</span><b>${num(d.admins.active)}</b><small>${num(d.admins.total)} ever granted access</small></a>
  </div><div class="spacer"></div>
  <div class="card"><h3>Service switches by type</h3>${SV_ORDER.map(k => `<div class="hbar"><div class="kv" style="border:0;padding:0 0 4px"><span>${SV[k][0]} ${SV[k][1]}</span><b>${num(c.per_service[k])}</b></div><div><i style="width:${c.total ? Math.min(100, (+c.per_service[k] || 0) / c.total * 100) : 0}%"></i></div></div>`).join('')}</div>
  <div class="spacer"></div><h3 style="margin-bottom:10px">Recent activity</h3>
  ${table(['When', 'Who', 'Action', 'Details'], d.recent.map(l => `<tr><td style="white-space:nowrap">${when(l.ts)}</td><td>${l.role === 'owner' ? '👑' : l.role === 'admin' ? '🧑‍💼' : '⚙️'} ${l.uid ? '<code>' + esc(l.uid) + '</code>' : 'system'}</td><td><b>${esc(l.action)}</b></td><td class="muted">${esc(l.detail || '')}</td></tr>`), 'No activity yet')}
  <div class="row" style="justify-content:flex-end;margin-top:10px"><a class="btn sm" href="#/logs">View all logs →</a></div>`;
  timer = setInterval(() => pageHome(true).catch(() => {}), 20000);
}

/* ───────── CHANNELS ───────── */
const CH = { page: 1, q: '', includeHidden: false };
async function pageChannels() {
  loading();
  const d = await api(`/channels?page=${CH.page}&include_hidden=${CH.includeHidden}&q=${encodeURIComponent(CH.q)}`);
  view.innerHTML = `<div class="row" style="margin-bottom:14px;justify-content:space-between">
    <div class="row"><input id="cq" placeholder="Search channels" value="${esc(CH.q)}" style="max-width:240px"><button class="btn sm primary" data-act="chanSearch">Search</button></div>
    <label class="row" style="gap:8px"><input type="checkbox" id="chHidden" ${CH.includeHidden ? 'checked' : ''} data-act="chanToggleHidden"> Show removed</label></div>
  ${table(['Channel', 'Type', 'Overall validity', 'Active services', ''], d.items.map(c => `<tr class="click" data-act="chanOpen" data-id="${esc(c.id)}">
    <td><b>${esc(c.title)}</b><br><span class="muted">${esc(c.link || '')}</span></td>
    <td>${c.type === 'private' ? '🔒 Private' : '🌐 Public'}</td>
    <td>${c.hidden ? P('removed', 'Removed') : (c.expires_at ? esc(c.days_left) : '<span class="muted">no overall limit</span>')}</td>
    <td>${SV_ORDER.filter(k => c.services[k].enabled).map(k => SV[k][0]).join(' ') || '<span class="muted">none</span>'}</td>
    <td>${c.active ? P('active', 'Active') : P('disabled', 'Idle')}</td></tr>`), 'No channels yet')}
  ${pager(d.page, d.pages, 'chanPage')}`;
  $('#cq').addEventListener('keydown', e => { if (e.key === 'Enter') A.chanSearch(); });
}
A.chanSearch = () => { CH.q = $('#cq').value.trim(); CH.page = 1; return pageChannels(); };
A.chanToggleHidden = () => { CH.includeHidden = $('#chHidden').checked; CH.page = 1; return pageChannels(); };
A.chanPage = el => { CH.page = +el.dataset.page; return pageChannels(); };
A.chanOpen = el => { location.hash = '#/channel/' + el.dataset.id; };

/* ───────── CHANNEL DETAIL ───────── */
async function pageChannelDetail(cid) {
  loading();
  const d = await api(`/channels/${encodeURIComponent(cid)}`);
  const c = d.channel, s = d.settings, ecid = esc(cid);
  view.innerHTML = `<a class="btn sm" href="#/channels">← All channels</a><div class="spacer"></div>
  <div class="card"><div class="row" style="justify-content:space-between;flex-wrap:wrap">
    <div><h3 style="margin-bottom:4px">${esc(c.title)} ${c.hidden ? P('removed', 'Removed') : ''}</h3>
      <span class="muted">${esc(c.link || '')} ${c.chat_id ? '· <code>' + esc(c.chat_id) + '</code>' : ''}</span><br>
      <span class="muted">Overall validity: ${c.expires_at ? esc(c.days_left) : 'no overall limit'}${c.rate_note ? ' · ' + esc(c.rate_note) : ''}</span></div>
    <div class="row">
      <button class="btn sm" data-act="chanRenew" data-id="${ecid}">🔁 Renew</button>
      <button class="btn sm bad" data-act="chanRevokeAll" data-id="${ecid}">🧨 Revoke All</button>
      ${c.hidden ? `<button class="btn sm good" data-act="chanRestore" data-id="${ecid}">♻️ Restore</button>` : `<button class="btn sm bad" data-act="chanHide" data-id="${ecid}">🗑 Remove</button>`}
    </div></div></div><div class="spacer"></div>
  <div class="card"><h3>⏳ Delay / 🌙 Night Mode / 🌊 Wave Mode</h3>
    <div class="three">
      <label>Process delay before acting on a new post<div class="row" style="flex-wrap:nowrap"><input id="delaySec" type="number" min="0" value="${esc(s.delay_seconds)}"><button class="btn sm" data-act="saveDelay" data-id="${ecid}">Save</button></div></label>
      <label>Night mode <input type="checkbox" id="nmEn" ${s.night_mode.enabled ? 'checked' : ''}> enabled
        <div class="row" style="margin-top:6px"><input id="nmStart" type="number" min="0" max="23" value="${esc(s.night_mode.start_hour)}" placeholder="start h" style="width:70px"><input id="nmEnd" type="number" min="0" max="23" value="${esc(s.night_mode.end_hour)}" placeholder="end h" style="width:70px"><input id="nmMult" type="number" min="1" step="0.1" value="${esc(s.night_mode.multiplier)}" placeholder="×slower" style="width:80px"></div>
        <button class="btn sm" style="margin-top:6px" data-act="saveNight" data-id="${ecid}">Save</button></label>
      <label>Wave mode (speed tapers as a post ages) <input type="checkbox" id="wvEn" ${s.wave_mode.enabled ? 'checked' : ''}> enabled
        <button class="btn sm" style="margin-top:6px" data-act="saveWave" data-id="${ecid}">Save</button></label>
    </div></div><div class="spacer"></div>
  <h3 style="margin-bottom:10px">Services — apply to every upcoming post</h3>
  <div class="grid g2" id="svcGrid">${SV_ORDER.map(k => serviceCard(cid, k, d.services[k])).join('')}</div>`;
}

function serviceCard(cid, svc, s) {
  const [emoji, title] = SV[svc];
  if (!s.enabled) {
    return `<div class="card"><h3>${emoji} ${title}</h3><p class="muted">Not active on this channel.</p>
      <button class="btn primary sm" data-act="svcEnable" data-cid="${esc(cid)}" data-svc="${esc(svc)}">✅ Enable service</button></div>`;
  }
  const cfg = s.config, left = s.expires_at ? '' : '';
  const body = svcFields(svc, cfg);
  return `<div class="card"><div class="row" style="justify-content:space-between"><h3>${emoji} ${title}</h3>${P('active', 'Active')}</div>
    <p class="muted">⏳ expires in ${num(Math.max(0, Math.round((s.expires_at - Date.now() / 1000) / 86400)))} day(s)${s.rate_note ? ' · ' + esc(s.rate_note) : ''}</p>
    <div id="svcfields-${esc(cid)}-${esc(svc)}">${body}</div>
    <div class="row" style="margin-top:10px">
      <button class="btn sm primary" data-act="svcSave" data-cid="${esc(cid)}" data-svc="${esc(svc)}">💾 Save config</button>
      <button class="btn sm" data-act="svcExtend" data-cid="${esc(cid)}" data-svc="${esc(svc)}">🔁 Extend</button>
      <button class="btn sm bad" data-act="svcRevoke" data-cid="${esc(cid)}" data-svc="${esc(svc)}">🧨 Revoke</button>
    </div></div>`;
}

function svcFields(svc, cfg) {
  const num1 = (id, label, val, step = 1) => `<label>${label}<input id="${id}" type="number" step="${step}" value="${esc(val)}"></label>`;
  const chk = (id, label, val) => `<label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="${id}" ${val ? 'checked' : ''}> ${label}</label>`;
  if (svc === 'subscribers') return `<div class="two">${num1('f_interval', 'Interval (sec) between each new subscriber', cfg.interval_seconds)}${num1('f_target', 'Target count (0 = until expiry)', cfg.target_count)}</div>`;
  if (svc === 'views') return `<div class="two">${num1('f_qty', 'Quantity per post', cfg.qty)}${num1('f_speed', 'Speed (sec per view)', cfg.speed_seconds, 0.5)}</div><div class="two">${chk('f_rand', 'Random views ±range', cfg.random_enabled)}${num1('f_range', 'Random range', cfg.random_range)}</div>`;
  if (svc === 'reactions') return `<div class="two">${num1('f_qty', 'Quantity per post', cfg.qty)}${num1('f_speed', 'Speed (sec per reaction)', cfg.speed_seconds, 0.5)}</div>${chk('f_skip', 'Skip poster (no effect for rented sessions)', cfg.skip_poster)}<label>Emoji mix (JSON, e.g. {"❤":80,"🔥":20}) — empty = system random<textarea id="f_mix" style="min-height:60px">${esc(JSON.stringify(cfg.mix || {}))}</textarea></label>`;
  if (svc === 'mute') return `<div class="two">${num1('f_daily', 'Daily mute/unmute actions', cfg.daily_actions)}${num1('f_speed', 'Speed (sec between actions)', cfg.speed_seconds, 0.5)}</div>`;
  if (svc === 'poll') return `<div class="two">${num1('f_votes', 'Max votes per poll (0 = unlimited)', cfg.max_votes)}${num1('f_speed', 'Speed (sec per vote)', cfg.speed_seconds, 0.5)}</div>`;
  if (svc === 'stream') return `<div class="two">${num1('f_members', 'Target concurrent viewers', cfg.max_members)}${num1('f_speed', 'Join speed (sec)', cfg.join_speed, 0.1)}</div><div class="two">${num1('f_hand', 'Hand-rise %', cfg.hand_rise_pct)}${chk('f_paused', 'Paused', cfg.paused)}</div>`;
  return '';
}

function readSvcFields(svc) {
  const v = id => document.getElementById(id);
  if (svc === 'subscribers') return { interval_seconds: +v('f_interval').value, target_count: +v('f_target').value };
  if (svc === 'views') return { qty: +v('f_qty').value, speed_seconds: +v('f_speed').value, random_enabled: v('f_rand').checked, random_range: +v('f_range').value };
  if (svc === 'reactions') {
    let mix = {}; try { mix = JSON.parse(v('f_mix').value || '{}'); } catch { throw new Error('Emoji mix must be valid JSON'); }
    return { qty: +v('f_qty').value, speed_seconds: +v('f_speed').value, skip_poster: v('f_skip').checked, mix };
  }
  if (svc === 'mute') return { daily_actions: +v('f_daily').value, speed_seconds: +v('f_speed').value };
  if (svc === 'poll') return { max_votes: +v('f_votes').value, speed_seconds: +v('f_speed').value };
  if (svc === 'stream') return { max_members: +v('f_members').value, join_speed: +v('f_speed').value, hand_rise_pct: +v('f_hand').value, paused: v('f_paused').checked };
  return {};
}

A.chanRenew = el => modal(`<h3>🔁 Renew channel</h3><label>Extra days<input id="rnDays" type="number" min="1" value="30"></label><div class="row" style="justify-content:flex-end"><button class="btn" data-act="close">Cancel</button><button class="btn primary" data-act="chanRenewGo" data-id="${esc(el.dataset.id)}">Renew</button></div>`);
A.chanRenewGo = async el => { await api(`/channels/${encodeURIComponent(el.dataset.id)}/renew`, { method: 'POST', json: { days: +$('#rnDays').value } }); toast('Renewed'); closeModal(); pageChannelDetail(el.dataset.id); };
A.chanRevokeAll = async el => { if (!confirm('Revoke every active service on this channel?')) return; await api(`/channels/${encodeURIComponent(el.dataset.id)}/revoke_all`, { method: 'POST' }); toast('Revoked all'); pageChannelDetail(el.dataset.id); };
A.chanHide = async el => { if (!confirm('Remove this channel from your lists? (nothing is deleted from the database)')) return; await api(`/channels/${encodeURIComponent(el.dataset.id)}/hide`, { method: 'POST' }); toast('Removed'); location.hash = '#/channels'; };
A.chanRestore = async el => { await api(`/channels/${encodeURIComponent(el.dataset.id)}/restore`, { method: 'POST' }); toast('Restored'); pageChannelDetail(el.dataset.id); };

A.saveDelay = async el => { await api(`/channels/${encodeURIComponent(el.dataset.id)}/settings`, { method: 'PUT', json: { delay_seconds: +$('#delaySec').value } }); toast('Saved'); };
A.saveNight = async el => { await api(`/channels/${encodeURIComponent(el.dataset.id)}/settings`, { method: 'PUT', json: { night_mode: { enabled: $('#nmEn').checked, start_hour: +$('#nmStart').value, end_hour: +$('#nmEnd').value, multiplier: +$('#nmMult').value } } }); toast('Saved'); };
A.saveWave = async el => { await api(`/channels/${encodeURIComponent(el.dataset.id)}/settings`, { method: 'PUT', json: { wave_mode: { enabled: $('#wvEn').checked } } }); toast('Saved'); };

A.svcEnable = el => {
  const cid = esc(el.dataset.cid), svc = el.dataset.svc;
  if (!SV[svc]) return;
  modal(`<h3>${SV[svc][0]} Enable ${SV[svc][1]}</h3><label>Days active on every upcoming post<input id="enDays" type="number" min="1" value="30"></label><label>Rate note (optional, e.g. "₹200 for 20 days")<input id="enRate"></label><div class="row" style="justify-content:flex-end"><button class="btn" data-act="close">Cancel</button><button class="btn primary" data-act="svcEnableGo" data-cid="${esc(cid)}" data-svc="${esc(svc)}">Enable</button></div>`);
};
A.svcEnableGo = async el => {
  const { cid, svc } = el.dataset;
  await api(`/channels/${encodeURIComponent(cid)}/services/${encodeURIComponent(svc)}/enable`, { method: 'POST', json: { days: +$('#enDays').value, rate_note: $('#enRate').value } });
  toast('Enabled'); closeModal(); pageChannelDetail(cid);
};
A.svcSave = async el => {
  const { cid, svc } = el.dataset;
  const config = readSvcFields(svc);
  await api(`/channels/${encodeURIComponent(cid)}/services/${encodeURIComponent(svc)}/config`, { method: 'PUT', json: { config } });
  toast('Config saved');
};
A.svcExtend = el => {
  const { svc } = el.dataset, cid = esc(el.dataset.cid);
  if (!SV[svc]) return;
  modal(`<h3>🔁 Extend ${SV[svc][1]}</h3><label>Extra days<input id="extDays" type="number" min="1" value="30"></label><div class="row" style="justify-content:flex-end"><button class="btn" data-act="close">Cancel</button><button class="btn primary" data-act="svcExtendGo" data-cid="${esc(cid)}" data-svc="${esc(svc)}">Extend</button></div>`);
};
A.svcExtendGo = async el => { const { cid, svc } = el.dataset; await api(`/channels/${encodeURIComponent(cid)}/services/${encodeURIComponent(svc)}/extend`, { method: 'POST', json: { days: +$('#extDays').value } }); toast('Extended'); closeModal(); pageChannelDetail(cid); };
A.svcRevoke = async el => { const { cid, svc } = el.dataset; if (!confirm(`Revoke ${SV[svc][1]}?`)) return; await api(`/channels/${encodeURIComponent(cid)}/services/${encodeURIComponent(svc)}/revoke`, { method: 'POST' }); toast('Revoked'); pageChannelDetail(cid); };

/* ───────── SESSIONS ───────── */
const SS = { page: 1, status: '', sel: new Set() };
async function pageSessions() {
  view.innerHTML = `<div id="sessTop"></div>
  <div class="grid g2"><div class="card"><h3>Add session files</h3>
    <div id="drop" class="drop" tabindex="0" role="button" aria-label="Choose session files"><b>Drop .session files here</b>or tap to pick many at once<input id="fileIn" type="file" multiple accept=".session,.txt,.string" hidden></div>
    <div class="prog" id="upProg" hidden><i style="width:0"></i></div><div id="upLog" class="files"></div></div>
    <div class="card"><h3>Add session strings</h3><label>One Telethon string per line<textarea id="strIn" placeholder="1BVts…"></textarea></label><button class="btn primary" data-act="addStrings">Import strings</button></div></div>
  <div class="spacer"></div><div id="sessTable"></div>`;
  const drop = $('#drop'), fi = $('#fileIn');
  drop.addEventListener('click', () => fi.click());
  drop.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fi.click(); } });
  fi.addEventListener('change', () => { uploadFiles([...fi.files]); fi.value = ''; });
  ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', e => uploadFiles([...e.dataTransfer.files]));
  await renderSessions();
}
async function renderSessions() {
  const d = await api(`/sessions?page=${SS.page}&status=${SS.status}`), c = d.counts, v = d.validation;
  $('#sessTop').innerHTML = `<div class="card" style="margin-bottom:16px"><div class="row" style="justify-content:space-between"><div class="row" style="gap:18px"><span>🧩 Total <b>${num(c.total)}</b></span><span class="ok">● Active <b>${num(c.active)}</b></span><span style="color:var(--sun)">● Resting <b>${num(c.flood)}</b></span><span style="color:#ffa45c">● Limited <b>${num(c.limited)}</b></span><span style="color:var(--red)">● Dead <b>${num(c.dead)}</b></span></div>
    <button class="btn primary" data-act="validateAll" ${v.running ? 'disabled' : ''}>${v.running ? 'Checking…' : 'Validate all sessions'}</button></div>
    ${v.running || v.total ? `<div class="prog"><i style="width:${v.total ? v.done / v.total * 100 : 0}%"></i></div><span class="muted">${v.running ? 'Checking' : 'Last check'}: ${v.done}/${v.total} · ${v.active} active · ${v.dead} dead · ${v.limited} limited</span>` : ''}</div>`;
  $('#sessTable').innerHTML = `<div class="row" style="justify-content:space-between"><div class="chips">${[['', 'All'], ['active', 'Active'], ['flood', 'Resting'], ['limited', 'Limited'], ['dead', 'Dead']].map(([k, l]) => `<button class="chip ${SS.status === k ? 'on' : ''}" data-act="sessFilter" data-s="${k}">${l}</button>`).join('')}</div>
    <button class="btn sm bad" data-act="sessDelSel">Remove selected (${SS.sel.size})</button></div>
    ${table(['<input type="checkbox" data-act="sessAll" aria-label="Select page">', 'Session', 'Status', 'API', 'Added', 'Checked', ''], d.items.map(s => `<tr>
     <td><input type="checkbox" data-act="sessSel" data-id="${esc(s.id)}" ${SS.sel.has(s.id) ? 'checked' : ''} aria-label="Select"></td>
     <td><b>${esc(s.label || s.username || s.id)}</b><br><span class="muted">${s.username ? '@' + esc(s.username) + ' · ' : ''}${esc(s.phone || s.id)}${s.proxy ? ' · 🌐 own proxy' : ''}</span></td>
     <td>${P(s.status)}${s.note ? `<div class="muted" style="font-size:12px;max-width:220px">${esc(s.note)}</div>` : ''}</td>
     <td><code>${esc(s.api_id)}</code></td><td>${when(s.added_at)}</td><td>${when(s.last_check)}</td>
     <td><div class="row"><button class="btn sm" data-act="sessCheck" data-id="${esc(s.id)}">Check</button><button class="btn sm" data-act="sessProxy" data-id="${esc(s.id)}">Proxy</button><button class="btn sm bad" data-act="sessDel" data-id="${esc(s.id)}">Remove</button></div></td></tr>`), 'No sessions yet. Drop some files above, or let admins paste strings from the bot.')}${pager(d.page, d.pages, 'sessPage')}`;
  clearInterval(timer);
  if (v.running) timer = setInterval(renderSessions, 2000);
}
async function uploadFiles(files) {
  if (!files.length) return;
  const log = $('#upLog'), bar = $('#upProg'); bar.hidden = false; log.innerHTML = ''; let ok = 0;
  for (let i = 0; i < files.length; i++) {
    const f = files[i], line = document.createElement('div'); line.textContent = '⏳ ' + f.name; log.prepend(line);
    try {
      const fd = new FormData(); fd.append('file', f);
      const r = await api('/sessions/upload', { method: 'POST', form: fd });
      line.textContent = (r.ok ? '✅ ' : '❌ ') + f.name + (r.ok ? (r.username ? ' · @' + r.username : '') : ' — ' + r.error); if (r.ok) ok++;
    } catch (e) { line.textContent = '❌ ' + f.name + ' — ' + e.message; }
    $('i', bar).style.width = ((i + 1) / files.length * 100) + '%';
  }
  toast(`${ok} of ${files.length} sessions imported`, ok === 0); renderSessions();
}
A.addStrings = async () => {
  const strings = $('#strIn').value.split('\n').map(x => x.trim()).filter(Boolean); if (!strings.length) return toast('Paste at least one string', true);
  const r = await api('/sessions/string', { method: 'POST', json: { strings } }), ok = r.results.filter(x => x.ok).length;
  $('#upLog').innerHTML = r.results.map((x, i) => `<div>${x.ok ? '✅' : '❌'} #${i + 1} ${x.ok ? esc(x.username || x.id) : esc(x.error)}</div>`).join('');
  toast(`${ok} of ${r.results.length} strings imported`, ok === 0); if (ok) $('#strIn').value = ''; renderSessions();
};
A.validateAll = async () => { await api('/sessions/validate', { method: 'POST', json: { ids: [] } }); toast('Checking all sessions…'); renderSessions(); };
A.sessCheck = async el => { await api('/sessions/validate', { method: 'POST', json: { ids: [el.dataset.id] } }); toast('Checking…'); setTimeout(renderSessions, 1500); };
A.sessFilter = el => { SS.status = el.dataset.s; SS.page = 1; return renderSessions(); };
A.sessPage = el => { SS.page = +el.dataset.page; return renderSessions(); };
A.sessSel = el => { el.checked ? SS.sel.add(el.dataset.id) : SS.sel.delete(el.dataset.id); $('[data-act=sessDelSel]').textContent = `Remove selected (${SS.sel.size})`; };
A.sessAll = el => { $$('[data-act=sessSel]').forEach(c => { c.checked = el.checked; A.sessSel(c); }); };
A.sessDel = async el => { if (!confirm('Remove this session from the active pool? (kept in the database, never deleted)')) return; await api('/sessions/delete', { method: 'POST', json: { ids: [el.dataset.id] } }); SS.sel.delete(el.dataset.id); toast('Session removed'); renderSessions(); };
A.sessDelSel = async () => { if (!SS.sel.size) return toast('Select sessions first', true); if (!confirm(`Remove ${SS.sel.size} sessions from the pool?`)) return; const r = await api('/sessions/delete', { method: 'POST', json: { ids: [...SS.sel] } }); SS.sel.clear(); toast(r.deleted + ' removed'); renderSessions(); };
A.sessProxy = async el => { const p = prompt('Proxy for this session (overrides the global rotating proxy)\nsocks5://user:pass@host:port  (leave empty to remove)', ''); if (p === null) return; await api(`/sessions/${encodeURIComponent(el.dataset.id)}/proxy`, { method: 'PUT', json: { proxy: p } }); toast('Proxy saved'); renderSessions(); };

/* ───────── ADMINS ───────── */
async function pageAdmins() {
  loading();
  const d = await api('/admins');
  view.innerHTML = `<div class="card"><div class="row" style="justify-content:space-between"><h3>👑 Owner</h3><code>${esc(d.owner_id)}</code></div></div><div class="spacer"></div>
  <div class="row" style="justify-content:space-between;margin-bottom:10px"><h3>Admins</h3><button class="btn primary sm" data-act="admAdd">+ Add admin</button></div>
  ${table(['Admin', 'Chat ID', 'Status', 'Added', ''], d.items.map(a => `<tr><td>${esc(a.username ? '@' + a.username : (a.name || 'Unknown'))}</td><td><code>${esc(a.id)}</code></td><td>${a.hidden ? P('removed', 'Removed') : P('active', 'Active')}</td><td>${when(a.added_at)}</td>
    <td>${a.hidden ? '' : `<button class="btn sm bad" data-act="admRemove" data-id="${esc(a.id)}">Remove</button>`}</td></tr>`), 'No admins granted yet — only the owner can use the bot.')}
  <p class="muted" style="margin-top:14px">Removed admins stay listed here for audit — nothing is ever deleted from the database. Their bot access is revoked immediately.</p>`;
}
A.admAdd = () => modal(`<h3>Grant bot access</h3><label>Telegram chat ID<input id="admId" type="number" placeholder="e.g. 123456789"></label><div class="row" style="justify-content:flex-end"><button class="btn" data-act="close">Cancel</button><button class="btn primary" data-act="admAddGo">Grant access</button></div>`);
A.admAddGo = async () => { const id = +$('#admId').value; if (!id) return toast('Enter a chat ID', true); await api('/admins', { method: 'POST', json: { id } }); toast('Access granted'); closeModal(); pageAdmins(); };
A.admRemove = async el => { if (!confirm('Revoke this admin\'s bot access?')) return; await api(`/admins/${encodeURIComponent(el.dataset.id)}/remove`, { method: 'POST' }); toast('Access revoked'); pageAdmins(); };

/* ───────── LOGS ───────── */
const LG = { page: 1, source: '', action: '' };
async function pageLogs() {
  loading();
  const d = await api(`/logs?page=${LG.page}&source=${LG.source}&action=${encodeURIComponent(LG.action)}`);
  view.innerHTML = `<div class="row" style="justify-content:space-between;margin-bottom:10px">
    <div class="chips">${[['', 'All sources'], ['bot', '🤖 Bot'], ['panel', '🌐 Panel'], ['engine', '⚙️ Engine']].map(([k, l]) => `<button class="chip ${LG.source === k ? 'on' : ''}" data-act="logSrc" data-s="${k}">${l}</button>`).join('')}</div>
    <div class="row"><input id="lgAction" placeholder="Filter by action" value="${esc(LG.action)}" style="max-width:200px"><button class="btn sm" data-act="logFilter">Filter</button></div></div>
  ${table(['Time', 'Who', 'Source', 'Action', 'Details', 'Channel'], d.items.map(l => `<tr><td style="white-space:nowrap">${when(l.ts)}</td><td>${l.role === 'owner' ? '👑' : l.role === 'admin' ? '🧑‍💼' : '⚙️'} ${l.uid ? '<code>' + esc(l.uid) + '</code>' : 'system'}</td><td>${esc(l.source || '')}</td><td><b>${esc(l.action)}</b></td><td class="muted" style="word-break:break-word">${esc(l.detail || '')}</td><td>${l.channel_id ? `<a href="#/channel/${esc(l.channel_id)}">${esc(l.channel_id)}</a>` : ''}</td></tr>`), 'No logs yet')}
  ${pager(d.page, d.pages, 'logPage')}<p class="muted" style="margin-top:10px">Logs are permanent — nothing here is ever trimmed or deleted.</p>`;
  $('#lgAction').addEventListener('keydown', e => { if (e.key === 'Enter') A.logFilter(); });
}
A.logSrc = el => { LG.source = el.dataset.s; LG.page = 1; return pageLogs(); };
A.logFilter = () => { LG.action = $('#lgAction').value.trim(); LG.page = 1; return pageLogs(); };
A.logPage = el => { LG.page = +el.dataset.page; return pageLogs(); };

/* ───────── SETTINGS ───────── */
const SAFE = { actions_per_hour: 'Actions per session / hour', joins_per_day: 'Joins per session / day', cooldown_sec: 'Cooldown between actions (sec)', max_clients: 'Simultaneous connections (RAM)', batch_max: 'Actions per tick', jitter_min: 'Random delay min (sec)', jitter_max: 'Random delay max (sec)', stream_cap: 'Max live-stream sessions', stream_max_minutes: 'Max stream duration (min)', stream_wait_minutes: 'Wait for stream to start (min)', stream_concurrency: 'Stream joins per tick' };
async function pageSettings() {
  loading();
  const s = await api('/settings');
  view.innerHTML = `<div class="grid g2">
   <div class="card"><h3>Owner account</h3><label>Username<input id="aUser" value="${esc(s.admin_username)}"></label><label>Current password<input id="aCur" type="password" autocomplete="current-password"></label><label>New password (8+ characters)<input id="aNew" type="password" autocomplete="new-password"></label><button class="btn primary" data-act="savePw">Change password</button></div>
   <div class="card"><h3>🌐 Rotating Proxy (e.g. IPRoyal)</h3><p class="muted" style="margin-bottom:10px">All sessions route through this when enabled.</p>
     <label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="pxEn" ${s.proxy.enabled ? 'checked' : ''}> Enabled</label>
     <label>Proxy URL<input id="pxUrl" value="${esc(s.proxy.url)}" placeholder="socks5://user:pass@gw.iproyal.com:12345"></label>
     <button class="btn primary" data-act="savePx">Save proxy</button></div>
   <div class="card"><h3>Telegram API ID &amp; hash</h3><p class="muted" style="margin-bottom:10px">New sessions are spread across these keys evenly.</p>
     ${s.apis.length ? s.apis.map(a => `<div class="kv"><span><code>${esc(a.id)}</code> · ${esc(a.hash)} · ${esc(a.sessions)} sessions</span><button class="btn sm bad" data-act="apiDel" data-id="${esc(a.id)}">Remove</button></div>`).join('') : '<p class="muted">None yet. Add one from my.telegram.org.</p>'}
     <div class="two" style="margin-top:12px"><label>API ID<input id="apiId" type="number"></label><label>API hash<input id="apiHash"></label></div><button class="btn primary" data-act="apiAdd">Add API</button></div>
   <div class="card"><h3>Session safety</h3><p class="muted" style="margin-bottom:12px">Caps applied to every session regardless of per-channel speed. Slower = safer.</p>
     <div class="three">${Object.entries(SAFE).map(([k, l]) => `<label>${l}<input data-safe="${k}" type="number" step="any" min="0" value="${esc(s.safety[k])}"></label>`).join('')}</div>
     <button class="btn primary" data-act="saveEngine">Save safety settings</button></div></div>`;
}
A.savePw = async () => { await api('/settings/password', { method: 'POST', json: { current: $('#aCur').value, new: $('#aNew').value, username: $('#aUser').value } }); toast('Password changed'); $('#aCur').value = $('#aNew').value = ''; };
A.savePx = async () => { await api('/settings/proxy', { method: 'PUT', json: { enabled: $('#pxEn').checked, url: $('#pxUrl').value.trim() } }); toast('Proxy saved'); };
A.apiAdd = async () => {
  const cur = (await api('/settings')).apis.map(a => ({ id: a.id, hash: '' })), id = +$('#apiId').value, hash = $('#apiHash').value.trim();
  if (!id || !hash) return toast('Enter both API ID and hash', true);
  await api('/settings/apis', { method: 'PUT', json: { apis: [...cur.filter(a => a.id !== id), { id, hash }] } }); toast('API added'); pageSettings();
};
A.apiDel = async el => { if (!confirm('Remove this API? Existing sessions switch to another key.')) return; const cur = (await api('/settings')).apis.filter(a => a.id !== +el.dataset.id).map(a => ({ id: a.id, hash: '' })); await api('/settings/apis', { method: 'PUT', json: { apis: cur } }); toast('API removed'); pageSettings(); };
A.saveEngine = async () => {
  const safety = {}; $$('[data-safe]').forEach(i => safety[i.dataset.safe] = +i.value);
  await api('/settings/engine', { method: 'PUT', json: { safety } }); toast('Safety settings saved');
};

boot();
