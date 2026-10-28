/* Pretty drama URLs: /d/<title-slug>-<drama_id>
 *
 * The id is always the last dash-separated token, so the slug in front is pure
 * decoration (SEO / readability): an old or hand-edited slug still resolves, and
 * the client rewrites it to the canonical one.
 */

/** Lowercase, dash-separated, ASCII where possible. Non-latin titles (th, ar,
 *  ja, ko, ru) keep their own characters — stripping them would leave an empty
 *  slug, and browsers percent-encode them fine. */
export function slugify(title) {
  const s = (title || '')
    .toString()
    // NFC (not NFKD): Hangul syllables stay whole instead of exploding into
    // conjoining jamo, which look identical but break copy/paste and length.
    .normalize('NFC')
    .replace(/[\u0300-\u036f]/g, '')       // strip latin accents: é -> e
    .toLowerCase()
    .replace(/['’`"“”]/g, '')              // apostrophes vanish (don't -> dont)
    .replace(/&/g, ' and ')
    .replace(/[^\p{L}\p{N}]+/gu, '-')      // everything else -> dash
    .replace(/^-+|-+$/g, '')
    .replace(/-{2,}/g, '-')
  return s.slice(0, 80).replace(/-+$/, '')
}

/** "/d/my-drama-title-6adb02c2..." — id appended verbatim. */
export function dramaPath(drama) {
  const id = (drama && (drama.drama_id || drama.dramaId)) || ''
  if (!id) return '/'
  const slug = slugify(drama.title || drama.drama_title)
  return '/d/' + (slug ? slug + '-' : '') + encodeURIComponent(id)
}

const ID_RE = /([0-9a-zA-Z]{8,})$/
// An id on its own (/d/<id>, no slug in front) is also a valid drama URL.
const ID_ONLY_RE = /^[0-9a-zA-Z]{8,}$/

/** Parse a /d/... pathname into {id, slug}. Returns null when not a drama URL. */
export function parseDramaPath(pathname) {
  const m = /^\/d\/(.+?)\/?$/.exec(pathname || '')
  if (!m) return null
  const rest = decodeURIComponent(m[1])
  const idm = ID_RE.exec(rest)
  if (!idm) return null
  const id = idm[1]
  let slug = rest.slice(0, rest.length - id.length)
  slug = slug.replace(/-+$/, '')
  if (ID_ONLY_RE.test(rest)) slug = ''
  return { id, slug }
}

/** True when the slug in the URL is not the canonical one for this title. */
export function needsCanonical(pathname, drama) {
  const parsed = parseDramaPath(pathname)
  if (!parsed || !drama) return false
  const want = dramaPath(drama)
  return want !== pathname && decodeURI(pathname) !== decodeURI(want)
}

/**
 * Does this browser have a picture decoder for the upstream HEVC/H.265 track
 * (720x1280 Main, hvc1)? MediaSource.isTypeSupported is the only signal a plain
 * <video> gives us before playback; Chrome/Firefox desktop answer false,
 * Safari/macOS and Android with HW HEVC answer true.
 */
export function hevcSupported() {
  if (typeof MediaSource === 'undefined' || typeof MediaSource.isTypeSupported !== 'function') {
    return false
  }
  const codecs = [
    'video/mp4; codecs="hvc1.1.6.L93.B0"',
    'video/mp4; codecs="hev1.1.6.L93.B0"',
  ]
  try {
    return codecs.some((c) => MediaSource.isTypeSupported(c))
  } catch {
    return false
  }
}

/**
 * Point a proxied playlist at the server-side H.264 rendition:
 * /proxy/hls?u=…&h264=1 (server.py transcodes + caches it per chapter).
 * Anything that is not our own /proxy/hls URL is returned untouched.
 */
export function h264Url(play) {
  if (!play || play.indexOf('/proxy/hls?') !== 0) return play
  return play.indexOf('h264=1') >= 0 ? play : play + '&h264=1'
}
