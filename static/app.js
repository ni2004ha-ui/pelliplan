'use strict';
/* PelliPlan UI. No framework, no build step. Every piece of user text goes in through textContent (el()),
   never innerHTML, so a task title like "<img onerror=...>" is just text. */
const state = {
  tokens: JSON.parse(localStorage.getItem('pp_tokens') || 'null'), who: localStorage.getItem('pp_who'),
  me: null, plan: null, proposals: [], approvals: [], activity: [], tab: 'dashboard', open: new Set(),
  hi: { moved: new Set(), pressured: new Set(), misses: new Set() }, whatif: null, ask: null, graphCer: null, sel: null, busy: false, uploadNote: null,
};
const $ = (s) => document.querySelector(s);

function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === 'class') e.className = v;
    else if (k === 'value') e.value = v;
    else if (k === 'checked' || k === 'disabled' || k === 'selected') e[k] = !!v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (const c of kids.flat(Infinity)) { if (c == null || c === false) continue; e.append(c.nodeType ? c : document.createTextNode(String(c))); }
  return e;
}
const NS = 'http://www.w3.org/2000/svg';
function svg(tag, attrs, ...kids) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v != null && v !== false) e.setAttribute(k, v);
  for (const c of kids.flat()) if (c != null) e.append(c.nodeType ? c : document.createTextNode(String(c)));
  return e;
}
let toastTimer;
function toast(msg, err) { const t = $('#toast'); t.textContent = msg; t.className = 'toast show' + (err ? ' err' : ''); clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.className = 'toast'), 3800); }

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (state.tokens && state.who && state.tokens[state.who]) headers['X-Token'] = state.tokens[state.who];
  let body = opts.body;
  if (body && !(body instanceof FormData)) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(body); }
  const r = await fetch(path, { method: opts.method || 'GET', headers, body });
  const isJson = (r.headers.get('content-type') || '').includes('json');
  const data = isJson ? await r.json() : null;
  if (!r.ok) { const e = new Error(typeof data?.detail === 'string' ? data.detail : 'That input was not accepted.'); e.status = r.status; throw e; }
  return data;
}
async function act(fn, okMsg) { try { const r = await fn(); if (okMsg) toast(okMsg); await refresh(); render(); return r; } catch (e) { toast(e.message, true); } }

const fmtD = (iso) => (iso ? new Date(iso + 'T00:00:00').toLocaleDateString('en-IN', { day: 'numeric', month: 'short' }) : '-');
const days = (a, b) => Math.round((new Date(b + 'T00:00:00') - new Date(a + 'T00:00:00')) / 86400000);
const rupee = (n) => '\u20B9' + Number(n).toLocaleString('en-IN');
const isBride = () => state.me && state.me.role === 'bride';

const FLAGS = {
  evidence_not_in_document: ['bad', 'The quoted words were NOT found in the document'], amount_not_in_document: ['bad', 'This amount was not found in the document'],
  image_source: ['tight', 'Read from an image: check the picture yourself'], possible_duplicate: ['tight', 'Looks like a task you already have'],
  past_due: ['tight', 'The date is already in the past'], after_ceremony: ['tight', 'Due after the ceremony it belongs to'],
  unknown_ceremony: ['tight', 'Ceremony name not recognised'], no_ceremony: ['grey', 'No ceremony assigned'], unknown_assignee: ['grey', 'Person not recognised'],
  low_confidence: ['tight', 'Low confidence'], duration_assumed: ['grey', 'Duration not stated: assumed 1 day'],
  dependency_unresolved: ['grey', 'A dependency could not be matched'], dependency_would_loop: ['tight', 'A dependency was skipped: it would create a loop'],
};
const BLOCKING = ['evidence_not_in_document', 'image_source', 'possible_duplicate', 'amount_not_in_document'];

function slackBadge(t) {
  if (t.status === 'done') return el('span', { class: 'badge b-ok' }, 'Done');
  if (t.late_days > 0) return el('span', { class: 'badge b-bad' }, `Late by ${t.late_days}d`);
  if (t.slack == null) return el('span', { class: 'badge b-grey' }, 'No deadline');
  if (t.slack < 0) return el('span', { class: 'badge b-bad' }, 'No room: behind');
  if (t.slack === 0) return el('span', { class: 'badge b-tight' }, 'Critical');
  if (t.tight) return el('span', { class: 'badge b-tight' }, `Tight: ${t.slack}d spare`);
  return el('span', { class: 'badge b-ok' }, `${t.slack}d spare`);
}
const riskBadge = (r) => el('span', { class: 'badge ' + (r === 'at_risk' ? 'b-bad' : r === 'tight' ? 'b-tight' : 'b-ok') }, r === 'at_risk' ? 'At risk' : r === 'tight' ? 'Tight' : 'On track');
const byId = () => Object.fromEntries(state.plan.tasks.map((t) => [t.id, t]));

