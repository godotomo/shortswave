import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { dramaPath, h264Url, hevcSupported, parseDramaPath } from './slug'

/* ------------------------------------------------------------------ helpers */
// GET cache for the whole tab lifetime. Without it every view remount (open a
// drama, go back) re-fires /api/rank, /api/config…, and a failing endpoint turns
// into a refetch loop: fetch -> setState -> render -> effect -> fetch.
const inflight = new Map()
const api = async (path, opts) => {
  // `nocache: true` = live endpoint (unlock status): never join a pending call.
  // Status polls must always be fresh -- one stale answer must not be replayed
  // to the whole loop, and dropping `opts` here used to make fetch() blow up.
  const nocache = !!opts?.nocache
  const cacheable = !nocache && (!opts || opts.method === undefined || opts.method === 'GET')
  if (cacheable && inflight.has(path)) return inflight.get(path)
  const run = (async () => {
    try {
      const r = await fetch(path, opts)
      const j = await r.json().catch(() => ({ ok: false, msg: `HTTP ${r.status}` }))
      if (!j.ok && j.msg === undefined) j.msg = `HTTP ${r.status}`
      return j
    } catch (e) {
      return { ok: false, msg: String(e) }
    }
  })()
  if (!cacheable) return run
  inflight.set(path, run)
  const res = await run
  // do not cache a failure; let the next caller retry
  if (!res || !res.ok) inflight.delete(path)
  return res
}

const LS_LANG = 'sw.lang'
const LS_LIST = 'sw.mylist'
const LS_HIST = 'sw.history'

const loadLS = (k, dflt) => {
  try { return JSON.parse(localStorage.getItem(k)) ?? dflt } catch { return dflt }
}
const saveLS = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)) } catch { /* ignore */ } }

/* -------------------------------------------------------------- components */
function LangPicker({ lang, languages, counts, onChange }) {
  // the count next to each language is the number of *dramas* in the online
  // catalogue for it (English (2737)); it comes from /api/catalogue/meta, so it
  // is missing for a code the sweep never saw -- fall back to the bare label
  const n = (code) => {
    const c = counts?.[code]
    return typeof c === 'number' ? ` (${c})` : ''
  }
  return (
    <select
      className="lang"
      value={lang}
      onChange={(e) => onChange(e.target.value)}
      title="Catalogue language (sent as the 'lang' header, like the app)"
    >
      {languages.map((l) => (
        <option key={l.code} value={l.code}>{l.label}{n(l.code)}</option>
      ))}
    </select>
  )
}

function Header({ user, lang, languages, counts, onLang, onFarm, farming, onHome }) {
  return (
    <header className="hdr">
      {/* the logo doubles as "go home": client-side route to "/" (no reload),
          but a real <a href="/"> so middle-click / open-in-new-tab still work */}
      <a className="brand" href="/" title="ShortsWave — back to the start page"
        onClick={(e) => {
          if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button > 0) return
          e.preventDefault()
          onHome?.()
        }}>Shorts<span>Wave</span></a>
      <div className="bal">
        <span title="bonus — task rewards, cannot buy chapters">🎁 {user?.bonus ?? '—'}</span>
        <span title="coins — bought via Google Play only">🪙 {user?.coin ?? '—'}</span>
        <button className="btn" onClick={onFarm} disabled={farming}>
          {farming ? '…' : 'Claim tasks'}
        </button>
        <LangPicker lang={lang} languages={languages} counts={counts} onChange={onLang} />
      </div>
    </header>
  )
}

function Card({ it, onOpen, rank }) {
  return (
    <button className="card" onClick={() => onOpen(it)}>
      <div className="cov">
        {it.cover ? <img src={it.cover} alt="" loading="lazy" referrerPolicy="no-referrer" /> : <div className="ph" />}
        {rank ? <span className="badge rank">#{rank}</span>
              : <span className="badge">{it.chapters} ep</span>}
      </div>
      <div className="t">{it.title}</div>
      <div className="sub">
        {it.chapters || '?'} ep{it.charge_chapters ? ` · ${it.charge_chapters} locked` : ''}
      </div>
    </button>
  )
}

function Grid({ items, onOpen, empty, numbered }) {
  if (!items?.length) return <p className="empty">{empty || 'Nothing here yet.'}</p>
  return (
    <div className="grid">
      {items.map((it, i) => (
        <Card key={it.drama_id || i} it={it} onOpen={onOpen} rank={numbered ? i + 1 : 0} />
      ))}
    </div>
  )
}

/* --------------------------------------------------------------- Search tab */
function SearchView({ lang, hot, onOpen }) {
  const [q, setQ] = useState('')
  const [items, setItems] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const seq = useRef(0)

  // realtime search, debounced 300 ms; guards against out-of-order responses
  useEffect(() => {
    const word = q.trim()
    if (!word) { setItems(null); setErr(null); return }
    const my = ++seq.current
    const t = setTimeout(async () => {
      setBusy(true)
      const j = await api(`/api/search?q=${encodeURIComponent(word)}&lang=${lang}`)
      if (my !== seq.current) return
      setBusy(false)
      if (j.ok) { setItems(j.items); setErr(null) } else { setErr(j.msg); setItems([]) }
    }, 300)
    return () => clearTimeout(t)
  }, [q, lang])

  return (
    <div className="wrap">
      <div className="searchbox">
        <span>🔍</span>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search dramas…"
          autoComplete="off"
          autoFocus
        />
        {q && <button className="x" onClick={() => setQ('')}>✕</button>}
      </div>

      {err && <p className="err">search error: {err}</p>}
      {busy && !items && <p className="dim">searching…</p>}

      {items === null && (
        <>
          <h3 className="sec">Hot search</h3>
          <div className="chips">
            {(hot || []).map((h) => (
              <button key={h.word} className="chip" onClick={() => setQ(h.word)}>{h.word}</button>
            ))}
          </div>
        </>
      )}

      {items !== null && (
        <>
          <p className="dim small">{items.length} result{items.length === 1 ? '' : 's'} for “{q.trim()}”</p>
          <Grid items={items} onOpen={onOpen} empty="No drama matches that word." />
        </>
      )}
    </div>
  )
}

