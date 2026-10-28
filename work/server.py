"""Flask API server + HLS proxy for ShortsWave (com.shorts.wave.drama).

Run:  python3 server.py        (listens on 0.0.0.0:8000)
"""
import csv
import json
import os
import re
import sqlite3
import threading
import time
import concurrent.futures as _cf
from urllib.parse import urljoin, urlparse

import requests
from flask import Flask, Response, jsonify, request, send_from_directory

import transcode
from sw_client import API as API_HOST, Client, ShortsWaveError

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("SW_DB", os.path.join(BASE_DIR, "sw.db"))
app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "ui", "dist"), static_url_path="")

_clients = {}

# The `lang` / `region` headers drive the whole catalogue: /v3/all returns a
# completely different set of dramas per language, and channel names come back
# translated too. So one Client (and session) per language is kept cached.
DEFAULT_LANG, DEFAULT_REGION = "en", "US"

# ---- H.264 fallback ------------------------------------------------------
# Upstream only ships HEVC/H.265, which Chrome/Firefox/WebView cannot decode.
# /proxy/h264 renders an episode to H.264 fMP4 HLS in work/.cache/h264/<chapter>
# on first request (one ffmpeg run per episode, ~4x realtime on this phone) and
# then serves it from disk.
H264_ENABLED = os.environ.get("SW_H264", "1") not in ("0", "", "false")
H264_PRESET = os.environ.get("SW_H264_PRESET", "ultrafast")
H264_CRF = int(os.environ.get("SW_H264_CRF", "26"))
H264_HEIGHT = int(os.environ.get("SW_H264_HEIGHT", "480"))
H264_CACHE_DAYS = float(os.environ.get("SW_H264_CACHE_DAYS", "7"))

# languageId values are taken verbatim from bm.e.f6566a in the APK.
LANGUAGES = [
    {"code": "en", "region": "US", "languageId": 2, "label": "English"},
    {"code": "es", "region": "ES", "languageId": 9, "label": "Español"},
    {"code": "pt", "region": "BR", "languageId": 8, "label": "Português"},
    {"code": "id", "region": "ID", "languageId": 23, "label": "Bahasa Indonesia"},
    {"code": "th", "region": "TH", "languageId": 30, "label": "ไทย"},
    {"code": "ja", "region": "JP", "languageId": 3, "label": "日本語"},
    {"code": "ko", "region": "KR", "languageId": 4, "label": "한국어"},
    {"code": "ar", "region": "SA", "languageId": 34, "label": "العربية"},
    {"code": "fr", "region": "FR", "languageId": 7, "label": "Français"},
    {"code": "de", "region": "DE", "languageId": 20, "label": "Deutsch"},
    {"code": "ru", "region": "RU", "languageId": 6, "label": "Русский"},
    {"code": "tr", "region": "TR", "languageId": 31, "label": "Türkçe"},
    {"code": "ms", "region": "MY", "languageId": 37, "label": "Bahasa Melayu"},
]


def req_lang():
    """Language/region for this request (query param wins over header/cookie)."""
    lang = (request.args.get("lang") or request.headers.get("X-Lang") or DEFAULT_LANG).lower()
    known = {l["code"]: l for l in LANGUAGES}
    if lang not in known:
        lang = DEFAULT_LANG
    region = (request.args.get("region") or request.headers.get("X-Region")
              or known[lang]["region"])
    return lang, region.upper()


def client_for(lang=DEFAULT_LANG, region=None):
    """Cached client for an explicit (lang, region) — safe off the request thread
    (background jobs must not touch Flask's `request`)."""
    known = {l["code"]: l for l in LANGUAGES}
    if lang not in known:
        lang = DEFAULT_LANG
    region = (region or known[lang]["region"]).upper()
    key = (lang, region)
    if key not in _clients:
        _clients[key] = Client(DB_PATH, lang=lang, region=region)
    c = _clients[key]
    c.ensure_login()
    return c


def client():
    """Cached client for the (lang, region) of the current request."""
    lang, region = req_lang()
    key = (lang, region)
    if key not in _clients:
        _clients[key] = Client(DB_PATH, lang=lang, region=region)
    c = _clients[key]
    c.ensure_login()
    return c


def err(e, status=400):
    if isinstance(e, ShortsWaveError):
        return jsonify({"ok": False, "code": e.code, "msg": e.msg}), status
    return jsonify({"ok": False, "code": -1, "msg": str(e)}), 500


# ------------------------------------------------------------------ helpers
MEDIA_HOST = "https://v.shortswave.com"


def absolute(url):
    """play_url comes back relative (e.g. /v1/play/<id>/v.m3u8?auth_key=..) and is
    served by the API host, but the segment URLs inside it point to the CDN."""
    if not url:
        return ""
    if url.startswith("http"):
        return url
    if url.startswith("/"):
        # An auth_key whose signature part is empty (`auth_key=720p`) answers 403
        # even for the account that owns the chapter, and the first-ever fetch of
        # a *freshly issued* URL can hand one out (measured 2026-10-26). Ask for
        # the URL again instead of caching a dead one.
        m = re.search(r"auth_key=([^&]*)", url)
        if m and "auth_key" in url and len(m.group(1)) <= 6:
            return ""
        return API_HOST + url
    return MEDIA_HOST + "/" + url


def hls_proxy_url(absolute_url):
    """Rewrite an upstream URL so the browser fetches it through us."""
    return "/proxy/hls?u=" + requests.utils.quote(absolute_url, safe="")


def rewrite_manifest(text, base_url):
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            out.append(line)
            continue
        if s.startswith("#"):
            # rewrite URI="..." inside tags (keys, media, maps)
            def repl(m):
                return 'URI="%s"' % hls_proxy_url(urljoin(base_url, m.group(1)))
            out.append(re.sub(r'URI="([^"]+)"', repl, line))
        else:
            out.append(hls_proxy_url(urljoin(base_url, s)))
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------- routes
@app.get("/api/health")
def health():
    try:
        c = client()
        c.ensure_login()
        u = c.user_info()
        return jsonify({"ok": True, "user_id": u.get("user_id"), "bonus": u.get("bonus"),
                        "coin": u.get("coin"), "is_vip": u.get("is_vip")})
    except Exception as e:  # noqa: BLE001
        return err(e)


@app.post("/api/login")
def login():
    try:
        c = client()
        u = c.login_anonymous((request.json or {}).get("source_channel", "googleplay"))
        return jsonify({"ok": True, "user": {k: u.get(k) for k in
                                             ("user_id", "bonus", "coin", "is_vip", "vip_type", "avatar", "user_name")}})
    except Exception as e:  # noqa: BLE001
        return err(e)


@app.get("/api/user")
def user():
    try:
        return jsonify({"ok": True, "user": client().user_info(refresh=True)})
    except Exception as e:  # noqa: BLE001
        return err(e)


@app.get("/api/lang")
def lang_list():
    lang, region = req_lang()
    return jsonify({"ok": True, "lang": lang, "region": region, "languages": LANGUAGES})


def _slim(it):
    """Normalise a DramaInfoItem / SearchBean into one UI shape."""
    return {
        "drama_id": it.get("drama_id") or it.get("dramaId"),
        "title": it.get("drama_title") or it.get("title"),
        "cover": it.get("drama_cover") or it.get("cover") or it.get("drama_cover_h"),
        "chapters": it.get("chapters") or it.get("chapterTotal"),
        "charge_chapters": it.get("charge_chapters"),
        "unlock_type": it.get("unlock_type") or it.get("unlockType"),
        "description": (it.get("description") or "")[:400],
        "tags": it.get("tags"),
        "sub_tags": it.get("sub_tags") or it.get("subTags"),
        "play_url": it.get("play_url"),
        "first_chapter_id": it.get("chapter_id") or it.get("chapterId"),
    }