/* ------------------------------------------------------------------ boot and shell */
async function refresh() {
  state.me = await api('/api/me');
  state.plan = await api('/api/plan');
  state.approvals = await api('/api/approvals');
  state.proposals = isBride() ? (await api('/api/proposals')).filter((p) => p.kind === 'extraction' && p.status === 'pending') : [];
}
async function boot() {
  if (!state.tokens) return renderStart();
  try { await refresh(); } catch (e) { if (e.status === 401) { localStorage.removeItem('pp_tokens'); state.tokens = null; return renderStart(); } toast(e.message, true); return; }
  render();
}
function renderStart() {
  $('#who').replaceChildren(); $('#nav').replaceChildren();
  $('#view').replaceChildren(el('div', { class: 'card', style: 'max-width:560px;margin:30px auto' },
    el('h2', {}, 'Welcome to PelliPlan'),
    el('p', {}, 'A wedding operating system: it reads your plan, tracks what depends on what, catches problems early, and keeps the family in sync.'),
    el('p', { class: 'mute small' }, 'This starts a demo wedding with invented people. Nothing leaves your computer.'),
    el('button', { onclick: async () => {
      try { const rows = await api('/api/demo/reset', { method: 'POST' }); state.tokens = Object.fromEntries(rows.map((r) => [r.name, r.token])); state.who = rows[0].name;
        localStorage.setItem('pp_tokens', JSON.stringify(state.tokens)); localStorage.setItem('pp_who', state.who); boot(); } catch (e) { toast(e.message, true); }
    } }, 'Start the demo wedding')));
}
function tabsFor() {
  const t = [['dashboard', 'Dashboard'], ['plan', 'Plan'], ['graph', 'Dependencies'], ['brain', 'Wedding Brain']];
  if (isBride()) t.push(['whatif', 'What-if']);
  t.push(['activity', 'Activity']);
  return t;
}
function render() {
  const names = Object.keys(state.tokens || {});
  const roleOf = Object.fromEntries((state.plan?.members || []).map((m) => [m.name, m.role]));
  $('#who').replaceChildren(
    el('label', { style: 'color:#fff', for: 'who-sel' }, 'Viewing as'),
    el('select', { id: 'who-sel', onchange: async (e) => { state.who = e.target.value; localStorage.setItem('pp_who', state.who); state.tab = 'dashboard'; state.ask = null; state.whatif = null; state.sel = null; await refresh(); render(); } },
      names.map((n) => el('option', { value: n, selected: n === state.who }, `${n}${roleOf[n] ? ' (' + roleOf[n] + ')' : ''}`))),
    el('button', { class: 'ghost sm', onclick: async () => { if (!confirm('Reset the demo wedding? All changes will be lost.')) return;
      const rows = await api('/api/demo/reset', { method: 'POST' }); state.tokens = Object.fromEntries(rows.map((r) => [r.name, r.token])); state.who = rows[0].name;
      localStorage.setItem('pp_tokens', JSON.stringify(state.tokens)); localStorage.setItem('pp_who', state.who); state.whatif = null; state.ask = null; await refresh(); render(); } }, 'Reset demo'));
  if (!tabsFor().some(([k]) => k === state.tab)) state.tab = 'dashboard';
  $('#nav').replaceChildren(...tabsFor().map(([k, label]) => el('button', { class: state.tab === k ? 'on' : '', onclick: () => { state.tab = k; render(); } }, label)));
  const views = { dashboard: viewDashboard, plan: viewPlan, graph: viewGraph, brain: viewBrain, whatif: viewWhatIf, activity: viewActivity };
  const out = views[state.tab]();
  if (out instanceof Promise) out.then((n) => $('#view').replaceChildren(n)); else $('#view').replaceChildren(out);
}

/* ------------------------------------------------------------------ dashboard */
function viewDashboard() {
  const p = state.plan, tasks = p.tasks, me = state.me;
  const open = tasks.filter((t) => t.status !== 'done');
  const attention = open.filter((t) => t.late_days > 0 || (t.slack != null && t.slack <= 3)).sort((a, b) => (b.late_days - a.late_days) || ((a.slack ?? 99) - (b.slack ?? 99))).slice(0, 6);
  const mine = open.filter((t) => t.assignee_id === me.id);
  const pendingItems = state.proposals.reduce((n, pr) => n + pr.payload.items.filter((i) => i.status === 'pending').length, 0);
  return el('div', {},
    el('h2', {}, `Hello, ${me.name}`),
    el('div', { class: 'grid mb' }, p.ceremonies.map((c) => el('div', { class: 'card' },
      el('div', { class: 'row between' }, el('b', {}, c.name), riskBadge(c.risk)),
      el('div', { class: 'mute small mb' }, `${fmtD(c.date)} · ${days(p.today, c.date)} days to go`),
      el('div', { class: 'bar' }, el('i', { style: `width:${c.total ? Math.round(100 * c.done / c.total) : 0}%` })),
      el('div', { class: 'small mute', style: 'margin-top:4px' }, `${c.done} of ${c.total} tasks done`)))),
    el('div', { class: 'grid' },
      state.approvals.some((a) => a.status === 'pending') && el('div', { class: 'card' }, el('h3', {}, 'Waiting for your approval'),
        state.approvals.filter((a) => a.status === 'pending').map((a) => approvalRow(a))),
      el('div', { class: 'card' }, el('h3', {}, 'Needs attention'),
        attention.length ? attention.map((t) => el('div', { class: 'row between', style: 'padding:5px 0' }, el('span', {}, t.title), slackBadge(t))) : el('div', { class: 'mute small' }, 'Nothing is tight right now.')),
      el('div', { class: 'card' }, el('h3', {}, 'My tasks'),
        mine.length ? mine.map((t) => el('div', { class: 'row between', style: 'padding:5px 0' }, el('span', {}, t.title), el('span', { class: 'mute small' }, 'due ' + fmtD(t.effective_deadline)))) : el('div', { class: 'mute small' }, 'No open tasks assigned to you.')),
      isBride() && el('div', { class: 'card' }, el('h3', {}, 'To review'),
        el('div', {}, pendingItems ? `${pendingItems} item(s) found in uploaded documents are waiting for you.` : 'No uploaded documents are waiting.'),
        pendingItems ? el('button', { class: 'sm', style: 'margin-top:8px', onclick: () => { state.tab = 'brain'; render(); } }, 'Review now') : null,
        el('div', { class: 'small mute', style: 'margin-top:8px' }, `${open.filter((t) => t.is_decision).length} open decision(s) in the plan.`))));
}
function approvalRow(a) {
  const note = el('input', { type: 'text', placeholder: 'Optional comment' });
  return el('div', { style: 'padding:6px 0;border-top:1px solid var(--line)' }, el('b', {}, a.title), el('div', { class: 'small mute' }, `${a.question || ''} (due ${fmtD(a.due_date)})`),
    el('div', { class: 'row mt' }, note, el('button', { class: 'sm', onclick: () => act(() => api(`/api/approvals/${a.id}/decide`, { method: 'POST', body: { approve: true, comment: note.value || null } }), 'Approved') }, 'Approve'),
      el('button', { class: 'sm ghost', onclick: () => act(() => api(`/api/approvals/${a.id}/decide`, { method: 'POST', body: { approve: false, comment: note.value || null } }), 'Sent back') }, 'Not yet')));
}

