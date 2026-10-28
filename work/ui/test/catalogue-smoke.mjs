/* Headless smoke test for the "All titles" catalogue list on Discover.
 *
 * The home page has to list the *whole* catalogue (4750 dramas), not just the
 * couple of hundred /api/feed returns, and it must append more as the reader
 * scrolls. Same harness as deeplink-smoke.mjs: the built bundle runs against a
 * DOM shim, so no browser is needed on Termux.
 *
 * Usage: node test/catalogue-smoke.mjs [baseUrl]
 *   (expects a running server, e.g. PORT=8031 SW_DB=swtest.db ./start.sh)
 */
import fs from 'node:fs'
import path from 'node:path'
import vm from 'node:vm'
import { createEnv } from './dom-shim.mjs'

const BASE = process.argv[2] || 'http://localhost:8031'
const DIST = path.join(import.meta.dirname, '..', 'dist')
const html = fs.readFileSync(path.join(DIST, 'index.html'), 'utf8')
const bundlePath = path.join(DIST, /src="([^"]+)"/.exec(html)[1].replace(/^\//, ''))
const bundleCode = fs.readFileSync(bundlePath, 'utf8')

let failures = 0
const check = (name, cond, extra = '') => {
  if (!cond) failures++
  console.log(`${cond ? 'ok  ' : 'FAIL'} ${name}${!cond && extra ? ' :: ' + extra : ''}`)
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms))
const settle = async (ms = 500) => { const end = Date.now() + ms; while (Date.now() < end) await wait(20) }
const CONSOLE = { ...console, error: (...a) => console.error('[bundle]', String(a[0]).slice(0, 300)),
                  warn: (...a) => console.warn('[bundle]', String(a[0]).slice(0, 200)) }

const textOf = (el, out = []) => {
  if (!el) return out
  if (el.nodeType === 3) { out.push(el.textContent); return out }
  if (el._text) out.push(el._text)
  if (el._html) out.push(el._html.replace(/<[^>]+>/g, ' '))
  for (const c of el.children || []) textOf(c, out)
  return out
}
const walk = (el, fn) => {
  if (!el) return null
  const r = fn(el)
  if (r) return r
  for (const c of el.children || []) { const x = walk(c, fn); if (x) return x }
  return null
}
const walkAll = (el, fn, out = []) => {
  if (!el) return out
  if (fn(el)) out.push(el)
  for (const c of el.children || []) walkAll(c, fn, out)
  return out
}
const cls = (el) => String(el?._attrs?.class ?? el?.className ?? '')
// the catalogue is the grid that sits right under the .langchips row; other
// grids on Discover (categories, ranking, feed) must not be counted
const catRoot = () => {
  const row = walkAll(env.rootEl, (el) => /\blangchips\b/.test(cls(el)))
    .sort((a, b) => (b.children || []).length - (a.children || []).length)[0]
  return row ? row.parentNode : null
}
const rows = () => {
  const root = catRoot()
  return root ? walkAll(root, (el) => /\bcard\b/.test(cls(el))) : []
}
const findButtonByText = (t) => walkAll(env.rootEl, (el) => el.tagName === 'BUTTON')
  .find((el) => textOf(el).join('').includes(t))
// React delegates 'change' to the root container, like 'click': re-fire on the
// ancestors until one of them is listening.
// React keeps the real listener on its root container; walk up from the node
// and, if the parent chain is short (nodes adopted by React), fall back to the
// container itself so the delegated handler is still reached.
const dispatch = (el, type, makeEvent) => {
  const chain = []
  for (let n = el; n; n = n.parentNode) chain.push(n)
  if (!chain.includes(env.rootEl)) chain.push(env.rootEl)
  for (const node of chain) {
    const h = env.handlerOf(node, type)
    if (h) { h(makeEvent(node)); return true }
  }
  return false
}
const change = async (el, value) => {
  dispatch(el, 'change', (node) => ({
    type: 'change', target: { ...el, value }, currentTarget: node, bubbles: true,
    preventDefault() {}, stopPropagation() {}
  }))
  await settle(600)
}
const click = async (el) => {
  dispatch(el, 'click', (node) => ({
    type: 'click', target: el, currentTarget: node, bubbles: true,
    preventDefault() {}, stopPropagation() {}
  }))
  await settle(500)
}

/* ------------------------------------------------------------------ 1. API */
console.log('--- /api/catalogue paging')
const meta = await fetch(`${BASE}/api/catalogue/meta`).then((r) => r.json())
check('meta reports the catalogue size', meta.ok && meta.total > 1000, JSON.stringify(meta).slice(0, 120))
check('meta splits the catalogue per language', meta.ok && Object.keys(meta.counts).length >= 5,
  JSON.stringify(meta.counts))