@app.get("/api/config")
def config():
    """Everything the shell needs at boot: languages, categories, hot searches."""
    try:
        c = client()
        channels = []
        try:
            d = c._call("GET", "/v3/drama/bank/channel/list")
            for place in (d or {}).get("place_list") or []:
                channels.append({
                    "key": place.get("key"),
                    "name": place.get("name"),
                    "list": [{"id": t.get("id"), "name": t.get("name")}
                             for t in place.get("list") or []],
                })
        except ShortsWaveError:
            pass
        hot = []
        try:
            hot = [{"word": h.get("word") or h.get("title") or h.get("keyword"),
                    "id": h.get("id")} for h in (c._call("GET", "/v1/search/hotrecomm") or [])]
        except ShortsWaveError:
            pass
        displays = []
        try:
            displays = (c._call("GET", "/v3/discover/display/channel") or {}).get("channels") or []
        except ShortsWaveError:
            pass
        lang, region = req_lang()
        return jsonify({"ok": True, "lang": lang, "region": region, "languages": LANGUAGES,
                        "categories": channels, "display_channels": displays,
                        "hot_search": [h for h in hot if h.get("word")][:20]})
    except Exception as e:  # noqa: BLE001
        return err(e)


@app.get("/api/search")
def search():
    try:
        word = (request.args.get("q") or request.args.get("word") or "").strip()
        if not word:
            return jsonify({"ok": True, "q": "", "items": []})
        items = client()._call("GET", "/v1/search", params={"word": word}) or []
        return jsonify({"ok": True, "q": word, "items": [_slim(it) for it in items]})
    except Exception as e:  # noqa: BLE001
        return err(e)


@app.get("/api/category")
def category():
    """Category grid: /v3/drama/bank/drama/list?channel_id=&tag_id=&page=&page_size=
    (/v3/rcmd_by_category only answers with src_id+page together and 404s on the
    bare tag ids that /v3/drama/bank/channel/list hands out.)"""
    try:
        c = client()
        channel_id = int(request.args.get("channel_id", 0))
        tag_id = int(request.args.get("tag_id") or request.args.get("src_id")
                     or request.args.get("id") or 0)
        page = int(request.args.get("page", 1))
        size = int(request.args.get("page_size", 20))
        order_by = request.args.get("order_by")
        data = c._call("GET", "/v3/drama/bank/drama/list", params={
            "channel_id": channel_id, "tag_id": tag_id, "page": page, "page_size": size,
            **({"order_by": order_by} if order_by else {}),
        }) or {}
        items = []
        if isinstance(data, dict):
            for k in ("list", "drama_list", "items", "data"):
                if isinstance(data.get(k), list):
                    items = data[k]
                    break
        elif isinstance(data, list):
            items = data
        return jsonify({"ok": True, "channel_id": channel_id, "tag_id": tag_id, "page": page,
                        "is_more": bool(data.get("is_more")) if isinstance(data, dict) else False,
                        "items": [_slim(it) for it in items]})
    except Exception as e:  # noqa: BLE001
        return err(e)


@app.get("/api/rank")
def rank():
    try:
        c = client()
        d = c._call("GET", "/v3/discover/rank/all") or {}
        out = []
        blocks = [d] if isinstance(d, list) else (
            d.get("place_list") or d.get("rank_list") or d.get("list") or [])
        for block in blocks:
            out.append({
                "title": block.get("name") or block.get("title") or block.get("rank_name"),
                "rank_type": block.get("rank_type"),
                "items": [_slim(it) for it in (block.get("list") or block.get("drama_list") or [])],
            })
        if not out:
            top = c._call("GET", "/v3/top_unlock_list") or []
            out = [{"title": "Top unlocked", "items": [_slim(it) for it in top]}]
        return jsonify({"ok": True, "ranks": out})
    except Exception as e:  # noqa: BLE001
        return err(e)


@app.get("/api/feed")
def feed():
    try:
        page = int(request.args.get("page", 1))
        size = int(request.args.get("page_size", 20))
        kind = request.args.get("kind", "all")
        path = "/v3/more" if kind == "more" else "/v3/all"
        items = client().feed(page, size, path) or []
        slim = [{
            "drama_id": it.get("drama_id"),
            "title": it.get("drama_title"),
            "cover": it.get("drama_cover") or it.get("drama_cover_h"),
            "chapters": it.get("chapters"),
            "charge_chapters": it.get("charge_chapters"),
            "unlock_type": it.get("unlock_type"),
            "description": (it.get("description") or "")[:400],
            "tags": it.get("tags"),
            "play_url": it.get("play_url"),
            "first_chapter_id": it.get("chapter_id"),
        } for it in items]
        for it in slim:
            _remember_feed_title(it["drama_id"], it["title"], it["cover"])
        _flush_feed_titles()
        return jsonify({"ok": True, "page": page, "items": slim})
    except Exception as e:  # noqa: BLE001
        return err(e)


def _first_chapter_id(chs):
    """The detail page deep-links straight to a free playable episode, so a
    shared URL plays something instead of showing a bare list."""
    playable = [ch for ch in chs if ch.get("play_url")]
    for pool in (playable, chs):
        free = [ch for ch in pool if ch.get("is_free") and ch.get("chapter_id")]
        if free:
            return free[0]["chapter_id"]
    ch = (playable or chs or [None])[0]
    return ch.get("chapter_id") if ch else None


@app.get("/api/drama/<drama_id>")
def drama(drama_id):
    try:
        c = client()
        chs = c.chapters(drama_id)
        lang = request.args.get("lang", "en")
        return jsonify({"ok": True, "drama_id": drama_id,
                        "title": title_for(drama_id, lang),
                        "cover": (catalogue_titles().get(drama_id) or {}).get("cover")
                                 or (feed_titles().get(drama_id) or {}).get("cover")
                                 or _cover_from_first_frame(chs),
                        "first_chapter_id": _first_chapter_id(chs),
                        "chapters": [{
            "chapter_id": ch.get("chapter_id"),
            "index": ch.get("chapter_index"),
            "name": ch.get("chapter_name"),
            "is_free": bool(ch.get("is_free")),
            "price": ch.get("chapter_price"),
            "first_frame": ch.get("first_frame"),
        } for ch in chs]})
    except Exception as e:  # noqa: BLE001
        return err(e)


# --- catalogue titles (sweep output) -------------------------------------
# /api/drama/<id> cannot return a title (upstream chapter list has none), but the
# canonical /d/<slug>-<id> URL needs one. sweep_titles.py drops work/sweep/
# title_index.json ({dramas: {id: {t: {lang: title}, c: cover}}}); load it lazily
# and reload when the file is rewritten.
_SWEEP_TITLES = os.path.join(BASE_DIR, "sweep", "title_index.json")
_titles = None
_titles_mtime = 0.0


def catalogue_titles():
    global _titles, _titles_mtime
    try:
        mtime = os.path.getmtime(_SWEEP_TITLES)
    except OSError:
        return {}
    if _titles is None or mtime != _titles_mtime:
        try:
            with open(_SWEEP_TITLES, encoding="utf-8") as f:
                raw = (json.load(f) or {}).get("dramas") or {}
            _titles = {did: {"titles": rec.get("t") or {}, "cover": rec.get("c") or None}
                       for did, rec in raw.items()}
            _titles_mtime = mtime
        except Exception:  # noqa: BLE001 — a broken/missing sweep must not break the API
            _titles = {}
    return _titles


def _cover_from_first_frame(chs):
    """Poster for dramas the sweep has not seen yet: the webp stills sit next to
    the first episode's cover frame (…/image/<id>/1-v1/720p/first_frame.jpg ->
    …/image/cover/<id>_1.webp), which is exactly what the app shows in lists."""
    for ch in chs or []:
        ff = ch.get("first_frame") or ""
        m = re.search(r"/image/(\d+)/", ff)
        if m:
            return f"https://s.shortswave.com/image/cover/{m.group(1)}_1.webp"
    return None


# --- feed titles (secondary source) ---------------------------------------
# sweep/title_index.json can lag behind upstream: a drama that just landed in
# the feed is missing there, /api/drama then answers title=null and the UI can
# never canonicalise /d/<slug>-<id> (it keeps whatever slug the link had). The
# feed itself carries drama_title, so remember every title it shows.
_FEED_TITLES = os.path.join(BASE_DIR, "feed_titles.json")
_feed_titles = None


def feed_titles():
    global _feed_titles
    if _feed_titles is None:
        try:
            with open(_FEED_TITLES, encoding="utf-8") as f:
                _feed_titles = json.load(f) or {}
        except Exception:  # noqa: BLE001 — optional cache
            _feed_titles = {}
    return _feed_titles