/* ------------------------------------------------------------------ plan */
function viewPlan() {
  const p = state.plan, ids = byId();
  const groups = [...p.ceremonies.map((c) => ({ key: c.id, title: c.name, sub: `${fmtD(c.date)}`, risk: c.risk })), { key: null, title: 'General', sub: '' }];
  const wrap = el('div', {}, el('div', { class: 'row between' }, el('h2', {}, 'Plan'), isBride() ? el('button', { class: 'sm', onclick: () => { state.open.add('new'); render(); } }, '+ Add task') : null));
  if (isBride() && state.open.has('new')) wrap.append(newTaskForm());
  for (const g of groups) {
    const ts = p.tasks.filter((t) => t.ceremony_id === g.key);
    if (!ts.length) continue;
    wrap.append(el('div', { class: 'card pad0 mb' },
      el('div', { class: 'row between', style: 'padding:10px 12px;background:#faf5ea' }, el('b', {}, g.title), el('span', { class: 'row' }, g.sub ? el('span', { class: 'mute small' }, g.sub) : null, g.risk ? riskBadge(g.risk) : null)),
      ts.map((t) => taskRow(t, ids))));
  }
  if (!p.tasks.length) wrap.append(el('div', { class: 'empty' }, 'No tasks yet.'));
  return wrap;
}
function taskRow(t, ids) {
  const expanded = state.open.has(t.id), hi = state.hi.moved.has(t.id) || state.hi.misses.has(t.id);
  const box = el('input', { type: 'checkbox', checked: t.status === 'done', disabled: !t.can_change_status, 'aria-label': 'Mark done',
    onchange: (e) => act(() => api(`/api/tasks/${t.id}`, { method: 'PATCH', body: { status: e.target.checked ? 'done' : 'todo' } })) });
  const row = el('div', { class: 'task' + (t.status === 'done' ? ' done' : '') + (hi ? ' hi' : '') }, box,
    el('div', { onclick: () => { expanded ? state.open.delete(t.id) : state.open.add(t.id); render(); }, style: 'cursor:pointer' },
      el('div', { class: 't' }, t.title, t.is_decision ? el('span', { class: 'badge b-info', style: 'margin-left:6px' }, 'decision') : null),
      el('div', { class: 'meta' }, el('span', {}, t.assignee_name ? '\u{1F464} ' + t.assignee_name : 'Unassigned'), el('span', {}, 'due ' + fmtD(t.effective_deadline)),
        t.depends_on.length || t.hidden_blockers ? el('span', {}, `\u{1F517} waits on ${t.depends_on.length + t.hidden_blockers}`) : null,
        t.amount != null ? el('span', {}, rupee(t.amount)) : null, t.visibility !== 'team' ? el('span', {}, '\u{1F512} ' + t.visibility) : null)),
    el('div', { class: 'right' }, slackBadge(t)));
  if (expanded) row.append(taskDetail(t, ids));
  return row;
}
function taskDetail(t, ids) {
  const d = el('div', { class: 'detail' });
  d.append(el('div', { class: 'small' }, `Starts ${fmtD(t.es)}, finishes ${fmtD(t.ef)} (${t.duration_days} day${t.duration_days === 1 ? '' : 's'} of work). Status: ${t.status.replace('_', ' ')}.`));
  if (t.description) d.append(el('div', { class: 'small mute' }, t.description));
  if (t.depends_on.length) d.append(el('div', { class: 'small mt' }, 'Waits on: ', t.depends_on.map((i) => el('span', { class: 'chip' }, ids[i]?.title || '#' + i,
    isBride() ? el('button', { title: 'Remove', onclick: (e) => { e.stopPropagation(); act(() => api(`/api/tasks/${t.id}/dependencies/${i}`, { method: 'DELETE' }), 'Link removed'); } }, '\u00d7') : null))));
  if (t.hidden_blockers) d.append(el('div', { class: 'small mute' }, `${t.hidden_blockers} more blocker(s) are private.`));
  if (t.can_change_status && !isBride()) d.append(el('div', { class: 'row mt' }, ['todo', 'in_progress', 'done', 'blocked'].map((s) =>
    el('button', { class: 'sm ' + (t.status === s ? '' : 'ghost'), onclick: () => act(() => api(`/api/tasks/${t.id}`, { method: 'PATCH', body: { status: s } })) }, s.replace('_', ' ')))));
  for (const a of t.approvals || []) d.append(el('div', { class: 'small mt' }, '\u2714 Approval asked: ', a.question, ' ', el('span', { class: 'badge ' + (a.status === 'approved' ? 'b-ok' : a.status === 'rejected' ? 'b-bad' : 'b-grey') }, a.status)));
  if (isBride()) {
    if (t.notes_private) d.append(el('div', { class: 'quote' }, '\u{1F512} Private note: ' + t.notes_private));
    if (t.evidence) d.append(el('div', { class: 'quote' }, 'Source: "' + t.evidence + '" ', t.source_doc_id ? el('button', { class: 'sm ghost', onclick: () => openDoc(t.source_doc_id) }, 'Open document') : null));
    d.append(editForm(t, ids));
  }
  return d;
}
async function openDoc(id) {
  try { const r = await fetch(`/api/documents/${id}/file`, { headers: { 'X-Token': state.tokens[state.who] } }); if (!r.ok) throw new Error('Could not open the document');
    window.open(URL.createObjectURL(await r.blob()), '_blank'); } catch (e) { toast(e.message, true); }
}
function memberSelect(sel, id) { return el('select', { id }, el('option', { value: '' }, 'Unassigned'), state.plan.members.map((m) => el('option', { value: m.id, selected: m.id === sel }, m.name))); }
function editForm(t, ids) {
  const f = { title: el('input', { type: 'text', value: t.title }), dur: el('input', { type: 'number', min: 0, value: t.duration_days, style: 'width:70px' }),
    due: el('input', { type: 'date', value: t.due_date || '' }), who: memberSelect(t.assignee_id), vis: el('select', {}, ['private', 'team', 'family'].map((v) => el('option', { value: v, selected: v === t.visibility }, v))),
    amt: el('input', { type: 'number', min: 0, value: t.amount ?? '', style: 'width:110px' }), dep: el('select', {}, el('option', { value: '' }, 'Add a dependency...'), state.plan.tasks.filter((x) => x.id !== t.id && !t.depends_on.includes(x.id)).map((x) => el('option', { value: x.id }, x.title))),
    appr: el('select', {}, el('option', { value: '' }, 'Ask a family member...'), state.plan.members.filter((m) => m.role === 'family').map((m) => el('option', { value: m.id }, m.name))) };
  return el('div', { class: 'mt', style: 'border-top:1px dashed var(--line);padding-top:8px' },
    el('div', { class: 'row' }, el('div', { class: 'grow' }, el('label', {}, 'Title'), f.title), el('div', {}, el('label', {}, 'Days of work'), f.dur), el('div', {}, el('label', {}, 'Deadline'), f.due)),
    el('div', { class: 'row mt' }, el('div', {}, el('label', {}, 'Assigned to'), f.who), el('div', {}, el('label', {}, 'Who can see it'), f.vis), el('div', {}, el('label', {}, 'Amount (\u20B9)'), f.amt),
      el('button', { class: 'sm', onclick: () => act(() => api(`/api/tasks/${t.id}`, { method: 'PATCH', body: { title: f.title.value, duration_days: Number(f.dur.value), due_date: f.due.value || null,
        assignee_id: f.who.value ? Number(f.who.value) : null, visibility: f.vis.value, amount: f.amt.value === '' ? null : Number(f.amt.value) } }), 'Saved') }, 'Save')),
    el('div', { class: 'row mt' }, f.dep, el('button', { class: 'sm ghost', onclick: () => f.dep.value && act(() => api(`/api/tasks/${t.id}/dependencies`, { method: 'POST', body: { depends_on_id: Number(f.dep.value) } }), 'Linked') }, 'Link'),
      f.appr, el('button', { class: 'sm ghost', onclick: () => f.appr.value && act(() => api(`/api/tasks/${t.id}/approval`, { method: 'POST', body: { requested_from: Number(f.appr.value), question: `Please approve: ${t.title}` } }), 'Approval requested') }, 'Request approval'),
      el('button', { class: 'sm danger', onclick: () => confirm('Delete this task?') && act(() => api(`/api/tasks/${t.id}`, { method: 'DELETE' }), 'Deleted') }, 'Delete')));
}
function newTaskForm() {
  const f = { title: el('input', { type: 'text', placeholder: 'What needs to happen?' }), cer: el('select', {}, el('option', { value: '' }, 'No ceremony'), state.plan.ceremonies.map((c) => el('option', { value: c.id }, c.name))),
    dur: el('input', { type: 'number', min: 0, value: 1, style: 'width:70px' }), due: el('input', { type: 'date' }), who: memberSelect(null) };
  return el('div', { class: 'card mb' }, el('h3', {}, 'New task'), el('div', { class: 'row' }, el('div', { class: 'grow' }, f.title), f.cer, el('div', {}, el('label', {}, 'Days'), f.dur), f.due, f.who,
    el('button', { class: 'sm', onclick: () => { if (f.title.value.trim().length < 2) return toast('Please enter a title', true);
      state.open.delete('new'); act(() => api('/api/tasks', { method: 'POST', body: { title: f.title.value.trim(), ceremony_id: f.cer.value ? Number(f.cer.value) : null, duration_days: Number(f.dur.value), due_date: f.due.value || null, assignee_id: f.who.value ? Number(f.who.value) : null } }), 'Task added'); } }, 'Add'),
    el('button', { class: 'sm ghost', onclick: () => { state.open.delete('new'); render(); } }, 'Cancel')));
}