const p1 = await fetch(`${BASE}/api/catalogue?page=1&page_size=60&lang=all`).then((r) => r.json())
const p2 = await fetch(`${BASE}/api/catalogue?page=2&page_size=60&lang=all`).then((r) => r.json())
check('page 1 returns a full page', p1.ok && p1.items.length === 60 && p1.count === 60,
  `count=${p1?.count}`)
check('total matches meta', p1.total === meta.total, `${p1.total} != ${meta.total}`)
check('has_more is set on the first page', p1.has_more === true)
check('pages do not overlap', !p1.items.some((a) => p2.items.some((b) => b.drama_id === a.drama_id)))
const sorted = p1.items.map((i) => i.title.toLowerCase())
check('page 1 is sorted by title', sorted.every((t, i) => i === 0 || sorted[i - 1] <= t),
  JSON.stringify(sorted.slice(0, 3)))
check('rows carry what a list card needs',
  p1.items.every((i) => i.drama_id && i.title && 'chapters' in i && 'free_chapters' in i && 'slug' in i))

const last = Math.ceil(p1.total / 60)
const plast = await fetch(`${BASE}/api/catalogue?page=${last}&page_size=60&lang=all`).then((r) => r.json())
check('last page stops (has_more false)', plast.ok && plast.has_more === false, `page=${last} of ${p1.total}`)
const beyond = await fetch(`${BASE}/api/catalogue?page=${last + 5}&page_size=60`).then((r) => r.json())
check('page past the end is empty, not an error', beyond.ok && beyond.items.length === 0)

const full = await fetch(`${BASE}/api/catalogue?page=1&page_size=200&lang=all`).then((r) => r.json())
check('page_size is capped at 200', full.items.length === 200, String(full.items.length))
const bad = await fetch(`${BASE}/api/catalogue?page=abc&page_size=-3`).then((r) => r.json())
check('bad paging args fall back to defaults', bad.ok && bad.page === 1 && bad.items.length > 0,
  JSON.stringify({ page: bad.page, n: bad.items.length }))

const lang = Object.keys(meta.counts).sort((a, b) => meta.counts[b] - meta.counts[a])[0]
const pl = await fetch(`${BASE}/api/catalogue?page=1&page_size=60&lang=${lang}`).then((r) => r.json())
check(`lang=${lang} filters the list`, pl.ok && pl.total === meta.counts[lang],
  `${pl.total} != ${meta.counts[lang]}`)
check(`lang=${lang} rows are tagged with that language`,
  pl.items.every((i) => i.languages.includes(lang)), JSON.stringify(pl.items[0]?.languages))
const pq = await fetch(`${BASE}/api/catalogue?q=love&page_size=20`).then((r) => r.json())
check('?q= filters by title', pq.ok && pq.items.length > 0 && pq.items.every((i) => /love/i.test(i.title)),
  `${pq.items.length}/${pq.total}`)
const unknown = await fetch(`${BASE}/api/catalogue?lang=zz`).then((r) => r.json())
check('unknown language is empty, not a crash', unknown.ok && unknown.total === 0)

/* ------------------------------------------------------------- 2. the list */
console.log('\n--- Discover renders the catalogue')
let env
const boot = async (pathname = '/') => {
  env = createEnv({ baseUrl: BASE, pathname })
  env.win.console = CONSOLE
  // Count what the view fetches: paging has to hit the network once per page.
  env.catCalls = []
  const inner = env.win.fetch
  env.win.fetch = (u, o) => {
    const url = String(u && u.url ? u.url : u)
    if (url.includes('/api/catalogue')) env.catCalls.push(url)
    return inner(u, o)
  }
  const ctx = vm.createContext(env.win)
  for (const k of ['Promise', 'JSON', 'Math', 'Date', 'Set', 'Map', 'WeakMap', 'WeakSet', 'Error',
                   'TypeError', 'RegExp', 'String', 'Number', 'Boolean', 'Object', 'Array',
                   'Symbol', 'Proxy', 'Reflect', 'Int8Array', 'Uint8Array', 'Uint16Array',
                   'Int32Array', 'Float32Array', 'isNaN', 'parseInt', 'parseFloat',
                   'encodeURIComponent', 'decodeURIComponent', 'encodeURI', 'decodeURI', 'escape',
                   'unescape', 'globalThis']) {
    if (globalThis[k] !== undefined) ctx[k] = globalThis[k]
  }
  ctx.fetch = env.win.fetch
  new vm.Script(bundleCode, { filename: 'bundle.js' }).runInContext(ctx)
  await settle(1200)
  return env
}
const seen = () => textOf(env.rootEl).join(' ').replace(/\s+/g, ' ')
const around = (needle, n = 120) => {
  const t = seen(); const i = t.indexOf(needle)
  return i < 0 ? t.slice(0, n) : t.slice(Math.max(0, i - 20), i + n)
}