def _remember_feed_title(drama_id, title, cover=None):
    if not drama_id or not title:
        return
    cache = feed_titles()
    cur = cache.get(drama_id) or {}
    if cur.get("title") == title and (cur.get("cover") or not cover):
        return
    cache[drama_id] = {"title": title, "cover": cover or cur.get("cover")}
    _feed_titles_unsaved.add(drama_id)


_feed_titles_unsaved = set()


def _flush_feed_titles():
    if not _feed_titles_unsaved:
        return
    try:
        tmp = _FEED_TITLES + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(feed_titles(), f, ensure_ascii=False)
        os.replace(tmp, _FEED_TITLES)
    except Exception:  # noqa: BLE001 — cache only, never break a request
        return
    _feed_titles_unsaved.clear()


def title_for(drama_id, lang="en"):
    rec = catalogue_titles().get(drama_id) or {}
    titles = dict(rec.get("titles") or {})
    fb = (feed_titles().get(drama_id) or {}).get("title")
    if fb and not titles:
        titles = {"en": fb}
    if not titles:
        # Neither the sweep index nor anything the feed served so far knows this
        # id (a deep link opened before the feed was ever fetched). Ask upstream
        # once and remember it, otherwise the UI can never canonicalise the slug.
        try:
            for it in client().feed(1, 20, "/v3/all") or []:
                _remember_feed_title(it.get("drama_id"), it.get("drama_title"),
                                     it.get("drama_cover") or it.get("drama_cover_h"))
            _flush_feed_titles()
        except Exception:  # noqa: BLE001 — best effort, offline must not break /api/drama
            pass
        fb = (feed_titles().get(drama_id) or {}).get("title")
        if fb:
            titles = {"en": fb}
    if not titles:
        return None
    order = [lang, "en"] + list(titles.keys())
    for l in order:
        if titles.get(l):
            return titles[l]
    return None


@app.get("/api/drama/<drama_id>/title")
def drama_title(drama_id):
    """Catalogue title for a drama id (used to canonicalise /d/ deep links)."""
    rec = catalogue_titles().get(drama_id) or {}
    return jsonify({"ok": True, "drama_id": drama_id,
                    "title": title_for(drama_id, request.args.get("lang", "en")),
                    "cover": rec.get("cover"),
                    "titles": rec.get("titles") or {}})


# --- full catalogue list (the whole 4750-drama sweep, paged) --------------
# The home page lists *every* title, not the ~100 that /api/feed / /api/rank show.
# The list comes from the sweep artifact (build_titles_artifact.py ->
# work/sweep4/titles.tsv), loaded lazily and reloaded when the file changes, so
# re-running the sweep needs no server restart. Paging happens here, not in the
# browser: pushing all 4750 rows to the client would be a 1.5 MB payload on a
# phone. Sorted by title, one ordered list per view ('all' / a language), and
# `page`/`page_size` slice it -- identical paging for every caller.
_SWEEP_DIR = os.environ.get("SW_SWEEP_DIR", os.path.join(BASE_DIR, "sweep4"))
CATALOGUE_COLS = ["drama_id", "title", "slug", "primary_lang", "languages", "chapters",
                  "chapter_count", "free_chapters", "charge_chapters", "unlock_type",
                  "playable", "play_kind", "subtitle_count", "subtitle_langs", "tags",
                  "cover", "sources", "categories", "ranks"]
_catalogue = None
_catalogue_stamp = None
_catalogue_lock = threading.Lock()
CATALOGUE_MAX_PAGE = 200


def _catalogue_path():
    return os.path.join(_SWEEP_DIR, "titles.tsv")


def catalogue_rows():
    """[(row dict, title) ...] sorted by title, plus the per-language split.

    Returns {"rows": [...], "by_lang": {lang: [row, ...]}, "counts": {...}}.
    A missing/broken tsv degrades to an empty catalogue: the home page must
    still render the rest of the app."""
    global _catalogue, _catalogue_stamp
    path = _catalogue_path()
    try:
        st = os.stat(path)
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        return {"rows": [], "by_lang": {}, "counts": {}, "generated_at": None}
    if _catalogue is not None and stamp == _catalogue_stamp:
        return _catalogue
    with _catalogue_lock:
        if _catalogue is not None and stamp == _catalogue_stamp:
            return _catalogue
        rows = []
        try:
            with open(path, encoding="utf-8", newline="") as f:
                for rec in csv.DictReader(f, delimiter="\t"):
                    if not rec.get("drama_id"):
                        continue
                    rows.append({k: (rec.get(k) or "") for k in CATALOGUE_COLS})
        except (OSError, csv.Error):
            rows = []
        rows.sort(key=lambda r: ((r["title"] or r["drama_id"]).casefold(), r["drama_id"]))
        by_lang = {}
        for r in rows:
            for lang in (r["languages"] or r["primary_lang"] or "").split("|"):
                if lang:
                    by_lang.setdefault(lang, []).append(r)
        for lang, lst in by_lang.items():
            # inside one language, the title in that language wins the sort
            lst.sort(key=lambda r: ((r["title"] or r["drama_id"]).casefold(), r["drama_id"]))
        counts = {lang: len(lst) for lang, lst in by_lang.items()}
        _catalogue = {"rows": rows, "by_lang": by_lang, "counts": counts,
                      "generated_at": int(st.st_mtime)}
        _catalogue_stamp = stamp
    return _catalogue


def _catalogue_item(r, lang):
    """One list card. `live` = the sweep saw the first episode playable; dramas
    without a free episode open in the detail page, which can unlock them."""
    n = r["chapter_count"] or r["chapters"]
    return {
        "drama_id": r["drama_id"],
        "title": r["title"] or r["drama_id"],
        "cover": r["cover"] or None,
        "chapters": int(n) if n.isdigit() else n,
        "free_chapters": int(r["free_chapters"]) if r["free_chapters"].isdigit() else None,
        "charge_chapters": r["charge_chapters"],
        "unlock_type": r["unlock_type"],
        "playable": r["playable"] == "True",
        "lang": r["primary_lang"] or lang,
        "languages": [l for l in r["languages"].split("|") if l],
        "tags": r["tags"],
        "slug": r["slug"],
    }


@app.get("/api/catalogue")
def catalogue():
    """Paged list of every drama in the sweep, sorted by title.

    `?lang=all` (default) walks the whole catalogue; `?lang=en` only the titles
    that exist in that language, labelled with that language's title.
    `?q=` filters by title substring (case-insensitive)."""
    lang = (request.args.get("lang") or "all").lower()
    q = (request.args.get("q") or "").strip().casefold()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        size = int(request.args.get("page_size", 60))
    except (TypeError, ValueError):
        size = 60
    size = max(1, min(size, CATALOGUE_MAX_PAGE))
    cat = catalogue_rows()
    if lang in ("", "all", "*"):
        source = cat["rows"]
    else:
        source = cat["by_lang"].get(lang) or []
    if q:
        source = [r for r in source if q in (r["title"] or "").casefold()]
    total = len(source)
    start = (page - 1) * size
    chunk = source[start:start + size]
    return jsonify({"ok": True, "lang": lang, "q": q, "page": page, "page_size": size,
                    "total": total, "count": len(chunk),
                    "has_more": start + len(chunk) < total,
                    "total_all": len(cat["rows"]), "counts": cat["counts"],
                    "items": [_catalogue_item(r, lang) for r in chunk]})


@app.get("/api/catalogue/meta")
def catalogue_meta():
    """Counts the home page shows above the list (per language + total)."""
    cat = catalogue_rows()
    return jsonify({"ok": True, "total": len(cat["rows"]), "counts": cat["counts"],
                    "generated_at": cat.get("generated_at"),
                    "source": os.path.basename(_catalogue_path())})


