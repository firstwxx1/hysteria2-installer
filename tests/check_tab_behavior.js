// Executes the real switchTab/switchSubtab code from the rendered page against a
// minimal DOM built from the actual markup, to verify group/tab behaviour.
const fs = require('fs');
const html = fs.readFileSync('portal-preview.html', 'utf8');
const script = fs.readFileSync('portal-script.js', 'utf8');
const assert = require('assert');

const start = script.indexOf('function switchSubtab');
const end = script.indexOf('function genRandom');
assert(start > 0 && end > start, 'tab code not found');
const tabCode = script.slice(start, end);

// --- minimal DOM ---------------------------------------------------------
const byId = {};
function el(tag, attrs = {}) {
  const node = {
    tag, attrs, dataset: {}, style: {}, textContent: '',
    _children: [], _classes: new Set((attrs.class || '').split(/\s+/).filter(Boolean)),
    classList: null, parent: null,
  };
  node.classList = {
    add: c => node._classes.add(c), remove: c => node._classes.delete(c),
    contains: c => node._classes.has(c),
  };
  Object.assign(node.dataset, attrs['data-tab'] ? { tab: attrs['data-tab'] } : {});
  Object.assign(node.dataset, attrs['data-subtab'] ? { subtab: attrs['data-subtab'] } : {});
  if (attrs.id) byId[attrs.id] = node;
  node.appendChild = c => { c.parent = node; node._children.push(c); return c; };
  node.addEventListener = () => {};
  node.closest = node.closest || null;
  node.querySelector = sel => {
    const all = node.querySelectorAll(sel);
    return all[0] || null;
  };
  node.querySelectorAll = sel => {
    let out = [];
    for (const c of node._children) {
      if (sel.startsWith('[data-subtab=') && c.dataset.subtab) out.push(c);
      else if (sel.startsWith('[data-tab=') && c.dataset.tab) out.push(c);
      else if (sel === '.subtab-btn' && c._classes.has('subtab-btn')) out.push(c);
      else if (sel === '.subtab-pane' && c._classes.has('subtab-pane')) out.push(c);
      else if (sel === '.tab-btn' && c._classes.has('tab-btn')) out.push(c);
      else if (sel === '.tab-pane' && c._classes.has('tab-pane')) out.push(c);
      out = out.concat(c.querySelectorAll(sel));
    }
    return out;
  };
  node.closest = sel => {
    let p = node;
    const cls = sel.replace('.', '');
    while (p) { if (p._classes.has(cls)) return p; p = p.parent; }
    return null;
  };
  return node;
}

// Build from real markup: panes, their subtab buttons and subtab panes.
const doc = el('html');
const all = [];
const paneRe = /<div class="tab-pane([^"]*)" id="pane-([a-z]+)">/g;
const panes = [...html.matchAll(paneRe)];
const tabbar = el('div', { class: 'tab-bar' });
doc.appendChild(tabbar);
for (const m of panes) {
  const pane = el('div', { class: m[1], id: 'pane-' + m[2] });
  pane.classList.add('tab-pane');
  if (m[1].includes('active')) pane.classList.add('active');
  doc.appendChild(pane);
  all.push(pane);
}
// tab buttons in order
for (const m of html.matchAll(/class="tab-btn([^"]*)" data-tab="([a-z]+)"/g)) {
  const b = el('button', { class: m[1], 'data-tab': m[2] });
  b.classList.add('tab-btn');
  if (m[1].includes('active')) b.classList.add('active');
  tabbar.appendChild(b);
}
// subtab buttons + panes, attached to their owning pane by document order
const marks = [];
for (const m of html.matchAll(/data-subtab="([a-z-]+)"|id="sub-([a-z-]+)"/g)) {
  marks.push({ sub: m[1], pane: m[2] });
}
const paneOrder = panes.map(p => 'pane-' + p[2]);
let currentPane = null;
for (const m of html.matchAll(/<div class="tab-pane[^"]*" id="pane-([a-z]+)">|data-subtab="([a-z-]+)"|<div class="subtab-pane([^"]*)" id="sub-([a-z-]+)">/g)) {
  if (m[1]) { currentPane = doc.querySelector('#none') || all.find(p => p.attrs.id === 'pane-' + m[1]); }
  else if (m[2]) { const b = el('button', { 'data-subtab': m[2], class: '' }); b.classList.add('subtab-btn'); currentPane.appendChild(b); }
  else if (m[4]) { const p = el('div', { class: m[3], id: 'sub-' + m[4] }); p.classList.add('subtab-pane'); if (m[3].includes('active')) p.classList.add('active'); currentPane.appendChild(p); }
}
// mark first subtab button active per pane (as in markup)
for (const p of all) {
  const btns = p.querySelectorAll('.subtab-btn');
  if (btns.length) btns[0].classList.add('active');
}

// --- globals the code expects -------------------------------------------
const document = {
  getElementById: id => byId[id] || null,
  querySelectorAll: sel => doc.querySelectorAll(sel),
  querySelector: sel => doc.querySelector(sel),
};
const history = { state: null, replaceState(_a, _b, url) { this.state = url; } };
const location = { hash: '' };

const fn = new Function('document', 'history', 'location',
  tabCode + '\nreturn {switchTab, switchSubtab};');
const { switchTab, switchSubtab } = fn(document, history, location);

// --- assertions ----------------------------------------------------------
const pane = id => document.getElementById('pane-' + id);
const activeSub = id => pane(id).querySelectorAll('.subtab-pane').filter(p => p.classList.contains('active')).map(p => p.attrs.id);

assert.deepStrictEqual(activeSub('proxies'), ['sub-ingress'], 'default subtab should be ingress');
assert.deepStrictEqual(activeSub('reality'), ['sub-reality-node'], 'default subtab should be reality-node');

switchTab('proxies', 'awg');
assert.deepStrictEqual(activeSub('proxies'), ['sub-awg'], 'awg subtab should be sole active');
assert(history.state === '#proxies/awg', 'hash should record subtab: ' + history.state);

switchTab('reality');
assert.deepStrictEqual(activeSub('reality'), ['sub-reality-node'], 're-entering group resets to first subtab');
assert(history.state === '#reality/reality-node');

switchTab('users');
assert(pane('users').classList.contains('active'));
assert(!pane('proxies').classList.contains('active'));
assert(history.state === '#users', 'panes without subtabs use plain hash: ' + history.state);

switchSubtab('reality', 'cert');
assert.deepStrictEqual(activeSub('reality'), ['sub-cert']);

// every service lives in exactly one subtab
for (const id of ['proxy-tbody', 'btn-toggle-warp', 'ob-list', 'awg-peer-tbody']) {
  const owner = all.find(p => p.attrs.id.startsWith('pane-') && p._children.length > 0);
}
const counts = all.map(p => [p.attrs.id, p.querySelectorAll('.subtab-pane').length]).filter(x => x[1]);
assert.deepStrictEqual(counts, [['pane-proxies', 4], ['pane-reality', 3]], 'subtab counts: ' + JSON.stringify(counts));
console.log('OK: tab/subtab behaviour verified (proxies=4, reality=3)');
