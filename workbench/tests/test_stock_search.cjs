const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const source = fs.readFileSync(path.join(__dirname, '../dashboard/app.js'), 'utf8');
const start = source.indexOf('function mountStockSearch()');
const end = source.indexOf('\nfunction marketStatus()', start);
assert.ok(start >= 0 && end > start, 'stock search controller exists');
const escapeCode = source.slice(source.indexOf('const esc ='), source.indexOf('\nconst num ='));
const code = escapeCode + '\n' + source.slice(start, end);
const flush = () => new Promise(resolve => setImmediate(resolve));

function element() {
 const listeners = new Map(), attributes = new Map(), classes = new Set();
 return {
  children: [], dataset: {}, value: '', hidden: false, disabled: false, textContent: '',
  classList: {
   add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name),
   toggle(name, state) { if (state ?? !classes.has(name)) classes.add(name); else classes.delete(name); }
  },
  setAttribute(name, value) { attributes.set(name, String(value)); },
  getAttribute(name) { return attributes.get(name) ?? null; },
  removeAttribute(name) { attributes.delete(name); },
  addEventListener(name, fn) { const handlers = listeners.get(name) || []; handlers.push(fn); listeners.set(name, handlers); },
  fire(name, fields = {}) {
   const event = {target: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...fields};
   for (const listener of listeners.get(name) || []) listener(event);
   return event;
  },
  append(child) { this.children.push(child); },
  replaceChildren(...children) { this.children = children; },
  contains(target) { return this === target || this.children.some(child => child.contains(target)); },
  scrollIntoView() { this.scrolled = true; }
 };
}
function setup() {
 const host = element(), input = element(), clear = element(), panel = element();
 const list = element(), status = element(), note = element(), document = element(), window = element();
 const nodes = {input, button: clear, '.stock-search-panel': panel, '[role="listbox"]': list, '[role="status"]': status, '.stock-search-note': note};
 host.querySelector = selector => nodes[selector];
 host.children = [input, clear, panel]; panel.children = [list, status, note]; panel.hidden = clear.hidden = input.disabled = true;
 document.getElementById = id => id === 'stock-search' ? host : null;
 document.createElement = element;
 document.activeElement = input;
 input.blur = () => { document.activeElement = null; host.fire('focusout', {relatedTarget: null}); };
 input.focus = () => { document.activeElement = input; input.fire('focus'); };
 const timers = new Map(), requests = [], location = {hash: '#home'};
 let serial = 0;
 const context = vm.createContext({document, window, location, AbortController,
  setTimeout(fn, delay) { const id = ++serial; timers.set(id, {fn, delay}); return id; },
  clearTimeout(id) { timers.delete(id); },
  fetch(url, options) {
   return new Promise((resolve, reject) => requests.push({url, options, resolve, reject}));
  }
 });
 vm.runInContext(code, context);
 context.mountStockSearch();
 return {context, host, input, clear, panel, list, status, note, document, window, timers, requests, location,
  type(value) { input.value = value; input.fire('input'); },
  key(key, fields = {}) { return input.fire('keydown', {key, ...fields}); },
  tick() {
   const [id, timer] = [...timers.entries()][0] || [];
   assert.ok(timer, 'debounce is pending'); assert.equal(timer.delay, 150);
   timers.delete(id); timer.fn();
  },
  async reply(index, items, total = items.length, ok = true) {
   requests[index].resolve({ok, json: async () => ({items, total})}); await flush();
  },
  async results(value = 'nvd', items = stocks) { this.type(value); this.tick(); await this.reply(requests.length - 1, items); }
 };
}
const stocks = [
 {market: 'US', symbol: 'NVDA', name: 'NVIDIA Corp', exchange: 'NASDAQ'},
 {market: 'US', symbol: 'NVD', name: 'NVIDIA Bear ETF', exchange: 'NASDAQ'}
];
(async () => {
 let h = setup();
 assert.equal(h.input.disabled, false);
 h.context.mountStockSearch();
 h.type('n'); h.type('nv'); h.type('nvd');
 assert.equal(h.timers.size, 1, 'rapid input and repeated mounting create one debounce');
 assert.equal(h.requests.length, 0); assert.match(h.status.textContent, /검색 중/);
 h.tick(); assert.equal(h.requests.length, 1); assert.equal(h.requests[0].url, '/api/stock-search?q=nvd');
 await h.reply(0, stocks, 15);
 assert.equal(h.list.children.length, 2); assert.match(h.note.textContent, /2 \/ 15개/);
 assert.equal(h.input.getAttribute('aria-expanded'), 'true');
 assert.equal(h.input.getAttribute('aria-activedescendant'), h.list.children[0].id);
 assert.equal(h.list.children[0].getAttribute('aria-selected'), 'true');
 assert.match(h.list.children[0].innerHTML, /<mark>NVD<\/mark>A/);
 assert.equal(h.key('ArrowDown').defaultPrevented, true);
 assert.equal(h.list.children[1].getAttribute('aria-selected'), 'true'); assert.equal(h.list.children[1].scrolled, true);
 h.key('ArrowDown'); assert.equal(h.list.children[0].getAttribute('aria-selected'), 'true', 'Down wraps');
 h.key('ArrowUp'); assert.equal(h.list.children[1].getAttribute('aria-selected'), 'true', 'Up wraps');
 h.key('Enter'); assert.equal(h.location.hash, '#stock/US/NVD');
 assert.equal(h.input.value, ''); assert.equal(h.panel.hidden, true); assert.equal(h.clear.hidden, true);
 assert.equal(h.input.getAttribute('aria-activedescendant'), null);

 h = setup(); h.type('nv'); h.tick(); h.type('삼성');
 assert.equal(h.requests[0].options.signal.aborted, true, 'new input aborts the old network request');
 h.tick(); const korean = [{market: 'KR', symbol: '005930.KS', name: '삼성전자', exchange: 'KOSPI'}];
 await h.reply(1, korean); await h.reply(0, stocks);
 assert.equal(h.list.children.length, 1, 'late successful response cannot replace the latest query');
 assert.match(h.list.children[0].innerHTML, /<mark>삼성<\/mark>전자/); assert.match(h.list.children[0].innerHTML, /005930<\/span>/);
 assert.equal(h.requests[1].url, '/api/stock-search?q=' + encodeURIComponent('삼성'));
 h.key('Enter'); assert.equal(h.location.hash, '#stock/KR/005930.KS', 'navigation retains the canonical market-qualified symbol');

 h = setup(); h.type('nv'); h.tick(); h.type('삼성'); h.tick(); await h.reply(1, korean);
 h.requests[0].reject(Error('old offline response')); await flush();
 assert.match(h.list.children[0].innerHTML, /삼성/); assert.equal(h.status.classList.contains('stock-search-sr'), true, 'old failure cannot replace new results');

 h = setup(); await h.results();
 h.input.fire('compositionstart'); assert.equal(h.panel.hidden, true);
 h.type('삼'); assert.equal(h.timers.size, 0); assert.equal(h.key('Enter').defaultPrevented, false);
 assert.equal(h.location.hash, '#home', 'IME confirmation does not select a result');
 h.input.fire('compositionend'); h.input.fire('input');
 assert.equal(h.timers.size, 1); h.tick(); await h.reply(1, korean);
 assert.equal(h.key('Enter', {isComposing: true}).defaultPrevented, false);
 assert.equal(h.key('Enter', {keyCode: 229}).defaultPrevented, false);
 assert.equal(h.location.hash, '#home'); h.key('Enter'); assert.equal(h.location.hash, '#stock/KR/005930.KS');

 h = setup(); h.type('absent'); h.tick(); await h.reply(0, []);
 assert.equal(h.list.children.length, 0); assert.match(h.status.textContent, /일치하는 종목이 없습니다/);
 h.key('Enter'); assert.equal(h.location.hash, '#home');
 h.type('nv'); h.tick(); await h.reply(1, [], 0, false);
 assert.match(h.status.textContent, /불러오지 못했습니다/); assert.equal(h.input.getAttribute('aria-activedescendant'), null);
 await h.results(); assert.equal(h.list.children.length, 2, 'a later edit recovers from a failed request');
 h.type('nvdx'); h.tick(); h.requests.at(-1).reject(Error('offline')); await flush();
 assert.match(h.status.textContent, /불러오지 못했습니다/);

 h = setup(); await h.results(); h.key('Escape');
 assert.equal(h.panel.hidden, true); assert.equal(h.input.value, 'nvd');
 assert.equal(h.input.getAttribute('aria-expanded'), 'false'); assert.equal(h.list.children.length, 0);
 h.key('Enter'); assert.equal(h.location.hash, '#home', 'Enter on a closed popup does not navigate');
 h.key('ArrowDown'); assert.equal(h.panel.hidden, false); h.tick(); await h.reply(1, stocks);
 h.key('Tab'); assert.equal(h.panel.hidden, true);
 h.input.focus(); h.tick(); await h.reply(2, stocks);
 h.clear.fire('click'); assert.equal(h.input.value, ''); assert.equal(h.clear.hidden, true);
 assert.equal(h.panel.hidden, true); assert.equal(h.document.activeElement, h.input); assert.equal(h.timers.size, 0);
 h.type('   '); assert.equal(h.timers.size, 0); assert.equal(h.panel.hidden, true);

 for (const dismiss of [
  h => h.host.fire('focusout', {relatedTarget: element()}),
  h => h.document.fire('pointerdown', {target: element()}),
  h => h.window.fire('hashchange')
 ]) {
  h = setup(); h.type('nv'); h.tick(); dismiss(h); await h.reply(0, stocks);
  assert.equal(h.panel.hidden, true); assert.equal(h.list.children.length, 0, 'pending response cannot reopen dismissed results');
  assert.equal(h.requests[0].options.signal.aborted, true);
 }
 h = setup(); await h.results();
 h.host.fire('focusout', {relatedTarget: h.clear}); assert.equal(h.panel.hidden, false, 'focus inside search does not dismiss it');
 assert.equal(h.list.children[1].fire('pointerdown').defaultPrevented, true, 'click selection keeps the input focused');
 h.list.children[1].fire('pointermove'); assert.equal(h.list.children[1].getAttribute('aria-selected'), 'true');
 h.list.children[1].fire('click'); assert.equal(h.location.hash, '#stock/US/NVD');

 h = setup(); await h.results('<', [{market: 'US', symbol: 'A/B #<', name: '<img src=x onerror=alert(1)> & "quote"', exchange: '<script>alert(1)</script>'}]);
 const rendered = h.list.children[0].innerHTML;
 assert.doesNotMatch(rendered, /<img|<script|<\/script>/);
 assert.match(rendered, /<mark>&lt;<\/mark>img/); assert.match(rendered, /&amp;/); assert.match(rendered, /&quot;quote&quot;/);
 assert.match(rendered, /&lt;script&gt;/);
 h.key('Enter'); assert.equal(h.location.hash, '#stock/US/' + encodeURIComponent('A/B #<'));
 console.log('PASS: stock autocomplete debounce/stale requests, keyboard and Korean IME, safe rendering, canonical navigation, empty/error recovery, clear/focus/blur/outside/route dismissal.');
})().catch(error => { console.error(error); process.exitCode = 1; });