@app.get("/api/chapter/<drama_id>/<chapter_id>")
def chapter(drama_id, chapter_id):
    try:
        c = client()
        d = c.chapter_detail(drama_id, chapter_id) or {}
        # the default account may not own this chapter (a rotated account paid
        # for it) -- reuse the URL that unlock issued, and remember any fresh one
        pu = d.get("play_url") or _stored_play_url(chapter_id) or ""
        if d.get("play_url"):
            _remember_play_url(chapter_id, d["play_url"])
        subs = []
        for s in (d.get("sublist") or []):
            if not s.get("url"):
                continue
            subs.append({
                "language": s.get("language"),
                "languageId": s.get("languageId"),
                "label": s.get("subtitleDesc") or s.get("language") or "subtitle",
                "format": s.get("format") or "webvtt",
                "url": "/proxy/sub?u=" + requests.utils.quote(s["url"], safe=""),
            })
        return jsonify({"ok": True, "chapter": {
            "chapter_id": d.get("chapter_id"),
            "name": d.get("chapter_name"),
            "index": d.get("chapter_index"),
            "is_free": d.get("chapter_is_free") == 1,
            "price": d.get("price"),
            "unlock_type": d.get("unlock_type"),
            "duration": d.get("chapter_duration"),
            "playable": bool(pu),
            "play": hls_proxy_url(absolute(pu)) if pu else None,
            "subtitles": subs,
            "is_ad_unlock_user": d.get("is_ad_unlock_user"),
            "current_ad_unlock_count": d.get("current_ad_unlock_count"),
            "max_ad_unlock_count": d.get("max_ad_unlock_count"),
            "coin": d.get("coin"),
            "bonus": d.get("bonus"),
        }})
    except Exception as e:  # noqa: BLE001
        return err(e)


@app.post("/api/unlock")
def unlock():
    try:
        body = request.json or {}
        d = client().unlock(body["drama_id"], body["chapter_id"],
                            body.get("unlock_type", "coin"), int(body.get("auto_pay", 0))) or {}
        pu = d.get("play_url") or ""
        return jsonify({"ok": bool(pu), "play": hls_proxy_url(absolute(pu)) if pu else None,
                        "code_msg": "unlocked" if pu else "server returned empty play_url "
                                                         "(insufficient coin / flag-gated)",
                        "unlock_type": d.get("unlock_type"), "price": d.get("price"),
                        "coin": d.get("coin"), "bonus": d.get("bonus")})
    except Exception as e:  # noqa: BLE001
        return err(e)


# -------------------------------------------------------- bulk unlock (detail page)
# Opening a drama page unlocks every chapter of it in one go, so the player never
# stops at "episode N is locked" when the viewer presses Next.
#
# What the app itself does (measured against api.shortswave.com, 2026-10-26):
#   * free episodes answer chapter/detail with a play_url right away;
#   * a paid episode is unlocked with `unlock_type=coin` (the app hardcodes
#     "coin" and just changes auto_pay) -- it is paid from **bonus**, not coin.
#     Catalogue price is 50; user.coin stays 0 while user.bonus walks down
#     152 -> 102 -> 52 -> 2, one 50-step per chapter.
#   * `unlock_type=bonus` is rejected (401 "invalid unlock_type value"); only
#     "coin" and "ad" exist.
#   * auto_pay is a switch, not a mode: `0` answers a play_url only when the
#     chapter is already open (otherwise `play_url: ""` with price 50), while
#     **any** non-zero value (-1, 1, 2, 99) charges the 50 (re-measured
#     2026-10-26; an older note here claimed auto_pay=0 played for free).
#   * POST /v1/drama/chapter/detail/part {drama_id: <int intid>, chapter_indexes:
#     [..]} is a batch unlock: it charges 50 per not-yet-open index, in order,
#     and answers `{}`. It is *not* cheaper, and with the drama_id string it is
#     a 500. Nothing to gain over /v1/unlock/drama/chapter.
#   * /task/v2/submit is **not** verified: it only checks the task's limit, so
#     looping all tasks fills the daily ad-bonus pool (~747 bonus = ~15
#     chapters) on any account, including a fresh anonymous one. After the pool
#     is empty the endpoint answers 41 "the task is in limitation".
#   * `unlock_type=ad` is a bonus path too, capped per device+day
#     (max_ad_unlock_count=10, then 402 "too many times"), and the cap follows
#     the device, not the account: a fresh device_uuid also got "too many
#     times". So it must never be tried first -- it burns the 10 free chapters
#     before the bonus path, which needs no ad at all.
#   * unlocking is strictly per account: chapter/detail is what marks a chapter
#     open, and account B still gets an empty play_url for chapters opened by
#     account A (only the *media* URL is session-free, see /proxy/hls).
# So the sweep is: chapter/detail -> unlock(coin, auto_pay=1), with a bonus farm
# whenever the balance is below `price`, then unlock(ad) only as a last resort.
#
# Chapters already unlocked keep answering chapter/detail with a play_url, so
# open ones cost nothing to detect and the sweep is cheap to re-run.
UNLOCK_CACHE_PATH = os.path.join(BASE_DIR, "unlock_cache.json")
UNLOCK_CACHE_TTL = 6 * 3600          # an unlocked chapter stays unlocked
_CATALOGUE_PRICE = 50                # bonus per paid chapter, same for every drama
_unlock_lock = threading.Lock()


def _unlock_cache():
    try:
        with open(UNLOCK_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _unlock_cache_save(data):
    try:
        tmp = UNLOCK_CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, UNLOCK_CACHE_PATH)
    except OSError:
        pass


# Unlocking is per account: a chapter paid for by a rotated account answers an
# empty play_url for the default client, so the media URL that paid for it is
# stored here. Every play_url is absolute and its auth_key is chapter-bound, and
# once issued the media plays without any session (measured), so keeping it is
# all the app needs to play that chapter from any account.
PLAY_URLS_PATH = os.path.join(BASE_DIR, "play_urls.json")
_play_urls_lock = threading.Lock()


def _play_urls():
    try:
        with open(PLAY_URLS_PATH, encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _remember_play_url(chapter_id, play_url):
    if not (chapter_id and play_url):
        return
    with _play_urls_lock:
        data = _play_urls()
        if data.get(chapter_id) == play_url:
            return
        data[chapter_id] = play_url
        try:
            tmp = PLAY_URLS_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, PLAY_URLS_PATH)
        except OSError:
            pass


def _stored_play_url(chapter_id):
    return _play_urls().get(chapter_id) or None


def _is_open(d):
    return bool(d and d.get("play_url"))


def _try_unlock(c, drama_id, chapter_id, unlock_type, auto_pay=0):
    """(play_url or None, error message or None).

    Upstream answers HTTP 200 with `play_url: ""` when the balance cannot cover
    the price -- no error code, no message (measured with bonus 47 < price 50).
    That is a failure, so it must not be mistaken for "unlocked"."""
    try:
        r = c.unlock(drama_id, chapter_id, unlock_type=unlock_type,
                     auto_pay=auto_pay) or {}
    except ShortsWaveError as e:
        return None, e.msg or f"code {e.code}"
    play = (r.get("play_url") or r.get("play_url_720p") or "").strip() or None
    if play and not absolute(play):
        # dead auth_key (`auth_key=720p`) -- the chapter *was* paid for, so ask
        # once more instead of reporting a failure and losing the 50 bonus
        try:
            r2 = c.chapter_detail(drama_id, chapter_id) or {}
        except ShortsWaveError:
            r2 = {}
        play = (r2.get("play_url") or r2.get("play_url_720p") or "").strip() or None
        if play and not absolute(play):
            play = None
    if play:
        _remember_play_url(chapter_id, play)
        return play, None
    if unlock_type == "ad":
        return None, "too many times" if not r.get("is_ad_unlock_user") else "no play url"
    return None, f"not enough bonus (bonus {_bonus_of(r)} < price)"


def _bonus_of(d):
    try:
        return int(d.get("bonus") or 0)
    except (TypeError, ValueError):
        return 0


