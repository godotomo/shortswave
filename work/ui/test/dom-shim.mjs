/* Minimal DOM shim: just enough of the platform for react-dom/client to mount a
 * small app in a Node vm context (no jsdom available in Termux).
 *
 * Everything is an object with a tagName/children list; we record React's work
 * and later walk the tree by hand (see deeplink-smoke.mjs).
 */

export const ALL_ELEMENTS = [
  'a', 'article', 'aside', 'b', 'button', 'div', 'em', 'footer', 'form', 'h1', 'h2',
  'h3', 'h4', 'header', 'i', 'img', 'input', 'label', 'li', 'main', 'nav', 'p',
  'section', 'select', 'small', 'span', 'strong', 'ul', 'video', 'source', 'track',
]

export function createEnv({ baseUrl, pathname = '/', storage = new Map() }) {
  const listeners = new Map()
  const on = (target, type, fn) => {
    if (!listeners.has(target)) listeners.set(target, new Map())
    // a DOM target may hold several listeners per type (the app adds popstate,
    // React adds media/scroll hooks): keep them all in order
    const byType = listeners.get(target)
    if (!byType.has(type)) byType.set(type, [])
    const list = byType.get(type)
    if (!list.includes(fn)) list.push(fn)
  }
  const off = (target, type, fn) => {
    const list = listeners.get(target)?.get(type)
    if (!list) return
    const k = list.indexOf(fn)
    if (k >= 0) list.splice(k, 1)
  }
  const handlerOf = (target, type) => {
    const list = listeners.get(target)?.get(type)
    if (!list || !list.length) return undefined
    return (ev) => { for (const fn of [...list]) fn(ev) }
  }

  let doc = null
  const makeEl = (tag, ns) => {
    const el = {
      nodeType: 1,
      tagName: String(tag).toUpperCase(),
      nodeName: String(tag).toUpperCase(),
      namespaceURI: ns || 'http://www.w3.org/1999/xhtml',
      children: [],
      childNodes: [],
      style: {},
      dataset: {},
      get ownerDocument() { return doc },
      parentNode: null,
      selected: false, checked: false, value: '', disabled: false, multiple: false,
      scrollTop: 0, scrollLeft: 0, scrollHeight: 900, clientHeight: 900, clientWidth: 430,
      _attrs: {}, _text: '', _html: '',
      appendChild(c) {
        if (c && c.nodeType === 11) { for (const k of c.children.slice()) this.appendChild(k); c.children = []; c.childNodes = []; return c }
        c.parentNode = this
        this.children.push(c); this.childNodes.push(c)
        if (c && c.nodeName === 'OPTION') this.options.push(c)
        return c
      },
      insertBefore(c, ref) {
        c.parentNode = this
        const i = this.children.indexOf(ref)
        if (c && c.nodeName === 'OPTION') this.options.push(c)
        if (i < 0) { this.children.push(c); this.childNodes.push(c) } else { this.children.splice(i, 0, c); this.childNodes.splice(i, 0, c) }
        return c
      },
      removeChild(c) {
        this.children = this.children.filter((x) => x !== c)
        this.childNodes = this.childNodes.filter((x) => x !== c)
        return c
      },
      replaceChild(a, b) { this.removeChild(b); return this.appendChild(a) },
      remove() { this.parentNode?.removeChild(this) },
      setAttribute(k, v) { this._attrs[k] = String(v) },
      getAttribute(k) { return Object.prototype.hasOwnProperty.call(this._attrs, k) ? this._attrs[k] : null },
      hasAttribute(k) { return Object.prototype.hasOwnProperty.call(this._attrs, k) },
      removeAttribute(k) { delete this._attrs[k] },
      setAttributeNS(_ns, k, v) { this._attrs[k] = String(v) },
      addEventListener(t, f) { on(this, t, f) },
      removeEventListener(t, f) { off(this, t, f) },
      attachEvent() {}, detachEvent() {},
      dispatchEvent() { return true },
      focus() {}, blur() {}, click() { handlerOf(this, 'click')?.({ type: 'click', target: this, currentTarget: this, preventDefault() {}, stopPropagation() {} }) },
      getBoundingClientRect() { return { x: 0, y: 0, width: 430, height: 900, top: 0, left: 0, right: 430, bottom: 900 } },
      getClientRects() { return [] },
      getContext() { return null },
      querySelector() { return null },
      querySelectorAll() { return [] },
      contains(other) {
        if (other === this) return true
        return this.children.some((c) => c && c.contains && c.contains(other))
      },
      type: String(tag).toLowerCase() === 'input' ? 'text' : undefined,
      selectedIndex: 0,
      options: [],
      get elements() { return this.children },
      get firstChild() { return this.children[0] || null },
      get lastChild() { return this.children[this.children.length - 1] || null },
      get nextSibling() {
        const p = this.parentNode
        if (!p) return null
        return p.children[p.children.indexOf(this) + 1] || null
      },
      get parentElement() { return this.parentNode },
      get nextElementSibling() { return this.nextSibling },
      get textContent() {
        if (this.children.length) return this.children.map((c) => c.textContent).join('')
        return this._text
      },
      set textContent(v) { this._text = String(v); this.children = []; this.childNodes = [] },
      get nodeValue() { return this._text },
      set nodeValue(v) { this._text = String(v) },
      get innerHTML() { return this._html },
      set innerHTML(v) { this._html = String(v) },
      get innerText() { return this._text },
      set innerText(v) { this._text = String(v) },
      get outerHTML() { return `<${tag}>` },
    }
    // React reads getOwnPropertyDescriptor(el.constructor.prototype, 'value') to
    // decide whether to install a value tracker, so give every element a real
    // constructor function with an own prototype object.
    const ctor = function HTMLUnknownElement() {}
    ctor.prototype = { value: undefined, checked: undefined, selected: undefined }
    el.constructor = ctor
    return el
  }
  // React updates a text node through `node.nodeValue = v` (commitTextUpdate),
  // never through textContent. If the two were independent plain properties the
  // shim would show the mount-time text forever, so make them one accessor pair.
  const textNode = (t) => {
    let val = String(t)
    const node = {
      nodeType: 3, nodeName: '#text', tagName: undefined,
      parentNode: null,
      get ownerDocument() { return doc }, children: [], childNodes: [],
      get firstChild() { return null }, get nextSibling() { return null },
      addEventListener() {}, removeEventListener() {},
      contains() { return false },
    }
    for (const k of ['textContent', 'nodeValue', 'data', 'innerText']) {
      Object.defineProperty(node, k, {
        get: () => val, set: (v) => { val = String(v) }, configurable: true, enumerable: true,
      })
    }
    return node
  }

  const body = makeEl('body')
  const head = makeEl('head')
  const documentElement = makeEl('html')
  const rootEl = makeEl('div')
  rootEl.setAttribute('id', 'root')
  documentElement.appendChild(head)
  documentElement.appendChild(body)
  body.appendChild(rootEl)

  const store = storage
  const location = {
    pathname, search: '', hash: '', origin: baseUrl,
    get href() { return this.origin + this.pathname + this.search + this.hash },
    set href(v) { const u = new URL(v, baseUrl); this.pathname = u.pathname; this.search = u.search; this.hash = u.hash },
    assign(v) { this.href = v }, replace(v) { this.href = v }, reload() {},
    toString() { return this.href },
  }
  const history = {
    state: null, length: 1,
    pushState(s, _t, url) { this.state = s; this.length++; if (url) location.href = url },
    replaceState(s, _t, url) { this.state = s; if (url) location.href = url },
    // real back/forward fire popstate; the shim will not guess, tests call
    // env.win.dispatchEvent({ type: 'popstate' }) after moving location themselves
    back() {}, forward() {}, go() {},
  }

  doc = {
    nodeType: 9,
    readyState: 'complete',
    visibilityState: 'visible',
    hidden: false,
    title: 'ShortsWave',
    documentMode: undefined,
    createElement: (t) => makeEl(t),
    createElementNS: (ns, t) => makeEl(t, ns),
    createTextNode: textNode,
    createComment: (t) => ({ nodeType: 8, textContent: String(t), nodeName: '#comment' }),
    createDocumentFragment: () => { const f = makeEl('fragment'); f.nodeType = 11; return f },
    getElementById: (id) => (id === 'root' ? rootEl : null),
    querySelector: (sel) => (sel === '#root' ? rootEl : null),
    querySelectorAll: () => [],
    addEventListener(t, f) { on(doc, t, f) },
    removeEventListener(t, f) { off(doc, t, f) },
    dispatchEvent() { return true },
    hasFocus: () => true,
    getSelection: () => ({ rangeCount: 0, addRange() {}, removeAllRanges() {}, extend() {}, toString: () => '' }),
    createRange: () => ({ setStart() {}, setEnd() {}, getBoundingClientRect: () => ({}) }),
    createEvent: () => ({ initEvent() {}, initCustomEvent() {} }),
    execCommand: () => false,
    body, head, documentElement,
    activeElement: body,
    defaultView: null,
  }

  const win = {
    document: doc,
    location, history,
    navigator: { userAgent: 'node-deeplink-smoke', language: 'en', languages: ['en'], platform: 'linux' },
    localStorage: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => { store.set(k, String(v)) },
      removeItem: (k) => { store.delete(k) },
      clear: () => store.clear(),
      key: (i) => [...store.keys()][i] ?? null,
      get length() { return store.size },
    },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    setTimeout, clearTimeout,
    setInterval: () => 0, clearInterval: () => {},
    requestAnimationFrame: (f) => setTimeout(() => f(Date.now()), 0),
    cancelAnimationFrame: (h) => clearTimeout(h),
    requestIdleCallback: (f) => setTimeout(() => f({ didTimeout: false, timeRemaining: () => 20 }), 0),
    cancelIdleCallback: (h) => clearTimeout(h),
    queueMicrotask,
    scrollTo() {}, scrollBy() {}, scrollY: 0, scrollX: 0,
    innerWidth: 430, innerHeight: 900, devicePixelRatio: 2,
    matchMedia: () => ({ matches: false, media: '', addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    addEventListener(t, f) { on(win, t, f) },
    removeEventListener(t, f) { off(win, t, f) },
    dispatchEvent() { return true },
    fetch: (p, o) => fetch(new URL(p, baseUrl), o),
    console, URL, URLSearchParams, TextEncoder, TextDecoder, performance,
    AbortController, DOMException, Blob: function Blob() {}, FormData: function FormData() {},
    Event: function Event(t) { this.type = t }, CustomEvent: function CustomEvent(t) { this.type = t },
    MutationObserver: class { observe() {} disconnect() {} takeRecords() { return [] } },
    IntersectionObserver: class { observe() {} unobserve() {} disconnect() {} },
    ResizeObserver: class { observe() {} unobserve() {} disconnect() {} },
    Image: function Image() { return makeEl('img') },
    HTMLElement: function HTMLElement() {},
    HTMLIFrameElement: function HTMLIFrameElement() {},
    Element: function Element() {},
    Node: function Node() {},
    getSelection: () => ({ rangeCount: 0, addRange() {}, removeAllRanges() {} }),
  }
  for (const tag of ALL_ELEMENTS) {
    const name = tag.replace(/(^|-)([a-z])/g, (_m, _d, c) => c.toUpperCase())
    win['HTML' + name + 'Element'] = function () {}
  }
  win.window = win
  win.self = win
  win.globalThis = win
  win.top = win
  win.parent = win
  win.frames = win
  doc.defaultView = win
  win.HTMLMediaElement = function HTMLMediaElement() {}

  return { win, doc, rootEl, body, listeners, handlerOf, location, history, store }
}