/* ------------------------------------------------------------------ dependency graph */
function viewGraph() {
  const p = state.plan, cers = p.ceremonies;
  if (!state.graphCer || !cers.some((c) => c.id === state.graphCer)) state.graphCer = (cers.find((c) => c.name === 'Muhurtham') || cers[0])?.id;
  const sel = el('select', { onchange: (e) => { state.graphCer = Number(e.target.value); state.sel = null; render(); } }, cers.map((c) => el('option', { value: c.id, selected: c.id === state.graphCer }, c.name)));
  const tasks = p.tasks.filter((t) => t.ceremony_id === state.graphCer);
  const idset = new Set(tasks.map((t) => t.id));
  const linked = tasks.filter((t) => t.depends_on.some((d) => idset.has(d)) || tasks.some((o) => o.depends_on.includes(t.id)));
  const nodes = linked, nset = new Set(nodes.map((t) => t.id));
  const depth = {}; const dep = (t) => (depth[t.id] ??= 1 + Math.max(-1, ...t.depends_on.filter((d) => nset.has(d)).map((d) => dep(nodes.find((x) => x.id === d)))));
  nodes.forEach(dep);
  const W = 190, H = 50, GX = 70, GY = 18, layers = {};
  nodes.forEach((t) => (layers[depth[t.id]] ||= []).push(t));
  const pos = {}; let maxRows = 0;
  Object.entries(layers).forEach(([d, ts]) => { maxRows = Math.max(maxRows, ts.length); ts.forEach((t, i) => (pos[t.id] = { x: 12 + d * (W + GX), y: 12 + i * (H + GY) })); });
  const width = Math.max(300, 24 + (Math.max(-1, ...Object.keys(layers).map(Number)) + 1) * (W + GX)), height = 24 + maxRows * (H + GY);
  const wrap = el('div', {}, el('h2', {}, 'Dependencies'), el('div', { class: 'row mb' }, el('label', {}, 'Ceremony'), sel,
    el('span', { class: 'legend' }, [['#2f7d4f', 'done'], ['#b3261e', 'behind or late'], ['#a86a00', 'critical or tight'], ['#cfc4b1', 'has spare time'], ['#e07a00', 'moved by what-if']].map(([c, l]) => el('span', {}, el('i', { class: 'dot', style: 'background:' + c }), l)))));
  if (!nodes.length) { wrap.append(el('div', { class: 'card empty' }, 'No linked tasks in this ceremony yet. Add dependencies from the Plan tab.')); return wrap; }
  const g = svg('svg', { class: 'graph', viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': 'Task dependency graph', style: `min-width:${Math.min(width, 900)}px` },
    svg('defs', {}, svg('marker', { id: 'arr', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto' }, svg('path', { d: 'M0 0L10 5L0 10z', fill: '#8a7f73' }))));
  nodes.forEach((t) => t.depends_on.filter((d) => nset.has(d)).forEach((d) => { const a = pos[d], b = pos[t.id];
    g.append(svg('path', { d: `M${a.x + W} ${a.y + H / 2} C${a.x + W + 40} ${a.y + H / 2}, ${b.x - 40} ${b.y + H / 2}, ${b.x} ${b.y + H / 2}`, fill: 'none', stroke: '#8a7f73', 'stroke-width': 1.5, 'marker-end': 'url(#arr)' })); }));
  nodes.forEach((t) => {
    const late = t.late_days > 0 || (t.slack != null && t.slack < 0), crit = t.critical || t.tight;
    const stroke = t.status === 'done' ? '#2f7d4f' : late ? '#b3261e' : crit ? '#a86a00' : '#cfc4b1', fill = t.status === 'done' ? '#e9f2ec' : late ? '#fde7e5' : crit ? '#fff1d6' : '#fff';
    const moved = state.hi.moved.has(t.id), pressed = state.hi.pressured.has(t.id);
    const title = t.title.length > 27 ? t.title.slice(0, 26) + '\u2026' : t.title;
    g.append(svg('g', { style: 'cursor:pointer', onclick: null, tabindex: 0 },
      svg('rect', { x: pos[t.id].x, y: pos[t.id].y, width: W, height: H, rx: 9, fill, stroke: moved ? '#e07a00' : stroke, 'stroke-width': moved ? 3.5 : 1.8, 'stroke-dasharray': pressed ? '5 3' : null }),
      svg('text', { x: pos[t.id].x + 10, y: pos[t.id].y + 20, 'font-size': 12.5, 'font-weight': 600, fill: '#2b2226' }, title),
      svg('text', { x: pos[t.id].x + 10, y: pos[t.id].y + 38, 'font-size': 11, fill: '#6f6368' }, `${t.assignee_name || 'unassigned'} · ${fmtD(t.es)}\u2013${fmtD(t.ef)}`)));
    const node = g.lastChild; node.addEventListener('click', () => { state.sel = t.id; render(); });
  });
  wrap.append(el('div', { style: 'overflow-x:auto' }, g));
  const s = state.sel && p.tasks.find((t) => t.id === state.sel);
  if (s) wrap.append(el('div', { class: 'card mt' }, el('div', { class: 'row between' }, el('b', {}, s.title), slackBadge(s)),
    el('div', { class: 'small mute' }, `Starts ${fmtD(s.es)}, finishes ${fmtD(s.ef)}; deadline ${fmtD(s.effective_deadline)}${s.slack != null ? `; ${s.slack} day(s) of slack` : ''}. ${s.assignee_name ? 'Owner: ' + s.assignee_name + '.' : ''}`),
    s.depends_on.length ? el('div', { class: 'small' }, 'Waits on: ' + s.depends_on.map((i) => byId()[i]?.title).join(', ')) : null));
  wrap.append(el('p', { class: 'small mute mt' }, 'Arrows point from a task to the task that cannot start until it finishes. Dashed outline = must finish sooner to protect a deadline.'));
  return wrap;
}

/* ------------------------------------------------------------------ Wedding Brain */
function viewBrain() {
  const wrap = el('div', {}, el('h2', {}, 'Wedding Brain'));
  const modeNote = state.me.llm === 'ai' ? el('span', { class: 'badge b-ok' }, 'AI on') : el('span', { class: 'badge b-tight' }, 'No AI key: using rule-based mode');
  wrap.append(el('div', { class: 'row mb' }, modeNote, el('span', { class: 'small mute' }, 'The AI reads and explains. It never changes your plan on its own.')));
  // Ask
  const q = el('input', { type: 'text', placeholder: 'What am I forgetting for my muhurtham?', value: state.ask?.question || '' });
  const go = (cerId) => async () => { try { const r = await api('/api/ask', { method: 'POST', body: { question: q.value || 'What am I forgetting?', ceremony_id: cerId || null } }); state.ask = { ...r, question: q.value }; render(); } catch (e) { toast(e.message, true); } };
  wrap.append(el('div', { class: 'card mb' }, el('h3', {}, 'Ask about your plan'), el('div', { class: 'row' }, q, el('button', { onclick: go() }, 'Ask')),
    el('div', { class: 'row mt' }, ['muhurtham', 'pellikuthuru', 'sangeet', 'reception'].map((c) => el('button', { class: 'sm ghost', onclick: () => { q.value = `What am I forgetting for ${c}?`; go()(); } }, c))),
    state.ask ? askResult(state.ask, go) : null));
  // Upload
  if (state.me.role !== 'family') {
    const file = el('input', { type: 'file', accept: '.txt,.md,.csv,.pdf,.png,.jpg,.jpeg,.webp' });
    wrap.append(el('div', { class: 'card mb' }, el('h3', {}, 'Add a document'), el('div', { class: 'drop' },
      el('div', { class: 'mb small' }, 'Vendor quote, planning notes, a PDF or a screenshot. Try the files in the samples folder.'), file,
      el('div', { class: 'mt' }, el('button', { disabled: state.busy, onclick: async () => {
        if (!file.files[0]) return toast('Choose a file first', true);
        const fd = new FormData(); fd.append('file', file.files[0]); state.busy = true; render();
        try { const r = await api('/api/documents', { method: 'POST', body: fd }); state.uploadNote = isBride() ? null : r.note; toast(isBride() ? `Found ${r.items.length} item(s) to review` : `Sent ${r.items_found} item(s) to the bride for review`); }
        catch (e) { toast(e.message, true); } state.busy = false; await refresh(); render(); } }, state.busy ? 'Reading...' : 'Analyse document'))),
      state.uploadNote ? el('p', { class: 'small mute' }, state.uploadNote) : null));
  }
  if (isBride()) {
    if (!state.proposals.length) wrap.append(el('div', { class: 'card empty' }, 'No documents are waiting for review.'));
    for (const pr of state.proposals) wrap.append(proposalCard(pr));
  }
  return wrap;
}
function askResult(r, go) {
  if (r.needs_ceremony) return el('div', { class: 'mt' }, el('div', { class: 'small mb' }, 'Which ceremony do you mean?'), el('div', { class: 'row' }, r.ceremonies.map((c) => el('button', { class: 'sm ghost', onclick: go(c.id) }, c.name))));
  const urg = { at_risk: ['b-bad', 'At risk'], missing: ['b-tight', 'Nothing planned yet'], tight: ['b-tight', 'Tight'], normal: ['b-grey', 'Open'] };
  return el('div', { class: 'mt' }, el('div', { class: 'row between' }, el('b', {}, `${r.ceremony.name} · ${fmtD(r.ceremony.date)}`), el('span', { class: 'badge ' + (r.mode === 'ai' ? 'b-ok' : 'b-grey') }, r.mode === 'ai' ? 'AI explanation' : 'Rule-based')),
    el('p', {}, r.summary), r.items.map((i) => el('div', { class: 'item' }, el('div', { class: 'row between' }, el('b', {}, i.title), el('span', { class: 'badge ' + urg[i.urgency][0] }, urg[i.urgency][1])),
      el('div', { class: 'small', style: 'margin-top:4px' }, i.why), i.next_step ? el('div', { class: 'small mute' }, '\u2192 ' + i.next_step) : null)),
    r.more_not_shown ? el('div', { class: 'small mute' }, `${r.more_not_shown} lower-priority item(s) not shown.`) : null, el('div', { class: 'small mute mt' }, r.template_note));
}
function proposalCard(pr) {
  const items = pr.payload.items;
  return el('div', { class: 'card mb' }, el('div', { class: 'row between' }, el('h3', {}, `From "${pr.payload.filename}"`), el('span', { class: 'badge b-info' }, pr.payload.mode === 'ai' ? 'AI extraction' : 'Rule-based extraction')),
    el('div', { class: 'small mute mb' }, 'Nothing here is in your plan yet. Confirm each item, fix it if needed, or reject it.' + (pr.payload.dropped_invalid ? ` ${pr.payload.dropped_invalid} malformed item(s) were discarded.` : '')),
    items.map((it) => itemCard(pr, it)));
}
function itemCard(pr, it) {
  const title = el('input', { type: 'text', value: it.title, disabled: it.status !== 'pending' });
  const due = el('input', { type: 'date', value: it.due_date || '', disabled: it.status !== 'pending' });
  const dur = el('input', { type: 'number', min: 0, value: it.duration_days, style: 'width:70px', disabled: it.status !== 'pending' });
  const ack = el('input', { type: 'checkbox', id: `ack-${pr.id}-${it.index}` });
  const blocking = it.flags.filter((f) => BLOCKING.includes(f));
  return el('div', { class: 'item ' + (it.status !== 'pending' ? it.status : '') },
    el('div', { class: 'row' }, el('div', { class: 'grow' }, title), el('span', { class: 'badge b-grey' }, it.kind)),
    el('div', { class: 'meta' }, el('span', {}, it.ceremony_name || 'No ceremony'), it.amount != null ? el('span', {}, rupee(it.amount)) : null, it.assignee_name ? el('span', {}, '\u{1F464} ' + it.assignee_name) : null,
      el('span', { class: 'badge ' + (it.evidence_verified === true ? 'b-ok' : it.evidence_verified === false ? 'b-bad' : 'b-tight') }, it.evidence_verified === true ? '\u2713 quote found in document' : it.evidence_verified === false ? '\u2717 quote NOT found' : 'image: check by eye')),
    el('div', { class: 'quote' }, '"' + it.evidence + '"'),
    it.flags.map((f) => FLAGS[f] ? el('span', { class: 'badge flag b-' + (FLAGS[f][0] === 'bad' ? 'bad' : FLAGS[f][0] === 'tight' ? 'tight' : 'grey') }, FLAGS[f][1]) : null),
    it.status === 'pending' ? el('div', { class: 'row mt' }, el('div', {}, el('label', {}, 'Deadline'), due), el('div', {}, el('label', {}, 'Days of work'), dur),
      blocking.length ? el('label', { style: 'display:flex;gap:6px;align-items:center;color:var(--bad)' }, ack, 'I have checked the warnings') : null,
      el('button', { class: 'sm', onclick: () => act(() => api(`/api/proposals/${pr.id}/items/${it.index}/confirm`, { method: 'POST', body: { acknowledge: ack.checked, overrides: { title: title.value, due_date: due.value || null, duration_days: Number(dur.value) } } }), 'Added to your plan') }, 'Confirm'),
      el('button', { class: 'sm ghost', onclick: () => act(() => api(`/api/proposals/${pr.id}/items/${it.index}/reject`, { method: 'POST' }), 'Rejected') }, 'Reject'))
      : el('div', { class: 'small mt' }, it.status === 'confirmed' ? '\u2713 Added to your plan' : 'Rejected'));
}

/* ------------------------------------------------------------------ what-if */
function viewWhatIf() {
  const open = state.plan.tasks.filter((t) => t.status !== 'done');
  const sel = el('select', {}, open.map((t) => el('option', { value: t.id, selected: state.whatif?.task_id === t.id || (!state.whatif && /Deliver saree/.test(t.title)) }, t.title)));
  const n = el('input', { type: 'number', min: 1, max: 90, value: state.whatif?.days ?? 5, style: 'width:80px' });
  const wrap = el('div', {}, el('h2', {}, 'What if something slips?'), el('p', { class: 'small mute' }, 'A scheduling engine (not the AI) recomputes every date. The AI only explains the result. Nothing changes until you approve a plan.'),
    el('div', { class: 'card mb' }, el('div', { class: 'row' }, el('div', { class: 'grow' }, el('label', {}, 'Task that is delayed'), sel), el('div', {}, el('label', {}, 'Days of delay'), n),
      el('button', { onclick: async () => { try { const body = { task_id: Number(sel.value), slip_days: Number(n.value) }; const r = await api('/api/whatif', { method: 'POST', body });
        state.whatif = { task_id: body.task_id, days: body.slip_days, ...r };
        state.hi = { moved: new Set(r.facts.moved.map((m) => m.id)), pressured: new Set(r.facts.pressured.map((m) => m.id)), misses: new Set(r.facts.deadline_misses.map((m) => m.id)) }; render(); } catch (e) { toast(e.message, true); } } }, 'Simulate'))));
  const w = state.whatif; if (!w) return wrap;
  const f = w.facts, rec = f.recovery;
  wrap.append(el('div', { class: 'card mb' }, el('div', { class: 'row between' }, el('h3', {}, 'What this means'), el('span', { class: 'badge ' + (w.explanation.mode === 'ai' ? 'b-ok' : 'b-grey') }, w.explanation.mode === 'ai' ? 'AI wording' : 'Rule-based wording')),
    el('p', {}, w.explanation.text), f.ceremonies_at_risk.length ? el('div', {}, 'Ceremonies at risk: ', f.ceremonies_at_risk.map((c) => el('span', { class: 'badge b-bad', style: 'margin-right:4px' }, c))) : el('span', { class: 'badge b-ok' }, 'No ceremony is at risk')));
  wrap.append(el('div', { class: 'grid mb' },
    el('div', { class: 'card' }, el('h3', {}, `Tasks that move later (${f.moved.length})`), f.moved.map((m) => el('div', { style: 'padding:4px 0;border-top:1px solid var(--line)' }, el('b', {}, m.title), el('div', { class: 'small mute' }, `+${m.shift_days} day(s), now finishes ${m.new_finish}`)))),
    el('div', { class: 'card' }, el('h3', {}, 'Deadlines missed'), f.deadline_misses.length ? f.deadline_misses.map((m) => el('div', { style: 'padding:4px 0' }, el('b', {}, m.title), el('div', { class: 'small', style: 'color:var(--bad)' }, `${m.late_days} day(s) after its ${m.deadline} deadline`))) : el('div', { class: 'mute small' }, 'None.'),
      f.pressured.length ? el('div', { class: 'small mute mt' }, 'Earlier tasks that must now finish sooner: ' + f.pressured.map((p) => p.title).join(', ')) : null),
    el('div', { class: 'card' }, el('h3', {}, 'Recovery plan'), rec.steps.length ? rec.steps.map((s, i) => el('div', { class: 'step' }, el('span', { class: 'num' }, i + 1), el('div', {}, el('b', {}, `Speed up "${s.title}" by ${s.cut_days} day(s)`), el('div', { class: 'small mute' }, `Extra cost ${rupee(s.cost)}`)))) : el('div', { class: 'mute small' }, 'No speed-up options exist for the affected tasks.'),
      rec.steps.length ? el('div', { class: 'row between mt' }, el('b', {}, 'Total ' + rupee(rec.total_cost)), el('span', { class: 'badge ' + (rec.fully_recovered ? 'b-ok' : 'b-bad') }, rec.fully_recovered ? 'Fully recovers the schedule' : 'Does not fully recover')) : null,
      rec.remaining.length ? el('div', { class: 'small', style: 'color:var(--bad)' }, 'Still late: ' + rec.remaining.map((r) => `${r.title} (${r.late_days}d)`).join(', ')) : null)));
  wrap.append(el('div', { class: 'card' }, el('div', { class: 'row' },
    el('button', { onclick: async () => { if (!confirm('Record this delay and apply the recovery plan?')) return;
      await act(async () => { const p = await api('/api/whatif/propose', { method: 'POST', body: { task_id: w.task_id, slip_days: w.days } }); await api(`/api/proposals/${p.proposal_id}/decide`, { method: 'POST', body: { approve: true } }); state.whatif = null; state.hi = { moved: new Set(), pressured: new Set(), misses: new Set() }; }, 'Delay recorded and recovery plan applied'); } }, 'Approve: record the delay and apply this plan'),
    el('button', { class: 'ghost', onclick: () => { const t = state.plan.tasks.find((x) => x.id === w.task_id); state.graphCer = t?.ceremony_id || state.graphCer; state.tab = 'graph'; render(); } }, 'Show on the dependency graph'),
    el('button', { class: 'ghost', onclick: () => { state.whatif = null; state.hi = { moved: new Set(), pressured: new Set(), misses: new Set() }; render(); } }, 'Clear'))));
  return wrap;
}

/* ------------------------------------------------------------------ activity */
async function viewActivity() {
  let rows = []; try { rows = await api('/api/activity?limit=60'); } catch (e) { toast(e.message, true); }
  const verb = { created: 'created', updated: 'updated', deleted: 'deleted', linked: 'linked', unlinked: 'unlinked', uploaded: 'uploaded', asked: 'asked the Wedding Brain', confirmed_ai_item: 'confirmed an AI-found item', rejected_ai_item: 'rejected an AI-found item',
    requested_approval: 'asked for approval', approved: 'approved', rejected: 'sent back', proposed_recovery: 'proposed a recovery plan', recorded_delay: 'recorded a delay on', expedited: 'sped up', rejected_recovery: 'rejected a recovery plan' };
  return el('div', {}, el('h2', {}, 'Activity'), el('div', { class: 'card pad0' }, rows.length ? rows.map((r) => el('div', { class: 'task', style: 'grid-template-columns:1fr auto' },
    el('div', {}, el('b', {}, r.actor || 'System'), ' ' + (verb[r.action] || r.action) + ' ', el('span', {}, r.detail.title || r.detail.filename || r.entity)),
    el('div', { class: 'small mute' }, new Date(r.ts).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })))) : el('div', { class: 'empty' }, 'Nothing yet.')));
}

boot();