# Rotating to a fresh anonymous account is the only way past a drained bonus
# pool: /task/v2/submit pays ~747 bonus per new device_uuid (no proof of work)
# and a paid chapter costs 50, so one drama of 40+ paid chapters needs 3+ accounts.
#
# Measured 2026-10-26 (drama 6adacb81…, 61 paid chapters): every fresh account
# pays *exactly* 14 chapters (747 bonus, 47 left over) and there is no per-IP
# limit -- accounts 8..11 in a row all farmed 747. The old fixed cap (2, then 4)
# was therefore the real blocker: the sweep stopped with chapters still locked
# and told the user "too many times". The cap is now derived from the drama size
# (paid chapters / 14, +2 slack), with FARM_MAX_ACCOUNTS as a hard safety stop.
FARM_BONUS_PER_ACCOUNT = 747        # measured: one fresh account/device
FARM_MAX_ACCOUNTS = int(os.environ.get("SW_FARM_MAX_ACCOUNTS", "12"))
FARM_MAX_ROUNDS = 3                 # passes over the drama (retry leftovers)
FARM_ACCOUNTS_PER_SWEEP = int(os.environ.get("SW_FARM_ACCOUNTS", "4"))
# Parallel workers of one sweep: each has its own account/device and streams its
# own chapters, so a 30-60 chapter drama finishes in a handful of round trips
# instead of one call after another. Measured safe: there is no per-IP limit
# (accounts 8..11 in a row each farmed the full 747) and the daily task limits
# are enforced per account upstream, so more workers only add accounts sooner.
UNLOCK_WORKERS = max(1, int(os.environ.get("SW_UNLOCK_WORKERS", "6")))
UNLOCK_RETRY_ROUNDS = max(0, int(os.environ.get("SW_UNLOCK_RETRY_ROUNDS", "1")))
# how long the sweep waits for a parallel farm before going on without it
FARM_ACCOUNT_TIMEOUT = float(os.environ.get("SW_FARM_TIMEOUT", "90"))
FARM_DB_DIR = os.path.join(BASE_DIR, ".farm")


def _farm_account(lang=DEFAULT_LANG, region=None):
    """New device_uuid -> new anonymous account, then claim every task.

    Returns (client, gained_bonus). A new session DB per account keeps the
    tokens apart (the client stores its session in sqlite)."""
    try:
        os.makedirs(FARM_DB_DIR, exist_ok=True)
        # Never reuse an existing file: Client() adopts the session stored in it
        # (device_uuid + tokens), so a reused name silently re-runs the *old*
        # account whose daily pool is spent (bug: len(existing)+1 collided with
        # auto015.db that a previous run had left behind -> farmed 0, unlocks
        # kept failing). Pick the first free name instead.
        n = len([d for d in os.listdir(FARM_DB_DIR) if d.startswith("auto")]) + 1
        while os.path.exists(os.path.join(FARM_DB_DIR, f"auto{n:03d}.db")):
            n += 1
        db = os.path.join(FARM_DB_DIR, f"auto{n:03d}.db")
    except OSError:
        db = os.path.join(BASE_DIR, f"farm_auto_{int(time.time() * 1000)}.db")
    c = Client(db, lang=lang or DEFAULT_LANG, region=region or "US")
    c.rotate_account()   # fresh anonymous login on the fresh device_uuid
    gained = (c.farm_tasks() or {}).get("bonus") or 0
    try:
        gained = int(gained)
    except (TypeError, ValueError):
        gained = 0
    return c, gained