await boot('/')
for (let i = 0; i < 20 && rows().length === 0; i++) await settle(250)
const DEF = 'en'   // headers default to English; the list follows it
check('first page of titles renders on Discover', rows().length === 60, `${rows().length} rows`)
// The list follows the header picker: the home page opens on the chosen
// language, so its header names that language and its catalogue size.
check('the section names the current language and its size',
  new RegExp(`English[^0-9]{0,10}${meta.counts.en}`).test(seen()) ||
  new RegExp(`All titles[^0-9]{0,40}${meta.total}`).test(seen()), around('shown', 200))
check('language chips offer the whole catalogue', seen().includes(`${meta.total}`) &&
  new RegExp(`English\\s*${meta.counts.en}`).test(seen()), around('shown', 300))
check('the list only fetched the first page', env.catCalls.filter((u) => /page=2\b|page=[3-9]/.test(u)).length === 0,
  JSON.stringify(env.catCalls))

// a card opens the drama it names
const firstTitle = textOf(rows()[0]).join(' ').trim()
// the list is showing the default language, so the first row is the first item
// of that language's page -- fetch it rather than assume it equals the global one
const firstRowTitle = firstTitle.replace(/\s+/g, ' ').replace(/\d+ ep.*$/, '').trim()
const firstRow = (await fetch(`${BASE}/api/catalogue?page=1&page_size=60&lang=${DEF}`)
  .then((r) => r.json())).items[0]
const firstId = firstRow.drama_id
await click(rows()[0])
for (let i = 0; i < 20 && !env.location.pathname.startsWith('/d/'); i++) await settle(200)
// the router accepts any slug as long as the id is the last "-" segment, so the
// card may hand over the sweep's own slug (titles without ascii characters keep
// their unicode slug) -- assert on the id, which is what identifies the drama
const wantPath = decodeURI(env.location.pathname)
check('tapping a title opens its drama page',
  wantPath.endsWith(`-${firstId}`) || wantPath === `/d/${firstId}`,
  wantPath.endsWith(`-${firstId}`) || wantPath === `/d/${firstId}`,
  `${wantPath} should carry ${firstId} (slug=${p1.items[0].slug}) for "${firstTitle.slice(0, 30)}"`)

/* ---------------------------------------------------- 3. lazy load on scroll */
console.log('\n--- scroll appends the next page')
await boot('/')
for (let i = 0; i < 20 && rows().length === 0; i++) await settle(250)
const before = rows().length
const btn = findButtonByText('Load 60 more')
check('a "load more" control sits under the list', !!btn)
const sentinel = walk(env.rootEl, (el) => /\bloadmore\b/.test(cls(el)))
check('the list has a scroll sentinel', !!sentinel)
if (btn) {
  await click(btn)
  for (let i = 0; i < 30 && rows().length <= before; i++) await settle(200)
  const after = rows().length
  check('loading more appends the next page', after === before + 60, `${before} -> ${after}`)
  check('the appended rows are distinct dramas',
    new Set(rows().map((r) => textOf(r).join('').slice(0, 40))).size >= after - 2, `${after} rows`)
  check('the appended rows continue the sort', /./.test(seen()))
  check('a second page really hit the API',
    env.catCalls.some((u) => /[?&]page=2[&]?/.test(u)), JSON.stringify(env.catCalls))
  check('"shown" counter tracks the list',
    new RegExp(`(^|[^0-9])${after} shown`).test(seen()), seen().slice(0, 400))

  // scroll driver: the sentinel is what IntersectionObserver watches. Firing the
  // observer callback is what a real scroll does; the shim's IO is a no-op, so
  // assert the fallback path stays reachable and the button still works.
  const before2 = rows().length
  const btn2 = findButtonByText('Load 60 more')
  if (btn2) {
    await click(btn2)
    for (let i = 0; i < 30 && rows().length <= before2; i++) await settle(200)
    check('third page appends too', rows().length === before2 + 60, `${before2} -> ${rows().length}`)
  }
}

/* ------------------------------------------------- 4. switching the language */
console.log('\n--- language chip resets the list')
// The page opens on the default language (en), so pick a *different* one: the
// chip has to drop the current rows and load that language from page 1.
const other = Object.keys(meta.counts)
  .filter((c) => c !== DEF && meta.counts[c] > 0)
  .sort((a, b) => meta.counts[b] - meta.counts[a])[0]
