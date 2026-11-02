'use strict';
/* Telegram SMM admin panel - vanilla JS SPA. All user-controlled text goes through esc(). */
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const money = n => '₹' + Number(n || 0).toLocaleString('en-IN', { maximumFractionDigits: 2 });
const num = n => Number(n || 0).toLocaleString('en-IN');
const when = ts => ts ? new Date(ts * 1000).toLocaleString(undefined, { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
const today = () => new Date().toISOString().slice(0, 10);
const SV = { subscribers: ['👥', 'Subscribers'], views: ['👁', 'Views'], reactions: ['❤️', 'Reactions'], stream: ['🔴', 'Live Stream'] };
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
const pill = s => ({ queued: 'yellow', running: 'blue', completed: 'green', partial: 'orange', failed: 'red', cancelled: 'gray', pending: 'yellow', approved: 'green', rejected: 'red', active: 'green', dead: 'red', limited: 'orange', flood: 'yellow', new: 'gray', done: 'green', deleted: 'gray', deleting: 'orange' }[s] || 'gray');
const P = (s, label) => `<span class="pill ${pill(s)}"><i class="dot"></i>${esc(label || s)}</span>`;
const pager = (page, pages, act) => pages <= 1 ? '' : `<div class="pager"><button class="btn sm" data-act="${act}" data-page="${page - 1}" ${page <= 1 ? 'disabled' : ''}>← Prev</button><span class="muted">Page ${page} of ${pages}</span><button class="btn sm" data-act="${act}" data-page="${page + 1}" ${page >= pages ? 'disabled' : ''}>Next →</button></div>`;
const table = (cols, rows, empty = 'Nothing here yet') => rows.length ? `<div class="tw"><table><thead><tr>${cols.map(c => `<th>${c}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>` : `<div class="card empty">${empty}</div>`;
const qs = R => `range=${R.range}&frm=${R.frm || ''}&to=${R.to || ''}`;
function rangeBar(R, act) {
  const chips = [['today', 'Today'], ['yesterday', 'Yesterday'], ['all', 'All time'], ['custom', 'Custom range']];
  return `<div class="chips">${chips.map(([k, l]) => `<button class="chip ${R.range === k ? 'on' : ''}" data-act="${act}" data-range="${k}">${l}</button>`).join('')}</div>` +
    (R.range === 'custom' ? `<div class="dates" style="margin-bottom:14px"><input type="date" data-k="frm" value="${esc(R.frm)}" aria-label="From"><span class="muted">to</span><input type="date" data-k="to" value="${esc(R.to)}" aria-label="To"><button class="btn sm primary" data-act="${act}" data-range="custom" data-apply="1">Apply</button></div>` : '');
}
function setRange(R, el) {
  R.range = el.dataset.range; R.page = 1;
  if (R.range === 'custom') {
    if (el.dataset.apply) { R.frm = $('[data-k=frm]').value; R.to = $('[data-k=to]').value || R.frm; if (!R.frm) { toast('Pick a start date', true); return false; } }
    else if (!R.frm) R.frm = R.to = today();
  }
  return true;
}
const userCell = u => `<b>${esc(u.name || 'User')}</b><br><span class="muted">${u.username ? '@' + esc(u.username) + ' · ' : ''}<code>${esc(u.id)}</code></span>`;
const planCell = o => `${SV[o.service][0]} ${num(o.qty)} <span class="muted">${esc(SV[o.service][1])}${o.plan_id === 'custom' ? ' · custom' : ''}</span>`;
function orderRows(items, withCancel) {
  return items.map(o => `<tr><td><b>#${o.n}</b></td><td>${planCell(o)}</td><td>${money(o.price)}</td><td>${userCell(o.user)}</td>
    <td>${P(o.status)}${o.note ? `<div class="muted" style="font-size:12px;max-width:220px">${esc(o.note)}</div>` : ''}</td>
    <td>${num(o.delivered)} / ${num(o.qty)}</td><td>${when(o.created_at)}</td>
    ${withCancel ? `<td>${['queued', 'running'].includes(o.status) ? `<button class="btn sm bad" data-act="cancelOrder" data-id="${esc(o.id)}">Cancel</button>` : ''}</td>` : ''}</tr>`);
}
const ORDER_COLS = ['Order', 'Plan', 'Price', 'Bought by', 'Status', 'Delivered', 'When'];

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

const PAGES = { home: ['Home', pageHome], users: ['Users', pageUsers], plans: ['Plans', pagePlans], payments: ['Payments', pagePayments], broadcast: ['Broadcast', pageBroadcast], sessions: ['Session', pageSessions], logs: ['Logs', pageLogs], settings: ['Setting', pageSettings] };
async function route() {
  clearInterval(timer); document.body.classList.remove('menu'); closeModal();
  const p = (location.hash.replace('#/', '') || 'home').split('?')[0], pg = PAGES[p] || PAGES.home;
  $('#pageTitle').textContent = pg[0]; document.title = pg[0] + ' · Telegram SMM';
  $$('#nav a').forEach(a => a.classList.toggle('on', a.dataset.p === (p === 'users' ? 'home' : p)));
  try { await pg[1](); view.focus({ preventScroll: true }); } catch (err) { if (err.message !== 'Please log in again') view.innerHTML = `<div class="card empty">😵 ${esc(err.message)}<br><br><button class="btn" onclick="location.reload()">Reload</button></div>`; }
}
async function boot() {
  try { me = await api('/me'); } catch { return showLogin(); }
  $('#login').hidden = true; $('#app').hidden = false; $('#who').textContent = '👋 ' + me.username;
  route();
}
window.addEventListener('hashchange', route);

/* ───────── HOME ───────── */
const H = { range: 'today', frm: '', to: '', page: 1, service: '', status: '' };
async function pageHome() {
  loading();
  const q = qs(H);
  const [d, o] = await Promise.all([api('/dashboard?' + q), api(`/orders?${q}&service=${H.service}&status=${H.status}&page=${H.page}`)]);
  const a = d.agg, e = d.engine || {}, s = e.sessions || {};
  const mx = Math.max(1, ...d.chart.map(c => c.earning)), sm = Math.max(1, ...Object.keys(SV).map(k => a['orders_' + k] || 0));
  const lbl = { today: 'today', yesterday: 'yesterday', all: 'all time', custom: 'selected dates' }[H.range];
  view.innerHTML = `
  ${rangeBar(H, 'homeRange')}
  <div class="grid g4">
    <button class="stat" data-act="gotoUsers"><span>Total users</span><b>${num(d.users)}</b><small>${num(a.users)} new · ${lbl} · tap to view</small></button>
    <div class="stat"><span>Total earning</span><b>${money(a.earning)}</b><small>${num(a.deposits)} approved deposits · ${lbl}</small></div>
    <div class="stat"><span>Plans purchased</span><b>${num(a.orders)}</b><small>${lbl}</small></div>
    <div class="stat"><span>Spent on orders</span><b>${money(a.spent)}</b><small>after refunds · ${lbl}</small></div>
  </div><div class="spacer"></div>
  <div class="grid g2">
    <div class="card"><h3>Earning · last 14 days</h3><div class="bars">${d.chart.map(c => `<div title="${c.day}: ${money(c.earning)}"><i style="height:${Math.max(3, c.earning / mx * 100)}%"></i>${c.day.slice(8)}</div>`).join('')}</div></div>
    <div class="card"><h3>Which plans sell</h3>${Object.keys(SV).map(k => `<div class="hbar"><div class="kv" style="border:0;padding:0 0 4px"><span>${SV[k][0]} ${SV[k][1]}</span><b>${num(a['orders_' + k])}</b></div><div><i style="width:${(a['orders_' + k] || 0) / sm * 100}%"></i></div></div>`).join('')}</div>
  </div><div class="spacer"></div>
  <div class="card"><h3>Engine right now</h3><div class="row" style="gap:22px">
    <span>🧩 Active sessions <b>${num(s.active)}</b> / ${num(s.total)}</span><span>🟡 Resting <b>${num(s.flood)}</b></span><span>💀 Dead <b>${num(s.dead)}</b></span>
    <span>⚙️ Running orders <b>${num(e.running_orders)}</b></span><span>🔴 Live connections <b>${num(e.live_connections)}</b></span></div></div>
  <div class="spacer"></div><h3 style="margin-bottom:10px">Purchases</h3>
  <div class="chips">${[['', 'All'], ...Object.entries(SV).map(([k, v]) => [k, v[0] + ' ' + v[1]])].map(([k, l]) => `<button class="chip ${H.service === k ? 'on' : ''}" data-act="homeService" data-s="${k}">${l}</button>`).join('')}</div>
  ${table([...ORDER_COLS, ''], orderRows(o.items, true), 'No purchases in this range')}${pager(o.page, o.pages, 'homePage')}`;
  if (e.running_orders) timer = setInterval(() => { if (!document.querySelector('.ov')) pageHome(); }, 15000);
}
A.homeRange = el => { if (setRange(H, el)) return pageHome(); };
A.homeService = el => { H.service = el.dataset.s; H.page = 1; return pageHome(); };
A.homePage = el => { H.page = +el.dataset.page; return pageHome(); };
A.gotoUsers = () => { location.hash = '#/users'; };
A.cancelOrder = async el => { if (!confirm('Cancel this order? Undelivered part is refunded to the user.')) return; await api(`/orders/${el.dataset.id}/cancel`, { method: 'POST' }); toast('Order cancelled'); setTimeout(route, 1500); };

/* ───────── USERS ───────── */
const U = { page: 1, q: '' };
async function pageUsers() {
  loading();
  const d = await api(`/users?page=${U.page}&q=${encodeURIComponent(U.q)}`);
  view.innerHTML = `<div class="row" style="margin-bottom:14px"><a class="btn sm" href="#/home">← Dashboard</a>
    <input id="uq" placeholder="Search by chat ID" value="${esc(U.q)}" style="max-width:260px"><button class="btn sm primary" data-act="userSearch">Search</button>
    <span class="muted">${num(d.total)} users</span></div>
    ${table(['User', 'Joined', 'Balance', 'Status'], d.items.map(u => `<tr class="click" data-act="userOpen" data-id="${u.id}"><td>${userCell(u)}</td><td>${when(u.joined_at)}</td><td>${money(u.balance)}</td><td>${u.banned ? P('rejected', 'Banned') : P('active', 'OK')}</td></tr>`), 'No users found')}${pager(d.page, d.pages, 'userPage')}`;
  $('#uq').addEventListener('keydown', e => { if (e.key === 'Enter') A.userSearch(); });
}
A.userSearch = () => { U.q = $('#uq').value.trim(); U.page = 1; return pageUsers(); };
A.userPage = el => { U.page = +el.dataset.page; return pageUsers(); };
A.userOpen = async el => {
  const d = await api('/users/' + el.dataset.id), u = d.user;
  modal(`<h3>${esc(u.name)} ${d.banned ? P('rejected', 'Banned') : ''}</h3>
    <div class="kv"><span>Chat ID</span><code>${u.id}</code></div><div class="kv"><span>Username</span><span>${u.username ? '@' + esc(u.username) : '—'}</span></div>
    <div class="kv"><span>Joined</span><span>${when(u.joined_at)}</span></div><div class="kv"><span>Wallet</span><b>${money(d.balance)}</b></div>
    <div class="kv"><span>Channel</span><span>${d.channel ? esc(d.channel.title) : '—'}</span></div>
    <div class="row" style="margin:14px 0"><button class="btn ${d.banned ? 'good' : 'bad'}" data-act="userBan" data-id="${u.id}" data-ban="${d.banned ? 0 : 1}">${d.banned ? 'Unban user' : 'Ban user'}</button></div>
    <label>Add / remove balance (use − to deduct)<div class="row" style="flex-wrap:nowrap"><input id="balAmt" type="number" step="0.01" placeholder="e.g. 100 or -50"><button class="btn primary" data-act="userBal" data-id="${u.id}">Apply</button></div></label>
    <label>Send a message<div class="row" style="flex-wrap:nowrap"><input id="msgTxt" placeholder="Type message"><button class="btn" data-act="userMsg" data-id="${u.id}">Send</button></div></label>
    <h3 style="margin-top:8px">Recent orders</h3>${d.orders.length ? d.orders.map(o => `<div class="kv"><span>#${o.n} ${planCell(o)}</span><span>${P(o.status)}</span></div>`).join('') : '<p class="muted">None yet</p>'}
    <div class="row" style="margin-top:16px;justify-content:flex-end"><button class="btn" data-act="close">Close</button></div>`);
};
A.close = closeModal;
A.userBan = async el => { await api(`/users/${el.dataset.id}/ban`, { method: 'POST', json: { banned: el.dataset.ban === '1' } }); toast('Done'); closeModal(); pageUsers(); };
A.userBal = async el => { const v = parseFloat($('#balAmt').value); if (!v) return toast('Enter an amount', true); const r = await api(`/users/${el.dataset.id}/balance`, { method: 'POST', json: { amount: v } }); toast('New balance ' + money(r.balance)); closeModal(); pageUsers(); };
A.userMsg = async el => { const t = $('#msgTxt').value.trim(); if (!t) return; await api(`/users/${el.dataset.id}/message`, { method: 'POST', json: { text: t } }); toast('Message sent'); $('#msgTxt').value = ''; };

/* ───────── PLANS ───────── */
const PL = { svc: 'subscribers' };
async function pagePlans() {
  loading();
  const all = await api('/plans'), d = all[PL.svc], pr = d.pricing;
  view.innerHTML = `<div class="tabs">${Object.entries(SV).map(([k, v]) => `<button class="chip ${PL.svc === k ? 'on' : ''}" data-act="planTab" data-s="${k}">${v[0]} ${v[1]}</button>`).join('')}</div>
  <div class="card"><h3>Price for 1 ${esc(SV[PL.svc][1].toLowerCase())} <span class="muted" style="font-size:14px">(used for custom quantities)</span></h3>
    <div class="three"><label>Price per 1 (₹)<input id="pUnit" type="number" step="0.01" min="0" value="${pr.unit}"></label><label>Min quantity<input id="pMin" type="number" min="1" value="${pr.min}"></label><label>Max quantity<input id="pMax" type="number" min="1" value="${pr.max}"></label></div>
    <label style="display:flex;gap:8px;align-items:center"><input id="pRecalc" type="checkbox"> Also recalculate all plan prices below</label>
    <button class="btn primary" data-act="savePricing">Save pricing</button></div><div class="spacer"></div>
  <div class="row" style="justify-content:space-between;margin-bottom:10px"><h3>Plans</h3><button class="btn primary" data-act="planForm" data-id="">+ Add plan</button></div>
  ${table(['Quantity', 'Price', 'Visible', 'Bought', ''], d.plans.map(p => `<tr><td><b>${num(p.qty)}</b></td><td>${money(p.price)}</td>
    <td><input type="checkbox" data-act="planToggle" data-id="${p.id}" data-qty="${p.qty}" data-price="${p.price}" ${p.active ? 'checked' : ''} aria-label="Visible"></td>
    <td><button class="btn sm" data-act="planBuyers" data-id="${p.id}">${num(p.purchases)} orders</button></td>
    <td class="row"><button class="btn sm" data-act="planForm" data-id="${p.id}" data-qty="${p.qty}" data-price="${p.price}" data-active="${p.active ? 1 : 0}">Edit</button><button class="btn sm bad" data-act="planDel" data-id="${p.id}">Delete</button></td></tr>`), 'No plans yet. Add your first one!')}
  <div class="spacer"></div><div class="card row" style="justify-content:space-between"><span>Custom quantity orders</span><button class="btn sm" data-act="planBuyers" data-id="custom">${num(d.custom_purchases)} orders</button></div>`;
}
A.planTab = el => { PL.svc = el.dataset.s; return pagePlans(); };
A.savePricing = async () => {
  await api('/pricing/' + PL.svc, { method: 'PUT', json: { unit: +$('#pUnit').value, min: +$('#pMin').value, max: +$('#pMax').value, recalc: $('#pRecalc').checked } });
  toast('Pricing saved'); pagePlans();
};
A.planForm = el => {
  const id = el.dataset.id;
  modal(`<h3>${id ? 'Edit plan' : 'Add plan'}</h3><div class="two"><label>Quantity<input id="fQty" type="number" min="1" value="${esc(el.dataset.qty || '')}"></label><label>Price (₹)<input id="fPrice" type="number" step="0.01" min="0" value="${esc(el.dataset.price || '')}"></label></div>
  <label style="display:flex;gap:8px;align-items:center"><input id="fAct" type="checkbox" ${el.dataset.active === '0' ? '' : 'checked'}> Visible to users</label>
  <div class="row" style="justify-content:flex-end"><button class="btn" data-act="close">Cancel</button><button class="btn primary" data-act="planSave" data-id="${esc(id)}">Save plan</button></div>`);
};
A.planSave = async el => {
  const body = { qty: +$('#fQty').value, price: +$('#fPrice').value, active: $('#fAct').checked };
  await api(`/plans/${PL.svc}${el.dataset.id ? '/' + el.dataset.id : ''}`, { method: el.dataset.id ? 'PUT' : 'POST', json: body });
  toast('Plan saved'); closeModal(); pagePlans();
};
A.planToggle = async el => { await api(`/plans/${PL.svc}/${el.dataset.id}`, { method: 'PUT', json: { qty: +el.dataset.qty, price: +el.dataset.price, active: el.checked } }); toast(el.checked ? 'Plan visible' : 'Plan hidden'); };
A.planDel = async el => { if (!confirm('Delete this plan? Past orders are kept.')) return; await api(`/plans/${PL.svc}/${el.dataset.id}`, { method: 'DELETE' }); toast('Plan deleted'); pagePlans(); };
A.planBuyers = async el => {
  const id = el.dataset.id, pg = +el.dataset.page || 1, d = await api(`/plans/${PL.svc}/${id}/orders?page=${pg}`);
  modal(`<h3>${id === 'custom' ? 'Custom quantity orders' : 'Orders for this plan'}</h3>${table(ORDER_COLS, orderRows(d.items), 'Nobody bought this yet')}
   ${d.pages > 1 ? `<div class="pager"><button class="btn sm" data-act="planBuyers" data-id="${esc(id)}" data-page="${d.page - 1}" ${d.page <= 1 ? 'disabled' : ''}>← Prev</button><span class="muted">${d.page}/${d.pages}</span><button class="btn sm" data-act="planBuyers" data-id="${esc(id)}" data-page="${d.page + 1}" ${d.page >= d.pages ? 'disabled' : ''}>Next →</button></div>` : ''}
   <div class="row" style="justify-content:flex-end;margin-top:14px"><button class="btn" data-act="close">Close</button></div>`, true);
};

/* ───────── PAYMENTS ───────── */
const PY = { range: 'all', frm: '', to: '', page: 1, status: '' };
async function pagePayments() {
  loading();
  const d = await api(`/payments?${qs(PY)}&status=${PY.status}&page=${PY.page}`), s = d.summary;
  view.innerHTML = `${rangeBar(PY, 'payRange')}
  <div class="grid g4" style="grid-template-columns:repeat(auto-fit,minmax(220px,1fr))">
    <div class="card"><span class="muted">Pending approval</span><h3 style="margin:4px 0 0;font-size:26px">${num(s.pending.n)} · ${money(s.pending.sum)}</h3></div>
    <div class="card"><span class="muted">Approved</span><h3 style="margin:4px 0 0;font-size:26px;color:var(--mint)">${num(s.approved.n)} · ${money(s.approved.sum)}</h3></div>
    <div class="card"><span class="muted">Failed</span><h3 style="margin:4px 0 0;font-size:26px;color:var(--red)">${num(s.rejected.n)} · ${money(s.rejected.sum)}</h3></div></div><div class="spacer"></div>
  <div class="chips">${[['', 'All'], ['pending', 'Pending'], ['approved', 'Approved'], ['rejected', 'Failed']].map(([k, l]) => `<button class="chip ${PY.status === k ? 'on' : ''}" data-act="payStatus" data-s="${k}">${l}</button>`).join('')}</div>
  ${table(['Request', 'User', 'Amount', 'Status', 'Proof', 'When', ''], d.items.map(p => `<tr><td><b>#${p.n}</b></td><td>${userCell({ id: p.user_id, name: p.name, username: p.username })}</td><td><b>${money(p.amount)}</b></td>
   <td>${P(p.status, p.status === 'rejected' ? 'failed' : p.status)}${p.reason ? `<div class="muted" style="font-size:12px">${esc(p.reason)}</div>` : ''}</td>
   <td><img class="thumb" loading="lazy" alt="Payment proof" src="/api/payments/${esc(p.id)}/proof" data-act="proofView" data-id="${esc(p.id)}"></td><td>${when(p.created_at)}</td>
   <td>${p.status === 'pending' ? `<div class="row"><button class="btn sm good" data-act="payOk" data-id="${esc(p.id)}" data-amt="${p.amount}">Approve</button><button class="btn sm bad" data-act="payNo" data-id="${esc(p.id)}">Reject</button></div>` : ''}</td></tr>`), 'No payments in this view')}${pager(d.page, d.pages, 'payPage')}`;
}
A.payRange = el => { if (setRange(PY, el)) return pagePayments(); };
A.payStatus = el => { PY.status = el.dataset.s; PY.page = 1; return pagePayments(); };
A.payPage = el => { PY.page = +el.dataset.page; return pagePayments(); };
A.proofView = el => modal(`<h3>Payment proof</h3><img class="big" alt="Payment proof" src="/api/payments/${esc(el.dataset.id)}/proof"><div class="row" style="justify-content:flex-end;margin-top:14px"><button class="btn" data-act="close">Close</button></div>`);
A.payOk = async el => { if (!confirm(`Approve ${money(el.dataset.amt)} and credit the user's wallet?`)) return; await api(`/payments/${el.dataset.id}/approve`, { method: 'POST' }); toast('Approved · wallet credited'); pagePayments(); };
A.payNo = el => modal(`<h3>Reject payment</h3><label>Reason (the user will see this)<textarea id="rejWhy" placeholder="e.g. Screenshot is unclear / amount doesn't match"></textarea></label><div class="row" style="justify-content:flex-end"><button class="btn" data-act="close">Cancel</button><button class="btn bad" data-act="payNoGo" data-id="${esc(el.dataset.id)}">Reject payment</button></div>`);
A.payNoGo = async el => { await api(`/payments/${el.dataset.id}/reject`, { method: 'POST', json: { reason: $('#rejWhy').value } }); toast('Rejected · user notified'); closeModal(); pagePayments(); };

/* ───────── BROADCAST ───────── */
const BC = { range: 'all', frm: '', to: '', page: 1 };
async function pageBroadcast() {
  loading();
  const d = await api(`/broadcasts?${qs(BC)}&page=${BC.page}`);
  view.innerHTML = `<div class="card"><h3>New broadcast</h3>
    <label>Message (HTML like &lt;b&gt;bold&lt;/b&gt; works)<textarea id="bcText" placeholder="Write something your users will love…"></textarea></label>
    <label>Image, video or file (optional)<input id="bcFile" type="file"></label>
    <button class="btn primary" data-act="bcSend">Send to all users 🚀</button></div><div class="spacer"></div>
  ${rangeBar(BC, 'bcRange')}
  ${d.items.length ? d.items.map(b => `<div class="card bc" style="margin-bottom:12px"><div style="flex:1;min-width:220px"><div class="row">${P(b.status)} <span class="muted">${esc(b.kind)} · ${when(b.created_at)}${b.origin ? ' · resend of #' + Number(b.origin) : ''}</span></div>
     <p>${esc(b.preview)}</p><div class="muted">👥 received <b style="color:var(--text)">${num(b.sent)}</b> of ${num(b.total)} · 🚫 blocked ${num(b.blocked)} · ❌ failed ${num(b.failed)}</div></div>
     <div class="row" style="align-content:flex-start"><button class="btn sm" data-act="bcResend" data-id="${esc(b.id)}" ${b.status === 'deleted' ? 'disabled' : ''}>Rebroadcast</button>
     <button class="btn sm" data-act="bcEdit" data-id="${esc(b.id)}" data-text="${esc(b.preview)}" ${['text', 'photo', 'video', 'document', 'animation'].includes(b.kind) && b.status === 'done' ? '' : 'disabled'}>Edit</button>
     <button class="btn sm bad" data-act="bcDel" data-id="${esc(b.id)}" ${['deleted', 'deleting'].includes(b.status) ? 'disabled' : ''}>Delete</button></div></div>`).join('') : '<div class="card empty">No broadcasts in this range yet. Send your first one above!</div>'}
  ${pager(d.page, d.pages, 'bcPage')}`;
  if (d.items.some(b => ['running', 'deleting'].includes(b.status))) timer = setInterval(pageBroadcast, 6000);
}
A.bcRange = el => { if (setRange(BC, el)) return pageBroadcast(); };
A.bcPage = el => { BC.page = +el.dataset.page; return pageBroadcast(); };
A.bcSend = async () => {
  const text = $('#bcText').value, f = $('#bcFile').files[0];
  if (!text.trim() && !f) return toast('Write a message or attach a file', true);
  if (!confirm('Send this to ALL users now?')) return;
  const fd = new FormData(); fd.append('text', text); if (f) fd.append('file', f);
  await api('/broadcasts', { method: 'POST', form: fd }); toast('Broadcast started'); BC.page = 1; pageBroadcast();
};
A.bcResend = async el => { if (!confirm('Send this broadcast to everyone again?')) return; await api(`/broadcasts/${el.dataset.id}/rebroadcast`, { method: 'POST' }); toast('Rebroadcast started'); pageBroadcast(); };
A.bcEdit = el => modal(`<h3>Edit broadcast</h3><p class="muted">Updates the text on every message already delivered.</p><label>New text<textarea id="bcNew">${esc(el.dataset.text)}</textarea></label><div class="row" style="justify-content:flex-end"><button class="btn" data-act="close">Cancel</button><button class="btn primary" data-act="bcEditGo" data-id="${esc(el.dataset.id)}">Update everywhere</button></div>`);
A.bcEditGo = async el => { await api(`/broadcasts/${el.dataset.id}`, { method: 'PUT', json: { text: $('#bcNew').value } }); toast('Editing delivered messages…'); closeModal(); pageBroadcast(); };
A.bcDel = async el => { if (!confirm('Delete this broadcast from every user’s chat?')) return; await api(`/broadcasts/${el.dataset.id}`, { method: 'DELETE' }); toast('Deleting…'); pageBroadcast(); };

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
  $('#sessTop').innerHTML = `<div class="card" style="margin-bottom:16px"><div class="row" style="justify-content:space-between"><div class="row" style="gap:18px"><span>🧩 Total <b>${num(c.total)}</b></span><span class="ok">● Active <b>${num(c.active)}</b></span><span style="color:var(--sun)">● Resting <b>${num(c.flood)}</b></span><span style="color:var(--orange,#ffa45c)">● Limited <b>${num(c.limited)}</b></span><span style="color:var(--red)">● Dead <b>${num(c.dead)}</b></span></div>
    <button class="btn primary" data-act="validateAll" ${v.running ? 'disabled' : ''}>${v.running ? 'Checking…' : 'Validate all sessions'}</button></div>
    ${v.running || v.total ? `<div class="prog"><i style="width:${v.total ? v.done / v.total * 100 : 0}%"></i></div><span class="muted">${v.running ? 'Checking' : 'Last check'}: ${v.done}/${v.total} · ${v.active} active · ${v.dead} dead · ${v.limited} limited</span>` : ''}</div>`;
  $('#sessTable').innerHTML = `<div class="row" style="justify-content:space-between"><div class="chips">${[['', 'All'], ['active', 'Active'], ['flood', 'Resting'], ['limited', 'Limited'], ['dead', 'Dead']].map(([k, l]) => `<button class="chip ${SS.status === k ? 'on' : ''}" data-act="sessFilter" data-s="${k}">${l}</button>`).join('')}</div>
    <button class="btn sm bad" data-act="sessDelSel">Delete selected (${SS.sel.size})</button></div>
    ${table(['<input type="checkbox" data-act="sessAll" aria-label="Select page">', 'Session', 'Status', 'API', 'Actions/hr', 'Added', 'Checked', ''], d.items.map(s => `<tr>
     <td><input type="checkbox" data-act="sessSel" data-id="${esc(s.id)}" ${SS.sel.has(s.id) ? 'checked' : ''} aria-label="Select"></td>
     <td><b>${esc(s.label || s.username || s.id)}</b><br><span class="muted">${s.username ? '@' + esc(s.username) + ' · ' : ''}${esc(s.phone || s.id)}${s.proxy ? ' · 🌐' : ''}</span></td>
     <td>${P(s.status)}${s.note ? `<div class="muted" style="font-size:12px;max-width:220px">${esc(s.note)}</div>` : ''}${s.status === 'flood' ? `<div class="muted" style="font-size:12px">until ${when(s.flood_until)}</div>` : ''}</td>
     <td><code>${esc(s.api_id)}</code></td><td>${s.actions_hr}</td><td>${when(s.added_at)}</td><td>${when(s.last_check)}</td>
     <td><div class="row"><button class="btn sm" data-act="sessCheck" data-id="${esc(s.id)}">Check</button><button class="btn sm" data-act="sessProxy" data-id="${esc(s.id)}">Proxy</button><button class="btn sm bad" data-act="sessDel" data-id="${esc(s.id)}">Delete</button></div></td></tr>`), 'No sessions yet. Drop some files above!')}${pager(d.page, d.pages, 'sessPage')}`;
  clearInterval(timer);
  if (v.running) timer = setInterval(renderSessions, 2000);
}
async function uploadFiles(files) {
  if (!files.length) return;
  const log = $('#upLog'), bar = $('#upProg'); bar.hidden = false; log.innerHTML = ''; let ok = 0;
  for (let i = 0; i < files.length; i++) {  // one by one: each is checked against Telegram, then saved
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
A.sessSel = el => { el.checked ? SS.sel.add(el.dataset.id) : SS.sel.delete(el.dataset.id); $('[data-act=sessDelSel]').textContent = `Delete selected (${SS.sel.size})`; };
A.sessAll = el => { $$('[data-act=sessSel]').forEach(c => { c.checked = el.checked; A.sessSel(c); }); };
A.sessDel = async el => { if (!confirm('Delete this session from the database?')) return; await api('/sessions/delete', { method: 'POST', json: { ids: [el.dataset.id] } }); SS.sel.delete(el.dataset.id); toast('Session deleted'); renderSessions(); };
A.sessDelSel = async () => { if (!SS.sel.size) return toast('Select sessions first', true); if (!confirm(`Delete ${SS.sel.size} sessions?`)) return; const r = await api('/sessions/delete', { method: 'POST', json: { ids: [...SS.sel] } }); SS.sel.clear(); toast(r.deleted + ' deleted'); renderSessions(); };
A.sessProxy = async el => { const p = prompt('Proxy for this session\nsocks5://user:pass@host:port  (leave empty to remove)', ''); if (p === null) return; await api(`/sessions/${el.dataset.id}/proxy`, { method: 'PUT', json: { proxy: p } }); toast('Proxy saved'); renderSessions(); };

/* ───────── LOGS ───────── */
const LG = { source: 'bot', page: 1 };
async function pageLogs() {
  loading();
  const d = await api(`/logs?source=${LG.source}&page=${LG.page}`);
  view.innerHTML = `<div class="tabs">${[['bot', '🤖 Bot users'], ['admin', '🛠 Admin panel']].map(([k, l]) => `<button class="chip ${LG.source === k ? 'on' : ''}" data-act="logTab" data-s="${k}">${l}</button>`).join('')}</div>
  ${table(['Time', LG.source === 'bot' ? 'User' : 'Admin', 'Action', 'Details'], d.items.map(l => `<tr><td style="white-space:nowrap">${when(l.ts)}</td><td>${l.uid ? '<code>' + esc(l.uid) + '</code>' : '—'}</td><td><b>${esc(l.action)}</b></td><td class="muted" style="word-break:break-word">${esc(l.detail)}</td></tr>`), 'No logs yet')}
  <div class="pager"><button class="btn sm" data-act="logPage" data-page="${d.page - 1}" ${d.page <= 1 ? 'disabled' : ''}>← Newer</button><span class="muted">Page ${d.page}</span><button class="btn sm" data-act="logPage" data-page="${d.page + 1}" ${d.has_next ? '' : 'disabled'}>Older →</button></div>`;
}
A.logTab = el => { LG.source = el.dataset.s; LG.page = 1; return pageLogs(); };
A.logPage = el => { LG.page = +el.dataset.page; return pageLogs(); };

/* ───────── SETTINGS ───────── */
const SAFE = { actions_per_hour: 'Actions per session / hour', joins_per_day: 'Joins per session / day', cooldown_sec: 'Cooldown between actions (sec)', max_clients: 'Simultaneous connections (RAM)', batch_max: 'Actions per step', jitter_min: 'Random delay min (sec)', jitter_max: 'Random delay max (sec)', stall_minutes: 'Give up after idle (min)', stream_cap: 'Max live-stream sessions', stream_max_minutes: 'Max stream duration (min)', stream_wait_minutes: 'Wait for stream to start (min)', stream_concurrency: 'Stream joins at once' };
const SPEED = { subscribers: 'Subscribers · sec per unit', views: 'Views · sec per unit (fastest)', reactions: 'Reactions · sec per unit', stream: 'Stream · sec per join' };
async function pageSettings() {
  loading();
  const s = await api('/settings');
  view.innerHTML = `<div class="grid g2">
   <div class="card"><h3>Admin account</h3><label>Username<input id="aUser" value="${esc(s.admin_username)}"></label><label>Current password<input id="aCur" type="password" autocomplete="current-password"></label><label>New password (8+ characters)<input id="aNew" type="password" autocomplete="new-password"></label><button class="btn primary" data-act="savePw">Change password</button></div>
   <div class="card"><h3>Payments</h3><label>UPI ID<input id="gUpi" value="${esc(s.upi_id)}" placeholder="name@bank"></label><label>Support username<input id="gSup" value="${esc(s.support_username)}" placeholder="@yourusername"></label><button class="btn primary" data-act="saveGeneral">Save</button>
     <hr style="border:0;border-top:2px dashed var(--line);margin:18px 0"><label>QR code image<input id="qrIn" type="file" accept="image/*"></label><button class="btn" data-act="saveQr">Upload QR</button>${s.has_qr ? `<br><img class="qr" alt="Current QR" src="/api/settings/qr?t=${Date.now()}">` : '<p class="muted">No QR uploaded yet.</p>'}</div>
   <div class="card"><h3>Telegram API ID &amp; hash</h3><p class="muted" style="margin-bottom:10px">New sessions are spread across these keys evenly.</p>
     ${s.apis.length ? s.apis.map(a => `<div class="kv"><span><code>${esc(a.id)}</code> · ${esc(a.hash)} · ${a.sessions} sessions</span><button class="btn sm bad" data-act="apiDel" data-id="${a.id}">Remove</button></div>`).join('') : '<p class="muted">None yet. Add one from my.telegram.org.</p>'}
     <div class="two" style="margin-top:12px"><label>API ID<input id="apiId" type="number"></label><label>API hash<input id="apiHash"></label></div><button class="btn primary" data-act="apiAdd">Add API</button></div>
   <div class="card"><h3>Force channel join</h3><p class="muted" style="margin-bottom:10px">${s.force_channel ? `Current: <b>${esc(s.force_channel.title)}</b>` : 'Currently off.'} The bot must be an admin there.</p><label>Channel (@username or -100… id; empty = off)<input id="fcIn" placeholder="@mychannel"></label><button class="btn primary" data-act="saveForce">Check &amp; save</button></div>
   <div class="card" style="grid-column:1/-1"><h3>Session safety &amp; speed</h3><p class="muted" style="margin-bottom:12px">Slower = safer for your accounts. Defaults are conservative. Max connections is limited by your server’s RAM.</p>
     <div class="three">${Object.entries(SAFE).map(([k, l]) => `<label>${l}<input data-safe="${k}" type="number" step="any" min="0" value="${s.safety[k]}"></label>`).join('')}</div>
     <div class="three">${Object.entries(SPEED).map(([k, l]) => `<label>${l}<input data-speed="${k}" type="number" step="any" min="0" value="${s.speed[k]}"></label>`).join('')}</div><button class="btn primary" data-act="saveEngine">Save safety settings</button></div></div>`;
}
A.savePw = async () => { await api('/settings/password', { method: 'POST', json: { current: $('#aCur').value, new: $('#aNew').value, username: $('#aUser').value } }); toast('Password changed'); $('#aCur').value = $('#aNew').value = ''; };
A.saveGeneral = async () => { await api('/settings/general', { method: 'PUT', json: { upi_id: $('#gUpi').value, support_username: $('#gSup').value } }); toast('Saved'); };
A.saveQr = async () => { const f = $('#qrIn').files[0]; if (!f) return toast('Choose an image first', true); const fd = new FormData(); fd.append('file', f); await api('/settings/qr', { method: 'POST', form: fd }); toast('QR updated'); pageSettings(); };
A.apiAdd = async () => {
  const cur = (await api('/settings')).apis.map(a => ({ id: a.id, hash: '' })), id = +$('#apiId').value, hash = $('#apiHash').value.trim();
  if (!id || !hash) return toast('Enter both API ID and hash', true);
  await api('/settings/apis', { method: 'PUT', json: { apis: [...cur.filter(a => a.id !== id), { id, hash }] } }); toast('API added'); pageSettings();
};
A.apiDel = async el => { if (!confirm('Remove this API? Existing sessions switch to another key.')) return; const cur = (await api('/settings')).apis.filter(a => a.id !== +el.dataset.id).map(a => ({ id: a.id, hash: '' })); await api('/settings/apis', { method: 'PUT', json: { apis: cur } }); toast('API removed'); pageSettings(); };
A.saveForce = async () => { const r = await api('/settings/force', { method: 'PUT', json: { channel: $('#fcIn').value } }); toast(r.message); pageSettings(); };
A.saveEngine = async () => {
  const safety = {}, speed = {}; $$('[data-safe]').forEach(i => safety[i.dataset.safe] = +i.value); $$('[data-speed]').forEach(i => speed[i.dataset.speed] = +i.value);
  await api('/settings/engine', { method: 'PUT', json: { safety, speed } }); toast('Safety settings saved');
};

boot();