def sweep_unlock(drama_id, force=False, progress=None, c=None, rotate=True):
    """Open every chapter of a drama the way the app does -- in parallel.

    Per chapter: chapter/detail (free / already open) -> unlock(coin, auto_pay=1)
    -> unlock(ad) as a last resort. Paid chapters are paid with **bonus** (not
    coin): the catalogue price is 50 and `user.bonus` is what decreases (measured:
    bonus 152 -> 102 -> 52 -> 2 while coin stayed 0).

    A fresh account/device yields ~747 bonus = **exactly 14 chapters** (50 each),
    so a 60-chapter drama needs 5 accounts. `rotate` keeps adding new anonymous
    accounts (new device_uuid => new pool) until every chapter is open.

    Speed (the old sweep was one call after another, so a 60-chapter drama took
    minutes *and* a full sequential farm every time the balance ran low):
      * SW_UNLOCK_WORKERS (default 6) workers, each with its own account/device;
        every worker runs its own farm, so farm latency overlaps with unlocking;
      * farm_tasks() submits the ~30 tasks concurrently and caches each task's
        per-day limit, so a second farm on one account costs <1 s, not 60 s;
      * chapters already paid for are remembered in play_urls.json and skipped;
      * a second pass retries only what failed (transient socket/403/timeout),
        with the final state read fresh -- the bonus is already spent by then, so
        the retry costs no unlock calls.
    Shared lists are guarded by `lock`; a straggler that pays for a chapter after
    the result was built is still stored in play_urls.json and opens on reload."""

    def _result(total, o, f, accounts, farmed, bonus, price, left, u):
        return {"ok": True, "drama_id": drama_id, "total": total, "open": o,
                "failed": f, "farmed_bonus": farmed, "accounts": accounts,
                "bonus_short": bool(left) and bonus < price,
                "price": price if left else None,
                "quota_hit": any("too many times" in (x.get("msg") or "") for x in f),
                "coin": u.get("coin"), "bonus": u.get("bonus"),
                "current_ad_unlock_count": u.get("current_ad_unlock_count"),
                "max_ad_unlock_count": u.get("max_ad_unlock_count")}

    c = c or client()
    chs = c.chapters(drama_id) or []
    if progress:
        progress({"total": len(chs)})   # so the UI can size its progress bar
    locked = [ch for ch in chs if ch.get("chapter_id")]
    if not locked:
        return _result(len(chs), [], [], 0, 0, 0, None, [], {})

    opened, failed, prices = [], [], {}
    farmed, accounts = [0], [1 if rotate else 0]
    lock = threading.Lock()             # guards opened/failed/prices/farmed/accounts
    stop = threading.Event()            # a worker hit "too many times" upstream
    spare = []                          # workers idle at the end of a pass

    def _snapshot(phase=None):
        with lock:
            o, f = sorted(opened), list(failed)
        if progress:
            progress({"open": o, "failed": f, "farmed_bonus": farmed[0],
                      "phase": phase, "accounts": accounts[0]})
        return o

    def _remember(cid, idx, play=None, msg=None):
        """opened / failed bookkeeping (one entry per chapter)."""
        with lock:
            if play:
                if cid not in opened:
                    opened.append(cid)
                failed[:] = [f for f in failed if f.get("chapter_id") != cid]
            elif cid not in opened:
                failed[:] = [f for f in failed if f.get("chapter_id") != cid]
                failed.append({"chapter_id": cid, "index": idx, "msg": msg})
        _snapshot()

    def _new_account(base=None):
        """Fresh account (own device_uuid) with its daily bonus pool farmed."""
        if base is None:
            try:
                base = _farm_account(c.lang, c.region)
            except Exception:       # noqa: BLE001 — offline/stuck upstream
                return None
        if not callable(getattr(base, "new_device", None)):
            return None
        try:
            gain = int(base.new_device() or 0)
        except Exception:           # noqa: BLE001
            return None
        with lock:
            accounts[0] += 1
            farmed[0] += gain
        return base

    def _worker(worker, todo):
        """Own chapter stream over `todo`.

        A chapter this worker's account cannot pay for gets its own brand-new
        account (farmed right there, in this thread), so a drained account never
        stalls the worker: it simply moves on under a fresh one."""
        if worker is None:
            return
        for ch in todo:
            if stop.is_set():
                break
            cid = ch.get("chapter_id")
            if not cid:
                continue
            with lock:
                if cid in opened:
                    continue
            paid = _stored_play_url(cid)
            if paid:
                # already paid by some account: the media URL is session-free,
                # so the chapter is open without spending anything again
                _remember(cid, ch.get("chapter_index"), play=paid)
                continue
            d, err = _chapter_state(worker, drama_id, cid)
            if err:
                _remember(cid, ch.get("chapter_index"), msg=err)
                continue
            with lock:
                prices[cid] = int((d or {}).get("price") or 0) or _CATALOGUE_PRICE
            if _is_open(d):
                _remember_play_url(cid, d.get("play_url"))
                _remember(cid, ch.get("chapter_index"), play=d.get("play_url"))
                continue
            play, msg, fresh = _pay_for_chapter(worker, drama_id, cid, prices[cid],
                                                _new_account if rotate else None)
            if fresh is not None:
                worker = fresh       # continue the stream under the new account
            if play:
                _remember(cid, ch.get("chapter_index"), play=play)
            elif "too many times" in (msg or ""):
                # device/day ad quota: every other account of this sweep hits it
                # too, so stop instead of burning minutes on it
                _remember(cid, ch.get("chapter_index"), msg=msg)
                stop.set()
                break
            else:
                _remember(cid, ch.get("chapter_index"), msg=msg)
        with lock:
            spare.append(worker)

    def _remaining():
        with lock:
            done = set(opened) | {f.get("chapter_id") for f in failed}
        return [ch for ch in locked if ch.get("chapter_id") not in done]

    def _run(workers):
        ths = [threading.Thread(target=_worker, args=(cl, list(locked)), daemon=True,
                                name=f"unlock-w{i}-{drama_id[:6]}")
               for i, cl in enumerate(workers)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()

    # pass 1: the caller's client + fresh accounts, all streaming in parallel
    _snapshot()
    workers = [c]
    if rotate and UNLOCK_WORKERS > 1:
        # farms run concurrently with the streams: a worker whose account runs
        # dry waits for the next farmed account instead of farming it serially
        pool = _cf.ThreadPoolExecutor(max_workers=UNLOCK_WORKERS - 1)
        futs = [pool.submit(_new_account) for _ in range(UNLOCK_WORKERS - 1)]
        runner = threading.Thread(target=_run, args=([c],), daemon=True,
                                  name=f"unlock-main-{drama_id[:6]}")
        runner.start()
        for f in futs:
            try:
                cl = f.result(timeout=FARM_ACCOUNT_TIMEOUT)
            except Exception:       # noqa: BLE001 — a slow farm must not hang the sweep
                cl = None
            if cl is not None:
                workers.append(cl)
        pool.shutdown(wait=False)
        runner.join()
        rest = [cl for cl in workers[1:]
                if not stop.is_set() and _remaining()]
        if rest:
            _run(rest)

    # pass 2: retry only the leftovers (transient errors), nothing to unlock
    for _ in range(UNLOCK_RETRY_ROUNDS):
        if stop.is_set() or not _remaining() or not spare:
            break
        workers = [cl for cl in spare if cl is not None]
        spare.clear()
        _snapshot()
        _run(workers)

    left = _remaining()
    with lock:
        o, f = list(opened), list(failed)
        price = None
        for ch in left:
            price = prices.get(ch.get("chapter_id")) or price
        price = price or _CATALOGUE_PRICE
        workers_all = [c] + [cl for cl in spare if cl is not None]
    u = {}
    for cl in workers_all:
        try:
            u = cl.user_info(refresh=True) or {}
        except Exception:           # noqa: BLE001
            u = {}
        if u:
            break
    bonus = int(u.get("bonus") or 0)
    if progress:
        progress({"open": sorted(o), "failed": f, "farmed_bonus": farmed[0],
                  "accounts": accounts[0], "phase": "done"})
    return _result(len(chs), o, f, accounts[0], farmed[0], bonus, price, left, u)


def _chapter_state(c, drama_id, chapter_id):
    """(detail, error) for one chapter, retried once on a transient failure.

    A socket timeout / 5xx / dead `auth_key=720p` is not "still locked": retrying
    once costs a second and keeps the sweep from reporting a chapter it could not
    even read as a chapter that cannot be opened."""
    last = None
    for attempt in range(2):
        try:
            d = c.chapter_detail(drama_id, chapter_id) or {}
        except ShortsWaveError as e:
            last = e.msg or f"code {e.code}"
        except Exception as e:      # noqa: BLE001 — timeout, reset, bad JSON
            last = (str(e)[:80] or "request failed")
        else:
            if d or _is_open(d):
                return d, None
            last = "empty chapter detail"
        if attempt == 0:
            time.sleep(0.5)
    return None, last


def _pay_for_chapter(c, drama_id, chapter_id, price, new_account=None):
    """(play_url, error, new_client) -- unlock one chapter with bonus.

    The upstream answers `play_url: ""` (no error) when the bonus cannot cover
    the price, so a farm is attempted before giving up; a farm on an account
    whose tasks were already claimed today is cheap (per-task limits are cached).
    When the account is out of pools, a brand-new account is farmed right here
    and handed back as the third value, so the caller continues under it."""
    play, msg = _try_unlock(c, drama_id, chapter_id, "coin", auto_pay=1)
    if play:
        return play, None, None
    try:
        c.farm_tasks()
    except Exception:               # noqa: BLE001 — keep trying other paths
        pass
    play, msg2 = _try_unlock(c, drama_id, chapter_id, "coin", auto_pay=1)
    if play:
        return play, None, None
    msg = msg2 or msg
    fresh = None
    if new_account is not None and not (msg and "too many times" in msg):
        cl = new_account(c)
        if cl is not None:
            fresh = cl
            play, msg3 = _try_unlock(cl, drama_id, chapter_id, "coin", auto_pay=1)
            if play:
                return play, None, fresh
            msg = msg3 or msg
    play, msg_ad = _try_unlock(c, drama_id, chapter_id, "ad")
    return (play, None, fresh) if play else (None, msg_ad or msg, fresh)


# A sweep over a 30-50 episode drama is dozens of upstream calls plus task
# farming, i.e. minutes, not seconds. Running it inside the request made
# /api/unlock_all sit there until the browser/proxy gave up (measured: >60 s of
# silence on the first page open, which looked exactly like a hung UI). So the
# endpoint now starts a background job and reports its state; the detail page
# polls. `?wait=1` keeps the old blocking behaviour for scripts/curl.
UNLOCK_JOB_TTL = 30 * 60
_unlock_jobs = {}
_unlock_jobs_lock = threading.Lock()


def _unlock_state(drama_id):
    """Public view of a job (merged with the last finished sweep, if any)."""
    with _unlock_jobs_lock:
        job = dict(_unlock_jobs.get(drama_id) or {})
    if job:
        return {"drama_id": drama_id, "running": bool(job.get("running")),
                "total": job.get("total"), "open": job.get("open") or [],
                "failed": job.get("failed") or [],
                "farmed_bonus": job.get("farmed_bonus") or 0,
                "accounts": job.get("accounts") or 0,
                "phase": job.get("phase") or ("sweeping" if job.get("running") else "done"),
                "error": job.get("error"),
                "bonus": job.get("bonus"), "coin": job.get("coin"),
                "price": job.get("price"), "bonus_short": job.get("bonus_short"),
                "quota_hit": job.get("quota_hit"),
                "current_ad_unlock_count": job.get("current_ad_unlock_count"),
                "max_ad_unlock_count": job.get("max_ad_unlock_count"),
                "at": job.get("at"), "started": job.get("started"),
                "cached": False}
    rec = _unlock_cache().get(drama_id)
    if rec:
        return {**rec, "running": False, "phase": "done", "cached": True}
    return {"drama_id": drama_id, "running": False, "phase": "idle", "total": None,
            "open": [], "failed": [], "cached": False}


def _start_unlock_job(drama_id, c=None, total=None):
    with _unlock_jobs_lock:
        job = _unlock_jobs.get(drama_id)
        if job and job.get("running"):
            return job                      # already sweeping: just report it
        job = {"running": True, "phase": "starting", "started": time.time(), "at": time.time(),
               "total": total, "open": [], "failed": [], "farmed_bonus": 0, "accounts": 0,
               "bonus": None, "coin": None, "price": None, "bonus_short": None,
               "quota_hit": None}
        _unlock_jobs[drama_id] = job

    def progress(part):
        with _unlock_jobs_lock:
            j = _unlock_jobs.get(drama_id)
            if j is not None:
                j.update({k: v for k, v in part.items() if v is not None})

    def worker():
        try:
            res = sweep_unlock(drama_id, progress=progress, c=c)
        except Exception as e:  # noqa: BLE001 — a sweep must never kill the server
            res = {"ok": False, "error": str(e), "open": [], "failed": []}
        res.pop("ok", None)
        res["at"] = time.time()
        res["running"] = False
        with _unlock_jobs_lock:
            _unlock_jobs[drama_id] = {**(_unlock_jobs.get(drama_id) or {}), **res}
        try:
            cache = _unlock_cache()
            cache[drama_id] = {k: res.get(k) for k in
                               ("drama_id", "total", "open", "failed", "farmed_bonus", "accounts",
                                "quota_hit", "bonus_short", "price", "coin", "bonus",
                                "current_ad_unlock_count", "max_ad_unlock_count", "at")}
            _unlock_cache_save(cache)
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=worker, daemon=True, name=f"unlock-{drama_id[:8]}").start()
    return job