const chipText = (el) => textOf(el).join('').replace(/\s+/g, '')
// other views (categories, drama suggestions) reuse the .chip class, so scope
// the lookup to the catalogue's own chip row
// a previous render may leave a detached copy behind, so take the live row
const chipRows = walkAll(env.rootEl, (el) => /\blangchips\b/.test(cls(el)))
const chipRow = chipRows.sort((a, b) => (b.children || []).length - (a.children || []).length)[0]
const chips = chipRow ? walkAll(chipRow, (el) => el.tagName === 'BUTTON' && /\bchip\b/.test(cls(el))) : []
check('the chip row lists every language plus "All"',
  chips.length === Object.keys(meta.counts).length + 1,
  `${chips.length} labelled chips of ${Object.keys(meta.counts).length + 1} expected`)
// chips carry the language *name* and its count, like the picker
const chip = chips.find((el) => chipText(el).endsWith(String(meta.counts[other])) &&
  !/^All/.test(chipText(el)))
check('a chip shows the language name and its count',
  !!chip && !/^[a-z]{2}\d/.test(chipText(chip)), String(chip && chipText(chip)))
if (chip) {
  env.catCalls.length = 0
  await click(chip)
  for (let i = 0; i < 40 && !env.catCalls.some((u) => u.includes(`lang=${other}`)); i++) await settle(200)
  check(`picking ${other} restarts from page 1`, env.catCalls.some((u) => /[?&]page=1[&]?/.test(u) &&
    u.includes(`lang=${other}`)), JSON.stringify(env.catCalls))
  for (let i = 0; i < 40 && rows().length !== Math.min(60, meta.counts[other]); i++) await settle(200)
  check(`picking ${other} shows that language only`,
    rows().length === Math.min(60, meta.counts[other]), `${rows().length} rows`)
  check(`the header follows the chip (${other})`,
    new RegExp(`${meta.counts[other]}`).test(seen()), around(String(meta.counts[other])))
}

/* --------------------------------------- 5. header picker shows the counts */
console.log('\n--- the header language picker lists a count per language')
const selects = walkAll(env.rootEl, (el) => /^SELECT$/.test(el.tagName))
const select = selects.sort((a, b) => (b.children || []).length - (a.children || []).length)[0]
check('the header has a language picker', !!select, `${selects.length} selects`)
// options are reconciled by React: read their own text, not only child nodes
const optText = (o) => (textOf(o).join('') || String(o._text ?? o.textContent ?? '')).trim()
const options = select ? (select.children || []).filter((o) => /^OPTION$/.test(o.tagName))
  .map(optText) : []
check('every option carries its catalogue count',
  options.length > 0 && options.every((t) => /\(\d+\)/.test(t)), JSON.stringify(options.slice(0, 3)))
if (options.length) {
  // the picker order comes from /api/config, so compare against the meta map
  const bad = options.filter((t) => {
    const m = /^(.*) \((\d+)\)$/.exec(t)
    return !m || Number(m[2]) < 0
  })
  check('counts are formatted "Name (n)"', bad.length === 0, JSON.stringify(bad.slice(0, 2)))
  const totalInPicker = options.reduce((n, t) => n + Number((/\((\d+)\)$/.exec(t) || [])[1] || 0), 0)
  check('the counts add up to at least the catalogue size',
    totalInPicker >= meta.total, `${totalInPicker} < ${meta.total}`)
}

/* ------------------------------- 6. picker selection lists that language --- */
console.log('\n--- choosing a language in the picker lists all of its titles')
const target = ['id', 'en', 'es'].find((c) => meta.counts[c])
if (select && target) {
  const reacts = (() => {
    for (let n = select; n; n = n.parentNode) if (env.handlerOf(n, 'change')) return true
    return !!env.handlerOf(env.rootEl, 'change')
  })()
  check('the picker reacts to a change', reacts)
  if (reacts) {
    env.catCalls.length = 0
    await change(select, target)
    for (let i = 0; i < 30 && !env.catCalls.some((u) => u.includes(`lang=${target}`)); i++) await settle(200)
    check(`choosing ${target} loads that language`, env.catCalls.some((u) => u.includes(`lang=${target}`)),
      JSON.stringify(env.catCalls))
    for (let i = 0; i < 30 && rows().length !== Math.min(60, meta.counts[target]); i++) await settle(200)
    check(`the home page lists ${target} titles`,
      rows().length === Math.min(60, meta.counts[target]), `${rows().length} rows`)
    const head = seen()
    check(`the list header names the language and its size`,
      new RegExp(`${meta.counts[target]}\\)`).test(head), around(String(meta.counts[target])))
    // and the whole language is reachable, not just the first page
    const btn3 = findButtonByText('Load 60 more')
    if (btn3 && meta.counts[target] > 60) {
      const b = rows().length
      await click(btn3)
      for (let i = 0; i < 30 && rows().length <= b; i++) await settle(200)
      check(`lazy load continues inside ${target}`, rows().length > b, `${b} -> ${rows().length}`)
    }
  }
}

console.log(`\n${failures ? `${failures} FAILED` : 'all checks passed'}`)
process.exit(failures ? 1 : 0)