/* ---------------------------------------------------------- All titles tab */
/* The home page lists the *whole* catalogue (4750 dramas), not just the couple
 * of hundred /api/feed and /api/rank return. The server pages it (60 rows at a
 * time) and this view appends a page whenever the sentinel below the list
 * scrolls into view. Everything is driven by one `next` counter, so the initial
 * load and the lazy loads can never race and double-append. */
const CAT_PAGE = 60

function AllTitles({ lang, onOpen, meta, labels, scope: scopeProp, onScope }) {
  const [rows, setRows] = useState([])
  const [total, setTotal] = useState(null)
  const [hasMore, setHasMore] = useState(true)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  // 'all' (whole catalogue) or a language code. Owned by App, so picking a
  // language in the header picker and picking it here are the same choice.
  const [ownScope, setOwnScope] = useState('all')
  const scope = scopeProp ?? ownScope
  const setScope = onScope ?? setOwnScope
  const next = useRef(1)                          // page the next load asks for
  const busyRef = useRef(false)                   // one fetch at a time
  const sentinel = useRef(null)
  // synchronous mirror of `scope`, so an in-flight page can tell it is stale
  const scopeRef = useRef(scope)

  // a new scope is a brand-new list: throw the old rows away first
  const reset = useCallback(() => {
    next.current = 1
    busyRef.current = false
    setRows([]); setTotal(null); setHasMore(true); setErr(null); setBusy(false)
  }, [])

  const load = useCallback(async () => {
    if (busyRef.current) return
    const page = next.current
    busyRef.current = true
    setBusy(true)
    let j
    try {
      j = await api(`/api/catalogue?page=${page}&page_size=${CAT_PAGE}` +
                    (scope === 'all' ? '&lang=all' : `&lang=${scope}`), { nocache: true })
    } catch (e) {
      j = { ok: false, msg: String(e) }
    }
    busyRef.current = false
    if (scope !== scopeRef.current) return         // scope changed while in flight
    setBusy(false)
    if (!j.ok) { setErr(j.msg || 'catalogue error'); return }
    setErr(null)
    next.current = page + 1
    setTotal(j.total)
    setHasMore(!!j.has_more)
    setRows((prev) => (page === 1 ? j.items : [...prev, ...j.items]))
  }, [scope])

  // on a scope switch: mirror it synchronously, drop the old rows, refetch
  useEffect(() => { scopeRef.current = scope; reset() }, [scope, reset])

  // the very first page (and the first page of every scope switch)
  useEffect(() => { if (!rows.length && hasMore && !err && !busyRef.current) load() },
    [rows.length, hasMore, err, load])

  // lazy load: observe a sentinel at the end of the list. Browsers without
  // IntersectionObserver (older WebViews) keep the explicit button below.
  useEffect(() => {
    const el = sentinel.current
    if (!el || typeof window === 'undefined' || !window.IntersectionObserver) return () => {}
    const io = new window.IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting) && hasMore && !busyRef.current) load()
    }, { rootMargin: '600px 0px' })     // start fetching before the reader gets there
    io.observe(el)
    return () => io.disconnect()
  }, [load, hasMore, rows.length])

  const langs = useMemo(() => {
    const counts = meta?.counts || {}
    return Object.keys(counts).sort((a, b) => counts[b] - counts[a])
  }, [meta])

  // "English" for the picked code, so the header says which slice is on screen
  const scopeLabel = scope === 'all' ? 'All titles'
    : (labels?.[scope] || scope)
  const scopeCount = scope === 'all' ? null : (meta?.counts?.[scope] ?? null)
  // One thin row above the grid: it says which slice is on screen and lets the
  // reader hop between languages without going back to the header picker.
  const chipRow = (
    <div className="chips langchips">
      <span className="dim small langpick">Language</span>
      <button className={'chip' + (scope === 'all' ? ' on' : '')} onClick={() => setScope('all')}>
        All <span className="dim">{meta?.total ?? ''}</span>
      </button>
      {langs.map((l) => (
        <button key={l} data-lang={l} className={'chip' + (scope === l ? ' on' : '')}
          onClick={() => setScope(l)}>
          {labels?.[l] || l} <span className="dim">{meta.counts[l]}</span>
        </button>
      ))}
    </div>
  )

  return (
    <div className="wrap">
      <div className="cattop">
        <h3 className="sec" style={{ margin: 0 }}>
          {scopeLabel} <span className="dim small">({total ?? scopeCount ?? meta?.total ?? '…'})</span>
        </h3>
        <span className="dim small">{rows.length} shown</span>
      </div>
      {/* the same covers as Categories, only the language slice changes */}
      {chipRow}

      {err && <p className="err">catalogue: {err}</p>}
      <Grid items={rows} onOpen={onOpen} empty={busy ? 'loading…' : 'No titles in this language.'} />

      {/* sentinel: scrolling it into view loads the next page */}
      <div ref={sentinel} className="loadmore" aria-hidden={!hasMore}>
        {hasMore ? (
          <>
            <span className="dot" />
            {busy ? 'loading more…' : 'scroll for more'}
            <button className="btn wide" disabled={busy} onClick={() => load()}>
              {busy ? 'loading…' : `Load ${CAT_PAGE} more`}
            </button>
          </>
        ) : rows.length ? (
          `— end of list · ${rows.length} titles —`
        ) : null}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------- Category tab */
function CategoryView({ config, lang, onOpen }) {
  const [place, setPlace] = useState(0)
  const [tag, setTag] = useState(null)
  const [items, setItems] = useState([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const places = config?.categories || []

  useEffect(() => { if (places.length && tag === null) setTag(0) }, [places, tag])

  useEffect(() => {
    if (tag === null || !places.length) return
    let dead = false
    setBusy(true)
    api(`/api/category?channel_id=0&tag_id=${tag}&page=1&page_size=30&lang=${lang}`).then((j) => {
      if (dead) return
      setBusy(false)
      if (j.ok) { setItems(j.items); setErr(null) } else { setErr(j.msg); setItems([]) }
    })
    return () => { dead = true }
  }, [tag, lang, places.length])

  if (!places.length) return <div className="wrap"><p className="empty">No categories for this language.</p></div>

  return (
    <div className="wrap">
      <div className="tabs">
        {places.map((p, i) => (
          <button key={p.key || i} className={'tab' + (i === place ? ' on' : '')}
            onClick={() => { setPlace(i); setTag(p.list?.[0]?.id ?? 0) }}>
            {p.name}
          </button>
        ))}
      </div>
      <div className="chips">
        {(places[place]?.list || []).map((t) => (
          <button key={t.id} className={'chip' + (t.id === tag ? ' on' : '')}
            onClick={() => setTag(t.id)}>{t.name}</button>
        ))}
      </div>
      {err && <p className="err">{err}</p>}
      {busy && <p className="dim small">loading…</p>}
      <Grid items={items} onOpen={onOpen} empty="No drama in this category." />
    </div>
  )
}

/* ----------------------------------------------------------------- Rank tab */
function RankView({ lang, onOpen }) {
  const [ranks, setRanks] = useState([])
  const [i, setI] = useState(0)
  const [err, setErr] = useState(null)

  useEffect(() => {
    api(`/api/rank?lang=${lang}`).then((j) => {
      if (j.ok) { setRanks(j.ranks); setI(0); setErr(null) } else setErr(j.msg)
    })
  }, [lang])

  if (err) return <div className="wrap"><p className="err">{err}</p></div>
  if (!ranks.length) return <div className="wrap"><p className="dim small">loading…</p></div>

  return (
    <div className="wrap">
      <div className="tabs">
        {ranks.map((r, k) => (
          <button key={r.rank_type || k} className={'tab' + (k === i ? ' on' : '')}
            onClick={() => setI(k)}>{r.title || r.rank_type}</button>
        ))}
      </div>
      <Grid items={ranks[i]?.items} onOpen={onOpen} numbered />
    </div>
  )
}

/* ----------------------------------------------------------------- Feed tab */
function Feed({ lang, kind, onOpen }) {
  const [items, setItems] = useState([])
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState(null)

  const load = useCallback(async (p, append, k) => {
    setLoading(true)
    const j = await api(`/api/feed?page=${p}&page_size=24&kind=${k}&lang=${lang}`)
    if (j.ok) {
      setItems((prev) => (append ? [...prev, ...j.items] : j.items))
      setErr(null)
    } else setErr(j.msg)
    setLoading(false)
  }, [lang])

  useEffect(() => { setPage(1); load(1, false, kind) }, [load, kind])

  return (
    <div className="wrap">
      {err && <p className="err">feed error: {err}</p>}
      <Grid items={items} onOpen={onOpen} />
      <div className="more">
        <button className="btn wide" disabled={loading}
          onClick={() => { const n = page + 1; setPage(n); load(n, true, kind) }}>
          {loading ? 'loading…' : 'Load more'}
        </button>
      </div>
    </div>
  )
}

/* ----------------------------------------------------------------- Player */
function Player({ drama, chapter, onBack, onNext, hasNext }) {
  const [sub, setSub] = useState(null)
  const [subErr, setSubErr] = useState(null)
  const [vidErr, setVidErr] = useState(null)
  const vidRef = useRef(null)
  const subs = chapter.subtitles || []
  // "auto" keeps the upstream stream when the browser can decode HEVC, otherwise
  // (and always after a black-frame probe) the server-side H.264 rendition.
  const [mode, setMode] = useState(() => (hevcSupported() ? 'auto' : 'h264'))

  useEffect(() => { setSub(subs.length ? subs[0].language : null) }, [chapter.chapter_id, subs.length])
  // next/prev episode and the h264 switch must not inherit the old error text;
  // the reset is keyed on the chapter itself, plus the retry counter below
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    setVidErr(null)
    setSubErr(null)
    setMode(hevcSupported() ? 'auto' : 'h264')
    setAttempt(0)
  }, [chapter.chapter_id, chapter.play])

  const src = mode === 'h264' ? h264Url(chapter.play) : chapter.play

  // Audio playing over a blank frame means the browser decoded the AAC track but
  // cannot show the HEVC one: fall back to the transcoded H.264 rendition once,
  // and only show a message if even that yields no picture.
  const probeVideo = () => {
    const v = vidRef.current
    if (!v) return
    if (v.videoWidth > 0) { setVidErr(null); return }
    if (mode !== 'h264') { setMode('h264'); return }
    setVidErr('Audio plays but the picture stays black even on the H.264 rendition — '
      + 'the server may still be transcoding this episode (first play takes a few '
      + 'seconds), or the segment failed to load. Seek/play again in a moment.')
  }

  if (!chapter.playable) {
    return (
      <div className="player">
        <div className="ptop">
          <button className="btn ghost" onClick={onBack}>‹ Back</button>
          <span>Ep {chapter.index}</span>
        </div>
        <div className="locked">
          <div className="lock">🔒</div>
          <h3>Episode {chapter.index} is locked</h3>
          <p>Price <b>{chapter.price}</b> coins · unlock_type <code>{chapter.unlock_type}</code></p>
          <p className="note">
            You have <b>{chapter.coin}</b> coins and <b>{chapter.bonus}</b> bonus.
            Coins can only be bought through Google Play (server-verified receipt);
            bonus is never accepted for chapters.
          </p>
          {chapter.is_ad_unlock_user === false && (
            <p className="note dim">
              Ad-unlock disabled for this account (<code>is_ad_unlock_user=false</code>) — a
              server-side flag.
            </p>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="player">
      <div className="ptop">
        <button className="btn ghost" onClick={onBack}>‹ Back</button>
        <span className="grow">{chapter.name || `Episode ${chapter.index}`} · Ep {chapter.index}</span>
        {hasNext && <button className="btn" onClick={onNext}>Next ›</button>}
      </div>

      <video
        key={src + '#' + attempt}
        ref={vidRef}
        src={src}
        className="vid"
        controls
        autoPlay
        playsInline
        crossOrigin="anonymous"
        onError={(e) => {
          const code = e?.target?.error?.code
          // MediaError 4 (SRC_NOT_SUPPORTED) right after "next" is usually the
          // segment/playlist of the *previous* episode still in flight or a
          // manifest fetched milliseconds after the first segment appeared:
          // retry twice (fresh src via key, short backoff) before reporting.
          if (attempt < 2) {
            const wait = attempt === 0 ? 700 : 2500
            setTimeout(() => setAttempt((a) => a + 1), wait)
            setVidErr(`Episode ${chapter.index} did not start yet — retrying…`)
            return
          }
          setSubErr('video failed to load' + (code ? ` (MediaError ${code})` : '')
            + ' — reopen the episode to retry')
        }}
        onLoadedMetadata={probeVideo}
        onPlaying={probeVideo}
        onTimeUpdate={(e) => { if (e.target.currentTime > 1.5) probeVideo() }}
      >
        {subs.map((s) => (
          <track
            key={s.language + s.url}
            kind="subtitles"
            src={s.url}
            srcLang={(s.language || 'en').slice(0, 2)}
            label={s.label}
            default={s.language === sub}
          />
        ))}
      </video>

      {vidErr && <p className="err" style={{ padding: '0 16px' }}>{vidErr}</p>}
      {chapter.playable && (
        <p className="note dim" style={{ padding: '0 16px' }}>
          {mode === 'h264'
            ? 'Playing the server-transcoded H.264 rendition (first play of an episode takes a few seconds to encode).'
            : 'Playing the original stream (HEVC).'}{' '}
          <a href="#" onClick={(e) => { e.preventDefault(); setVidErr(null); setMode(mode === 'h264' ? 'auto' : 'h264') }}>
            switch
          </a>
        </p>
      )}
      {subErr && <p className="err" style={{ padding: '0 16px' }}>{subErr}</p>}

      <div className="subbar">
        <span className="dim small">Subtitles:</span>
        {subs.length === 0 && <span className="dim small">none for this episode</span>}
        {subs.length > 0 && (
          <>
            <button className={'chip' + (sub === null ? ' on' : '')} onClick={() => setSub(null)}>Off</button>
            {subs.map((s) => (
              <button key={s.language}
                className={'chip' + (s.language === sub ? ' on' : '')}
                onClick={() => setSub(s.language)}>{s.label}</button>
            ))}
          </>
        )}
      </div>
      {subErr && <p className="err">{subErr}</p>}
    </div>
  )
}

/* ------------------------------------------------------------------ Drama */
// One short line about the unlock sweep. Quiet by design: the grid itself shows
// which episodes are open, so no per-episode "unlocked" tags and no recap while
// everything is going well. Never says "unlocked all" unless it really is.
function unlockNote(j) {
  const open = (j.open || []).length
  const total = j.total || 0
  const left = (j.failed || []).length
  if (j.running) return null
  if (!total) return null
  if (!left) return null
  const parts = [`${left} of ${total} episodes could not be unlocked yet.`]
  if (j.accounts) {
    parts.push(`Extra anonymous accounts were farmed automatically (${j.accounts})`
      + ' to pay for more episodes.')
  }
  if (j.bonus_short) {
    parts.push(`Bonus is ${j.bonus ?? 0} but a paid episode costs ${j.price ?? 50},`
      + ' and the reward pool of this account is drained.')
  }
  if (j.quota_hit) {
    parts.push(`The free ad-unlock quota for this device is used up`
      + ` (${j.current_ad_unlock_count ?? 0}/${j.max_ad_unlock_count ?? 0}).`)
  }
  parts.push('Press Retry to continue.')
  return parts.join(' ')
}
function Drama({ item, lang, onBack, onOpenEpisode, autoplay }) {
  const [chs, setChs] = useState([])
  const [sel, setSel] = useState(null)
  const [err, setErr] = useState(null)
  // chapters the server has already unlocked for this account (bulk unlock on
  // page open). A locked entry in `chs` is filtered through this set, so an
  // episode that got unlocked can be opened without refetching the list.
  const [unlocked, setUnlocked] = useState({})
  const [note, setNote] = useState(null)
  const autoRef = useRef(autoplay)

  useEffect(() => {
    let alive = true
    setChs([]); setSel(null); setErr(null)
    api(`/api/drama/${item.drama_id}?lang=${lang}`).then((j) => {
      if (!alive) return
      if (j.ok) setChs(j.chapters)
      else setErr(j.msg)
    })
    return () => { alive = false }
  }, [item.drama_id, lang])

  // Unlock every chapter as soon as the detail page is open. The server sweeps
  // in a background job (a 30-50 episode drama is minutes of upstream calls), so
  // we start it once and then poll /api/unlock_status: the grid fills in as
  // chapters open, and the note tells the truth about whatever is left.
  const [sweep, setSweep] = useState(null)     // {running,total,open,failed,...}
  const [retry, setRetry] = useState(0)
  const [beat, setBeat] = useState(0)          // watch: bump to refresh the clock
  useEffect(() => {
    let alive = true
    let timer = null
    let miss = 0                 // consecutive failed polls (drop the sticky cache)
    setUnlocked({}); setSweep(null); setNote(null)
    const start = retry > 0 || miss > 0 ? '?refresh=1' : ''
    const poll = async (path) => {
      // `nocache` bypasses the in-flight memo: a status poll must never be served
      // by the answer of the previous one, and a dropped request must not end
      // the loop (the sweep keeps running on the server either way)
      const j = await api(`${path}${path.includes('?') ? '&' : '?'}lang=${lang}`, { nocache: true })
      if (!alive) return
      if (!j || !j.ok) {
        miss += 1
        timer = setTimeout(() => poll(`/api/unlock_status/${item.drama_id}`), Math.min(1000 * 2 ** miss, 15000))
        return
      }
      miss = 0
      const map = {}
      for (const cid of j.open || []) map[cid] = true
      setUnlocked(map)
      setSweep(j)
      setNote(unlockNote(j))
      if (j.running) timer = setTimeout(() => poll(`/api/unlock_status/${item.drama_id}`), 1500)
    }
    poll(`/api/unlock_all/${item.drama_id}${start}`)
    return () => { alive = false; if (timer) clearTimeout(timer) }
  }, [item.drama_id, lang, retry])

  // a sweep started earlier (other tab, or the job outlived the page) is picked
  // up again on return: the server keeps the job, we only need to poll it
  useEffect(() => {
    if (!sweep || sweep.running) return undefined
    const t = setTimeout(async () => {
      const j = await api(`/api/unlock_status/${item.drama_id}?lang=${lang}`, { nocache: true })
      if (!j.ok || !j.running) return
      setSweep(j)
      setNote(unlockNote(j))
      setBeat((b) => b + 1)
    }, 4000)
    return () => clearTimeout(t)
  }, [sweep, beat, item.drama_id, lang])

  // live clock for the progress line ('unlocking 12/45 · 34s')
  useEffect(() => {
    if (!sweep?.running) return undefined
    const t = setInterval(() => setBeat((b) => b + 1), 1000)
    return () => clearInterval(t)
  }, [sweep?.running])

  const open = async (ch) => {
    const j = await api(`/api/chapter/${item.drama_id}/${ch.chapter_id}?lang=${lang}`)
    if (j.ok) { setSel(j.chapter); onOpenEpisode?.(item, ch) } else setErr(j.msg)
  }

  // deep link (/d/title-id) jumps into the first free episode once the list is in
  useEffect(() => {
    if (!autoRef.current || sel || !chs.length) return
    autoRef.current = false
    open(chs.find((c) => c.is_free) || chs[0])
  }, [chs, sel])

  const next = () => {
    if (!sel) return
    const i = chs.findIndex((c) => c.chapter_id === sel.chapter_id)
    if (i >= 0 && chs[i + 1]) open(chs[i + 1])
  }

  if (sel) {
    const i = chs.findIndex((c) => c.chapter_id === sel.chapter_id)
    return (
      <Player
        key={sel.chapter_id}
        drama={item}
        chapter={sel}
        onBack={() => setSel(null)}
        onNext={next}
        hasNext={i >= 0 && i + 1 < chs.length}
      />
    )
  }

  const free = chs.filter((c) => c.is_free).length
  const isOpen = (c) => c.is_free || !!unlocked[c.chapter_id]
  const openedCount = chs.filter(isOpen).length
  // live line under the title: the sweep is a background job, so without it the
  // page looks frozen until a manual refresh (that was the reported bug)
  const sweepLine = (() => {
    if (!sweep?.running) return null
    const total = sweep.total || chs.length
    const base = sweep.started
    const secs = base ? Math.max(0, Math.round(Date.now() / 1000 - base)) : 0
    return `unlocking ${openedCount}/${total || '…'} · ${secs}s`
  })()

  return (
    <div className="wrap">
      <button className="btn ghost" onClick={onBack}>‹ Back</button>
      <div className="dhead">
        {item.cover && <img src={item.cover} alt="" referrerPolicy="no-referrer" />}
        <div>
          <h2>{item.title}</h2>
          <p className="sub">
            {item.chapters} episodes · {free} free
            {sweepLine && <span className="dim"> · {sweepLine}</span>}
          </p>
          <p className="desc">{item.description}</p>
        </div>
      </div>
      {err && <p className="err">{err}</p>}
      {sweep?.running && (
        <div className="prog" role="progressbar" aria-valuemin={0}
          aria-valuemax={sweep.total || 0} aria-valuenow={openedCount}>
          <div className="progbar" style={{ width: `${(sweep.total ? (openedCount / sweep.total) * 100 : 5).toFixed(1)}%` }} />
        </div>
      )}
      {note && <p className="dim small">{note}</p>}
      {sweep && !sweep.running && (sweep.failed || []).length > 0 && (
        <button className="btn ghost" onClick={() => setRetry((n) => n + 1)}>
          Retry unlocking the remaining {(sweep.failed || []).length}
        </button>
      )}
      <div className="eps">
        {chs.map((c) => (
          <button
            key={c.chapter_id}
            className={'ep' + (isOpen(c) ? ' free' : '')}
            onClick={() => open(c)}
            title={isOpen(c) ? undefined : 'locked'}
          >
            <span>{c.index}</span>
            {c.is_free ? <em>free</em>
              : isOpen(c) ? null
              : <em className="paid">🪙 {c.price}</em>}
          </button>
        ))}
      </div>
    </div>
  )
}

/* --------------------------------------------------------------- Rewards */
function Rewards({ lang, user, onClaim, farming, note }) {
  const [tasks, setTasks] = useState([])
  const [err, setErr] = useState(null)

  useEffect(() => {
    api(`/api/tasks?lang=${lang}`).then((j) => {
      if (j.ok) {
        const raw = j.tasks
        setTasks(Array.isArray(raw) ? raw : (raw?.list || raw?.task_list || []))
      } else setErr(j.msg)
    })
  }, [lang, note])

  const total = tasks.reduce((a, t) => a + (Number(t.bonus || t.pay || 0) * Number(t.limit || 1)), 0)

  return (
    <div className="wrap">
      <div className="wallet">
        <div><b>🎁 {user?.bonus ?? '—'}</b><span className="dim small">bonus</span></div>
        <div><b>🪙 {user?.coin ?? '—'}</b><span className="dim small">coins</span></div>
      </div>
      <p className="dim small">
        Bonus is paid by tasks and <b>cannot</b> unlock chapters — chapters bill <b>coins</b>,
        which only come from server-verified Google Play purchases.
      </p>
      <button className="btn wide" onClick={onClaim} disabled={farming}>
        {farming ? 'claiming…' : `Claim all available tasks${total ? ` (~${total} bonus)` : ''}`}
      </button>
      {err && <p className="err">{err}</p>}
      <h3 className="sec">Tasks</h3>
      <div className="tasklist">
        {tasks.map((t, i) => (
          <div className="task" key={t.task_id || t.id || i}>
            <div>
              <div className="tname">{t.task_name || t.name || t.title || t.task_id}</div>
              <div className="dim small">{t.task_desc || t.desc || t.task_type || ''}</div>
            </div>
            <div className="tpay">+{t.bonus ?? t.pay ?? 0}<span className="dim small"> {t.limit ? `·${t.limit}×` : ''}</span></div>
          </div>
        ))}
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- My List */
function MyList({ lang, myList, history, onOpen, onRemove }) {
  return (
    <div className="wrap">
      <h3 className="sec">My List ({myList.length})</h3>
      {myList.length === 0 && <p className="empty">Nothing saved yet — tap ☆ on a drama page.</p>}
      <Grid items={myList} onOpen={(it) => onOpen(it)} />
      {myList.length > 0 && (
        <div className="chips" style={{ marginTop: 10 }}>
          {myList.map((it) => (
            <button key={it.drama_id} className="chip" onClick={() => onRemove(it.drama_id)}>
              Remove “{it.title}” ✕
            </button>
          ))}
        </div>
      )}
      <h3 className="sec">Recently watched</h3>
      {history.length === 0 ? <p className="empty">No history yet.</p> : (
        <div className="chips">
          {history.map((h) => (
            <button key={h.drama_id} className="chip" onClick={() => onOpen(h)}>
              {h.title} · Ep {h.episode}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/* -------------------------------------------------------------------- Me */
function Me({ user, lang, languages, counts, onLang, onFarm, farming }) {
  return (
    <div className="wrap">
      <div className="profile">
        <div className="avatar">{(user?.user_id || '?').slice(0, 2).toUpperCase()}</div>
        <div>
          <div className="pid">{user?.user_id || 'not signed in'}</div>
          <div className="dim small">{user?.is_vip ? 'VIP' : 'anonymous session'} · lang {lang}</div>
        </div>
      </div>
      <div className="wallet">
        <div><b>🎁 {user?.bonus ?? '—'}</b><span className="dim small">bonus</span></div>
        <div><b>🪙 {user?.coin ?? '—'}</b><span className="dim small">coins</span></div>
      </div>
      <h3 className="sec">Language</h3>
      <p className="dim small">Changing the language switches the whole catalogue (as in the app).</p>
      <LangPicker lang={lang} languages={languages} counts={counts} onChange={onLang} />
      <h3 className="sec">Session</h3>
      <button className="btn wide" onClick={onFarm} disabled={farming}>
        {farming ? 'claiming…' : 'Claim daily tasks'}
      </button>
      <p className="dim small" style={{ marginTop: 14 }}>
        Read-only mirror of the ShortsWave API. Episode unlocking is enforced server-side:
        coins need a Google Play receipt, ad-unlock needs the server flag
        <code> is_ad_unlock_user</code>.
      </p>
    </div>
  )
}

/* ------------------------------------------------------------------- App */
const TABS = [
  { key: 'discover', label: 'Discover', icon: '🏠' },
  { key: 'foryou', label: 'For You', icon: '▶' },
  { key: 'rewards', label: 'Rewards', icon: '🎁' },
  { key: 'mylist', label: 'My List', icon: '📑' },
  { key: 'me', label: 'Me', icon: '👤' },
]

export default function App() {
  const [user, setUser] = useState(null)
  const [lang, setLang] = useState(() => loadLS(LS_LANG, 'en') || 'en')
  const [config, setConfig] = useState(null)
  const [tab, setTab] = useState('discover')
  const [item, setItem] = useState(null)
  const [farming, setFarming] = useState(false)
  const [note, setNote] = useState(null)
  const [myList, setMyList] = useState(() => loadLS(LS_LIST, []) || [])
  const [history, setHistory] = useState(() => loadLS(LS_HIST, []) || [])
  const [boot, setBoot] = useState(null)
  const [routeErr, setRouteErr] = useState(null)
  const [autoplay, setAutoplay] = useState(false)
  const [catMeta, setCatMeta] = useState(null)
  // Which slice of the catalogue the home list shows. Follows the header picker:
  // choosing Indonesian there lists every Indonesian title. 'all' is only set by
  // the "All" chip in the list itself.
  const [catScope, setCatScope] = useState('all')  // 'all' | language code

  const languages = config?.languages || [{ code: 'en', region: 'US', label: 'English' }]
  const hot = config?.hot_search || []
  // code -> display name ("id" -> "Bahasa Indonesia") for the list headers
  const langLabels = useMemo(() => {
    const m = {}
    for (const l of languages) if (l?.code) m[l.code] = l.label || l.code
    return m
  }, [languages])
  const langLabel = langLabels[lang] || lang

  const refreshUser = useCallback(async (l) => {
    const j = await api(`/api/health?lang=${l || lang}`)
    if (j.ok) setUser(j)
  }, [lang])

  const bootConfig = useCallback(async (l) => {
    const j = await api(`/api/config?lang=${l}`)
    setConfig(j.ok ? j : { languages: [], categories: [], hot_search: [] })
    setBoot(!j.ok ? j.msg : null)
  }, [])

  useEffect(() => { bootConfig(lang); refreshUser(lang) }, [lang, bootConfig, refreshUser])
  // picking a language in the header also narrows the catalogue grid to it —
  // the row above the grid still offers "All languages" to get back
  useEffect(() => { setCatScope(lang) }, [lang])
  // catalogue totals for the "All titles" chip row (4750 dramas, 13 languages)
  useEffect(() => {
    let live = true
    api('/api/catalogue/meta').then((j) => { if (live && j.ok) setCatMeta(j) })
    return () => { live = false }
  }, [])
  useEffect(() => { saveLS(LS_LANG, lang) }, [lang])
  useEffect(() => { saveLS(LS_LIST, myList) }, [myList])
  useEffect(() => { saveLS(LS_HIST, history) }, [history])

  const farm = async () => {
    setFarming(true)
    const j = await api('/api/tasks/farm', { method: 'POST' })
    setNote(j.ok ? `Claimed ${j.bonus} bonus from ${j.tasks?.length ?? 0} tasks` : `failed: ${j.msg}`)
    await refreshUser()
    setFarming(false)
  }

  const openDrama = (it) => {
    setItem(it); setRouteErr(null); setAutoplay(false)
    window.scrollTo(0, 0)
    const p = dramaPath(it)
    if (window.location.pathname !== p) window.history.pushState({ p }, '', p)
  }
  const closeDrama = useCallback(() => {
    setItem(null); setRouteErr(null); setAutoplay(false)
    if (window.location.pathname.startsWith('/d/')) window.history.pushState({ p: '/' }, '', '/')
    window.scrollTo(0, 0)
  }, [])

  /* ------------------------------------------------- router: /d/<slug>-<id> */
  // Resolve a drama URL to an item. The cover/title in the URL slug let us fill
  // the header instantly; /api/drama gives the real title for the canonical URL.
  const applyRoute = useCallback(async (pathname) => {
    const parsed = parseDramaPath(pathname)
    if (!parsed) { setItem(null); setRouteErr(null); setAutoplay(false); return }
    setRouteErr(null)
    setAutoplay(true)
    const title = parsed.slug.replace(/-/g, ' ')
    setItem((prev) => (prev && prev.drama_id === parsed.id ? prev : { drama_id: parsed.id, title }))
    const j = await api(`/api/drama/${parsed.id}?lang=${lang}`)
    // Upstream answers 200 + an empty chapter list for unknown ids, so `ok`
    // alone is not enough to know the drama exists.
    if (!j.ok || !(j.chapters || []).length) {
      setRouteErr(j.ok ? `drama ${parsed.id} not found (no episodes)` : j.msg || `drama ${parsed.id} not found`)
      setItem(null); setAutoplay(false)
      return
    }
    const it = {
      drama_id: parsed.id,
      title: j.title || title || parsed.id,
      cover: j.cover || null,
      chapters: j.chapters?.length,
      first_chapter_id: j.first_chapter_id,
    }
    setItem(it)
    // Canonicalise even when only the slug is flaky (stale/legacy link): compare
    // against the URL we were asked for, not against the current location, which
    // may have been replaced already by a previous route.
    const want = dramaPath(it)
    // The location may already have moved on (a click or a second route change
    // while /api/drama was in flight): only rewrite when we are still on the
    // drama URL we were asked for.
    if (decodeURI(window.location.pathname) === decodeURI(pathname)
        && decodeURI(pathname) !== decodeURI(want)) {
      window.history.replaceState({ p: want }, '', want)
    }
  }, [lang])

  // initial deep link (also covers a refresh on /d/...)
  useEffect(() => { applyRoute(window.location.pathname) }, [])
  // browser back/forward
  useEffect(() => {
    const onPop = () => applyRoute(window.location.pathname)
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [applyRoute])

  const onOpenEpisode = (drama, ch) => {
    const next = [{ drama_id: drama.drama_id, title: drama.title, cover: drama.cover,
                    first_chapter_id: drama.first_chapter_id, chapters: drama.chapters,
                    episode: ch.index, description: drama.description, unlock_type: drama.unlock_type },
                  ...history.filter((h) => h.drama_id !== drama.drama_id)].slice(0, 12)
    setHistory(next)
  }
  const inList = item && myList.some((m) => m.drama_id === item.drama_id)
  const toggleList = () => {
    if (!item) return
    setMyList((prev) => inList
      ? prev.filter((m) => m.drama_id !== item.drama_id)
      : [item, ...prev].slice(0, 60))
  }

  const body = useMemo(() => {
    if (item) {
      return (
        <>
          {routeErr && <div className="wrap"><p className="err">drama: {routeErr}</p></div>}
          <div className="wrap" style={{ paddingBottom: 0 }}>
            <button className={'btn ghost' + (inList ? ' on' : '')} onClick={toggleList}>
              {inList ? '★ In My List' : '☆ Add to My List'}
            </button>
          </div>
          <Drama item={item} lang={lang} onBack={closeDrama} onOpenEpisode={onOpenEpisode}
            autoplay={autoplay} />
        </>
      )
    }
    if (routeErr) {
      return (
        <div className="wrap">
          <h3 className="sec" style={{ marginTop: 0 }}>Drama not found</h3>
          <p className="err">{routeErr}</p>
          <button className="btn ghost" onClick={closeDrama}>‹ Back to Discover</button>
        </div>
      )
    }
    switch (tab) {
      case 'discover':
        return (
          <>
            <SearchView lang={lang} hot={hot} onOpen={openDrama} />
            <div className="wrap">
              <h3 className="sec">Categories
                <span className="dim small"> · {langLabel}</span></h3>
            </div>
            <CategoryView config={config} lang={lang} onOpen={openDrama} />
            <div className="wrap"><h3 className="sec">Ranking</h3></div>
            <RankView lang={lang} onOpen={openDrama} />
            <AllTitles lang={lang} onOpen={openDrama} meta={catMeta} labels={langLabels}
              scope={catScope} onScope={setCatScope} />
          </>
        )
      case 'foryou':
        return (
          <>
            <div className="wrap"><h3 className="sec" style={{ marginTop: 0 }}>For You</h3></div>
            <Feed lang={lang} kind="all" onOpen={openDrama} />
            <div className="wrap"><h3 className="sec">More like this</h3></div>
            <Feed lang={lang} kind="more" onOpen={openDrama} />
          </>
        )
      case 'rewards':
        return <Rewards lang={lang} user={user} onClaim={farm} farming={farming} note={note} />
      case 'mylist':
        return <MyList lang={lang} myList={myList} history={history} onOpen={openDrama}
          onRemove={(id) => setMyList((p) => p.filter((m) => m.drama_id !== id))} />
      case 'me':
        return <Me user={user} lang={lang} languages={languages} counts={catMeta?.counts}
          onLang={setLang} onFarm={farm} farming={farming} />
      default:
        return null
    }
  }, [item, tab, lang, hot, config, myList, history, user, farming, note, inList, languages,
      routeErr, autoplay, closeDrama, catMeta, catScope, langLabels, langLabel])

  return (
    <div className="app">
      <Header user={user} lang={lang} languages={languages} counts={catMeta?.counts}
        onLang={setLang} onHome={closeDrama} onFarm={farm} farming={farming} />
      {note && <div className="note-bar" onClick={() => setNote(null)}>{note} ✕</div>}
      {boot && <div className="note-bar err-bar">config: {boot}</div>}
      <main>{body}</main>

      <nav className="bottom">
        {TABS.map((t) => (
          <button key={t.key} className={'nav' + (tab === t.key && !item ? ' on' : '')}
            onClick={() => {
              closeDrama()
              setTab(t.key)
            }}>
            <span className="ico">{t.icon}</span>
            <span className="lbl">{t.label}</span>
          </button>
        ))}
      </nav>
    </div>
  )
}