def _unlock_jobs_sweep():
    cutoff = time.time() - UNLOCK_JOB_TTL
    with _unlock_jobs_lock:
        for k in [k for k, j in _unlock_jobs.items()
                  if not j.get("running") and j.get("at", 0) < cutoff]:
            _unlock_jobs.pop(k, None)


@app.get("/api/unlock_all/<drama_id>")
def unlock_all(drama_id):
    """Unlock every chapter of a drama (called when the detail page opens).

    Returns immediately with the job state; the caller polls this same URL until
    `running` is false. A finished sweep with no failed chapters is cached for
    6 h, one with failures is retried on the next page open (a new-day ad quota
    or a balance top-up can open them later) -- `?refresh=1` forces a retry now,
    `?wait=1` blocks until the sweep finishes (scripts, curl)."""
    fresh = request.args.get("refresh") in ("1", "true")
    wait = request.args.get("wait") in ("1", "true")
    try:
        _unlock_jobs_sweep()
        with _unlock_jobs_lock:
            running = bool((_unlock_jobs.get(drama_id) or {}).get("running"))
        if not running and not fresh:
            rec = _unlock_cache().get(drama_id)
            if rec and not rec.get("failed") and time.time() - rec.get("at", 0) < UNLOCK_CACHE_TTL:
                return jsonify({**rec, "ok": True, "running": False,
                                "phase": "done", "cached": True})
        c = client()          # build it here: the worker thread has no request ctx
        if not wait:
            # the drama list already knows the chapter count: report it right away
            try:
                n = len(c.chapters(drama_id) or [])
            except Exception:  # noqa: BLE001
                n = None
        if wait:
            res = sweep_unlock(drama_id, c=c)
            res.pop("ok", None)
            res["at"] = time.time()
            cache = _unlock_cache()
            cache[drama_id] = res
            _unlock_cache_save(cache)
            return jsonify({**res, "ok": True, "running": False,
                            "phase": "done", "cached": False})
        _start_unlock_job(drama_id, c, total=n)
        return jsonify({"ok": True, **_unlock_state(drama_id)})
    except Exception as e:  # noqa: BLE001
        return err(e)


@app.get("/api/unlock_status/<drama_id>")
def unlock_status(drama_id):
    """Poll the unlock job of one drama (same payload as /api/unlock_all)."""
    return jsonify({"ok": True, **_unlock_state(drama_id)})


@app.get("/api/tasks")
def tasks():
    try:
        return jsonify({"ok": True, "tasks": client().task_list()})
    except Exception as e:  # noqa: BLE001
        return err(e)


@app.post("/api/tasks/farm")
def farm():
    try:
        return jsonify({"ok": True, **client().farm_tasks()})
    except Exception as e:  # noqa: BLE001
        return err(e)


@app.get("/proxy/hls")
def proxy_hls():
    """Fetch m3u8/ts/key through the server; rewrite playlists so segments also
    come back through here (the CDN URLs are signed and IP/time bound)."""
    u = request.args.get("u")
    if not u:
        return jsonify({"ok": False, "msg": "missing u"}), 400
    host = urlparse(u).hostname or ""
    if not host.endswith("shortswave.com"):
        return jsonify({"ok": False, "msg": "host not allowed"}), 403
    if request.args.get("h264") in ("1", "true") and urlparse(u).path.endswith(".m3u8"):
        return _h264_playlist(u, request.args.get("ch") or "")
    if urlparse(u).path.endswith(".m3u8") and not request.args.get("h264"):
        # The signed play URL (and the auth_key inside it) expires after ~6 h and
        # the player keeps the old one in its <video> element. Never trust it for
        # a playlist: always ask the API for a fresh URL for this chapter.
        cid, did = _episode_ids_from_url(u)
        fresh = _fresh_play_url(did, cid or request.args.get("ch") or "",
                                request.args.get("lang", DEFAULT_LANG))
        if fresh:
            u = fresh
    try:
        headers = {"User-Agent": "ShortsWave/2.68.00 (Android;13;samsung;SM-G991B;)"}
        rng = request.headers.get("Range")
        if rng:
            headers["Range"] = rng
        up = requests.get(u, headers=headers, timeout=30, stream=True)
    except Exception as e:  # noqa: BLE001
        return err(e, 502)

    ctype = up.headers.get("Content-Type", "application/octet-stream")
    path = urlparse(u).path.lower()
    if path.endswith(".ts"):
        ctype = "video/mp2t"
    elif path.endswith(".m4s") or path.endswith(".mp4"):
        ctype = "video/mp4"
    elif path.endswith(".aac"):
        ctype = "audio/aac"
    elif path.endswith(".key"):
        ctype = "application/octet-stream"
    passthrough = {k: up.headers[k] for k in
                   ("Content-Range", "Accept-Ranges", "Content-Length") if k in up.headers}

    is_playlist = ("mpegurl" in ctype.lower()) or urlparse(u).path.endswith(".m3u8")
    if is_playlist:
        text = up.content.decode("utf-8", "replace")
        if "#EXTM3U" not in text:
            # an error page (stale/revoked auth_key, 404) is NOT a manifest: pass
            # the status through so the player can report a real error
            return Response(up.content, status=up.status_code,
                            headers={"Content-Type": ctype,
                                     "Access-Control-Allow-Origin": "*",
                                     "Cache-Control": "no-store"})
        body = rewrite_manifest(text, u).encode()
        return Response(body, status=200,
                        headers={"Content-Type": "application/vnd.apple.mpegurl",
                                 "Access-Control-Allow-Origin": "*",
                                 "X-HLS-Origin": u,
                                 # the manifest is short-lived (signed URLs inside);
                                 # the switch to the H.264 rendition must not be
                                 # served from a stale browser cache
                                 "Cache-Control": "no-store"})
    return Response(up.iter_content(chunk_size=65536), status=up.status_code,
                    headers={"Content-Type": ctype, "Access-Control-Allow-Origin": "*", **passthrough})


@app.get("/proxy/sub")
def proxy_sub():
    """Serve WebVTT subtitle files. Upstream answers with
    application/octet-stream, which <track> may refuse -- force text/vtt."""
    u = request.args.get("u")
    if not u:
        return jsonify({"ok": False, "msg": "missing u"}), 400
    host = urlparse(u).hostname or ""
    if not host.endswith("shortswave.com"):
        return jsonify({"ok": False, "msg": "host not allowed"}), 403
    try:
        up = requests.get(u, headers={"User-Agent": "ShortsWave/2.68.00 (Android;13;samsung;SM-G991B;)"},
                          timeout=20)
    except Exception as e:  # noqa: BLE001
        return err(e, 502)
    if up.status_code != 200:
        return jsonify({"ok": False, "msg": f"upstream {up.status_code}"}), up.status_code
    body = up.content
    if not body.lstrip().startswith(b"WEBVTT"):
        body = b"WEBVTT\n\n" + body
    return Response(body, status=200, headers={
        "Content-Type": "text/vtt; charset=utf-8",
        "Cache-Control": "public, max-age=3600",
        "Access-Control-Allow-Origin": "*",
    })


# ---------------------------------------------------------------- H.264 HLS
def _episode_ids_from_url(u):
    """Recover (chapter_id, drama_id) from a signed play URL
    /v1/play/<chapter_id>/v.m3u8?auth_key=...&drama_id=... (drama id is often
    absent, in which case we look it up in the local chapter table)."""
    m = re.search(r"/play/([0-9a-zA-Z]+)/", urlparse(u).path)
    if not m:
        return None, None
    chapter_id = m.group(1)
    q = requests.utils.unquote(u)
    dm = re.search(r"(?:drama_id|dramaId)=([0-9a-zA-Z]+)", q)
    drama_id = dm.group(1) if dm else None
    if not drama_id:
        try:
            conn = sqlite3.connect(DB_PATH)
            row = conn.execute("SELECT drama_id FROM chapter WHERE chapter_id=?",
                               (chapter_id,)).fetchone()
            conn.close()
            drama_id = row[0] if row else None
        except sqlite3.Error:
            drama_id = None
    return chapter_id, drama_id


def _drama_id_for_chapter(chapter_id):
    """Local chapter table is only populated by /api/drama and the sweep; the
    signed play URL carries no drama_id either in most cases, so fall back to
    the saved sweep output before giving up (without a drama id no fresh signed
    URL can be minted and the transcode has nothing to read)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute("SELECT drama_id FROM chapter WHERE chapter_id=?",
                           (chapter_id,)).fetchone()
        conn.close()
        if row and row[0]:
            return row[0]
    except sqlite3.Error:
        pass
    did = (_chapter_owner_from_sweep(chapter_id) or "")
    return did or None


def _chapter_owner_from_sweep(chapter_id):
    """work/sweep/titles.csv column 8 is the drama's first_chapter_id: enough to
    map an episode back to its drama when the local table has no row yet."""
    path = os.path.join(BASE_DIR, "sweep", "titles.csv")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            next(f, None)                                   # header
            for line in f:
                parts = line.split(",")
                if len(parts) > 7 and parts[7].strip() == chapter_id:
                    return parts[2].strip() or None
    except OSError:
        return None
    return None


def _fresh_play_url(drama_id, chapter_id, lang):
    """The signed URL expires after 6 h; ask the API for a new one when the one
    the browser sent is stale (or when it sent none)."""
    if not (drama_id and chapter_id):
        return None
    for d in (client().chapter_detail(drama_id, chapter_id) or {},
              _stored_detail(drama_id, chapter_id)):
        pu = d.get("play_url") or ""
        url = absolute(pu) if pu else ""
        if url:
            return url
    return None


def _stored_detail(drama_id, chapter_id):
    """Everything the API said about this chapter on a previous pass (the
    chapters opened by a rotated account only answer on that account). Lets
    /proxy/hls mint a correct URL when the default client answers empty."""
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute("SELECT raw FROM chapter WHERE chapter_id=? AND drama_id=?",
                           (chapter_id, drama_id)).fetchone()
        conn.close()
        if row and row[0]:
            return json.loads(row[0]) or {}
    except (sqlite3.Error, ValueError):
        pass
    return {}


def _h264_base(chapter_id):
    return f"/proxy/h264?ch={requests.utils.quote(chapter_id, safe='')}"


def _h264_playlist(u, chapter_id):
    """?h264=1 on an upstream playlist -> the transcoded one (starting ffmpeg)."""
    if not H264_ENABLED:
        return jsonify({"ok": False, "msg": "H.264 transcoding disabled (SW_H264=0)"}), 503
    cid, did = _episode_ids_from_url(u)
    cid = cid or chapter_id
    if not cid:
        return jsonify({"ok": False, "msg": "cannot tell which chapter this is"}), 400
    did = did or _drama_id_for_chapter(cid)
    base = _h264_base(cid)

    def manifest(state):
        if not state["ready"]:
            # nothing encodable yet: give the player an empty live playlist so it
            # keeps polling instead of erroring out
            return Response("#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-TARGETDURATION:4\n"
                            "#EXT-X-MEDIA-SEQUENCE:0\n#EXT-X-PLAYLIST-TYPE:EVENT\n"
                            "#EXT-X-MAP:URI=\"" + base + "/init.mp4\"\n",
                            status=200, headers={"Content-Type": "application/vnd.apple.mpegurl",
                                                 "Cache-Control": "no-store",
                                                 "Access-Control-Allow-Origin": "*"})
        pl = transcode.playlist_path(cid)
        if not os.path.exists(pl):
            pl = transcode.live_playlist_path(cid)
        with open(pl, encoding="utf-8") as f:
            text = f.read()
        text = re.sub(r"^(init\.mp4|seg\d+\.(?:m4s|ts))\s*$",
                      lambda m: f'{base}&f={m.group(1)}', text, flags=re.M)
        text = transcode.inline_init_map(text, base)
        # no-store: the playlist grows while ffmpeg runs, the player polls it
        return Response(text, status=200,
                        headers={"Content-Type": "application/vnd.apple.mpegurl",
                                 "Cache-Control": "no-store",
                                 "Access-Control-Allow-Origin": "*"})

    state = transcode.status(cid)
    finished = state["done"] or (state["ok"] is False and state["ready"])
    if not finished and not state["running"]:
        master = _fresh_play_url(did, cid, request.args.get("lang", DEFAULT_LANG)) or u
        transcode.start(cid, master,
                        vf=f"scale=-2:{H264_HEIGHT}", crf=H264_CRF, preset=H264_PRESET)
        state = transcode.status(cid)
    return manifest(state)


@app.get("/proxy/h264")
def proxy_h264():
    """Serve (and lazily start) the H.264 rendition of one episode."""
    if not H264_ENABLED:
        return jsonify({"ok": False, "msg": "H.264 transcoding disabled (SW_H264=0)"}), 503
    cid = request.args.get("ch") or ""
    name = request.args.get("f") or "index.m3u8"
    if not re.fullmatch(r"init\.mp4|seg\d{5}\.(?:m4s|ts)|index\.m3u8", name):
        return jsonify({"ok": False, "msg": "bad file"}), 400
    d = transcode.cache_dir(cid)
    if name == "index.m3u8":
        return _h264_playlist("", cid)
    path = os.path.join(d, name)
    if not os.path.exists(path):
        if name == "init.mp4":
            # only TS segments are produced; the player never asks for this
            return jsonify({"ok": False, "msg": "not used (TS rendition)"}), 404
        return jsonify({"ok": False, "msg": "segment not ready"}), 404
    if name.endswith(".ts"):
        ctype = "video/mp2t"
    elif name.endswith(".m4s") or name.endswith(".mp4"):
        ctype = "video/mp4"
    else:
        ctype = "application/vnd.apple.mpegurl"
    resp = send_from_directory(d, name, mimetype=ctype, conditional=True)
    resp.headers["Cache-Control"] = "public, max-age=86400"
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.get("/api/h264/status")
def h264_status():
    cid = request.args.get("ch") or ""
    return jsonify({"ok": True, "enabled": H264_ENABLED, "chapter_id": cid,
                    **{k: v for k, v in transcode.status(cid).items() if k != "dir"}})


def _spa_index():
    """Serve the built SPA shell (deep links like /d/<slug>-<id> need it)."""
    if app.static_folder and os.path.exists(os.path.join(app.static_folder, "index.html")):
        return send_from_directory(app.static_folder, "index.html")
    return None


@app.get("/")
def index():
    page = _spa_index()
    if page is not None:
        return page
    return jsonify({"ok": True, "msg": "ShortsWave API. UI not built (see ui/).",
                    "endpoints": ["/api/health", "/api/login", "/api/feed", "/api/drama/<id>",
                                  "/api/chapter/<drama>/<chapter>", "/api/unlock", "/api/tasks",
                                  "/api/tasks/farm", "/proxy/hls?u="]})


@app.get("/d/<path:slug>")
def drama_deeplink(slug):
    """Pretty drama URL: /d/<title-slug>-<drama_id>.

    The client-side router owns this route; the server only has to hand back the
    SPA shell for any slug (200), so a shared/refreshed link works. An old or
    wrong slug is canonicalised in the browser, not here.
    """
    page = _spa_index()
    if page is None:
        return jsonify({"ok": False, "msg": "UI not built (see work/ui)"}), 404
    resp = page
    # deep links must not be cached as "/" — each slug is its own document
    resp.headers["Cache-Control"] = "no-cache"
    return resp


def _h264_sweeper():
    """Drop transcodes nobody watched for SW_H264_CACHE_DAYS (default 7 days)."""
    while True:
        try:
            n = transcode.sweep(H264_CACHE_DAYS * 86400)
            if n:
                app.logger.info("h264 cache: removed %d stale episode(s)", n)
        except Exception:  # noqa: BLE001 — a sweeper must never kill the server
            pass
        time.sleep(3600)


if H264_ENABLED and H264_CACHE_DAYS > 0:
    threading.Thread(target=_h264_sweeper, daemon=True, name="h264-sweeper").start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False, threaded=True)
