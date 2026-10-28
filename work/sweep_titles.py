#!/usr/bin/env python3
"""Full catalogue sweep for ShortsWave.

Stage 1 (harvest)  — walk every catalogue surface for every language and collect
                     every distinct drama title:
                       * /v3/all   and /v3/more   (paged until a page repeats / is empty)
                       * every category tag from /v3/drama/bank/channel/list, deep-paged
                       * every block of /v3/discover/rank/all
                       * /v3/top_unlock_list, /v3/topn, /v3/topn_v1_r3
                       * /v3/discover/channel?channel=new|dubbed|anime (paged via is_more)
                       * /v3/special/topic/all + /v3/special/topic/detail?index=<topic>
                       * /v3/vip_only_drama_list (ids only, no titles)
                       * /v1/search/hotrecomm  (bonus: hot words are often titles)
                     Measured 2026-10-28 on `en`: /v3/all 502s at page 76, /v3/more
                     stops at ~page 51 but its order is session-randomised (a re-run
                     yields a different slice), so both are always paged from 1 and
                     the surfaces above add ~150 ids the old sweep missed.
Stage 2 (health)   — for every distinct drama_id: chapter list (count / free count),
                     first free chapter detail (play_url present? subtitle languages).

Output (default work/sweep/):
    titles.json   full records
    titles.csv    one row per (drama_id, lang) — catalogue surface metadata
    health.csv    one row per drama_id — chapter/subtitle/playability probe
    summary.json  counters + timing

The sweep is read-only and rate limited: it never calls /api/unlock, never submits tasks.

Usage:
    python3 sweep_titles.py                     # all 13 languages, stage 1 + stage 2
    python3 sweep_titles.py --langs en,es       # subset
    python3 sweep_titles.py --no-health         # harvest only (fast)
    python3 sweep_titles.py --health-limit 200  # probe only N dramas
    python3 sweep_titles.py --out work/sweep --workers 4 --sleep 0.15
"""
import argparse
import concurrent.futures as futures
import csv
import json
import os
import random
import sys
import threading
import time
from collections import Counter, OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sw_client import Client, ShortsWaveError  # noqa: E402

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)

# code -> region (must match work/server.py::LANGUAGES)
LANGUAGES = OrderedDict([
    ("en", "US"), ("es", "ES"), ("pt", "BR"), ("id", "ID"), ("th", "TH"),
    ("ja", "JP"), ("ko", "KR"), ("ar", "SA"), ("fr", "FR"), ("de", "DE"),
    ("ru", "RU"), ("tr", "TR"), ("ms", "MY"),
])

FEED_PATHS = ("/v3/all", "/v3/more")
MAX_PAGES = 120          # /v3/all 502s at ~page 76, /v3/more ends at ~page 51; cap defensively
MAX_TAG_PAGES = 6        # a category tag never exceeded 8 pages on `en` and only
                         # added 427 of 2561 en ids; deep tag paging is the slowest
                         # surface (~0.4 s per page), so the cap stays small
DISPLAY_CHANNELS = ("new", "dubbed", "anime")   # from /v3/discover/display/channel
sleep_default = 0.15

_print_lock = threading.RLock()   # reentrant: log() is called under the same lock


def log(msg):
    with _print_lock:
        print(msg, flush=True)


# --------------------------------------------------------------------- retry
def retry(fn, *, tries=4, base=0.6, label=""):
    """Call fn(); retry with exponential backoff + jitter on any error."""
    last = None
    for i in range(tries):
        try:
            return fn()
        except ShortsWaveError as e:
            # code 206 = region/date gate, code 15 = rate limited -> back off harder
            last = e
            if not getattr(e, "code", None) or e.code in (15, 16, 17, 500):
                time.sleep(base * (2 ** i) + random.uniform(0, 0.4))
            elif e.code == 206:
                raise
            else:
                time.sleep(base * (2 ** i))
        except Exception as e:  # noqa: BLE001  (network/timeout)
            last = e
            time.sleep(base * (2 ** i) + random.uniform(0, 0.4))
    log("  ! %s failed after %d tries: %s" % (label, tries, last))
    return None


# ------------------------------------------------------------------ harvest
def slim(it):
    return {
        "drama_id": it.get("drama_id") or it.get("dramaId"),
        "title": it.get("drama_title") or it.get("title"),
        "cover": it.get("drama_cover") or it.get("drama_cover_h"),
        "chapters": it.get("chapters") or it.get("chapterTotal"),
        "charge_chapters": it.get("charge_chapters"),
        "unlock_type": it.get("unlock_type") or it.get("unlockType"),
        "first_chapter_id": it.get("chapter_id") or it.get("chapterId"),
        "description": (it.get("description") or "")[:400],
        "tags": it.get("tags"),
    }


def sweep_feed(c, path, lang, sleep):
    """Walk a paged feed until it stops yielding new ids (or repeats/empties)."""
    seen_local = set()
    total = 0
    fails = 0
    for page in range(1, MAX_PAGES + 1):
        rows = retry(lambda: c.feed(page, 20, path), label="%s p%d %s" % (path, page, lang))
        if rows is None:
            # retry() exhausted its tries (/v3/all answers 502 for a page or two
            # before it really ends): give up on this feed after 3 misses.
            # An EMPTY list is not an error -- /v3/more is simply empty for some
            # languages (id, de, ms...) and that must stop the walk at once.
            fails += 1
            if fails >= 3:
                break
            time.sleep(0.8 * fails)
            continue
        fails = 0
        if not rows:
            break
        fresh = 0
        for it in rows:
            did = it.get("drama_id")
            if not did:
                continue
            total += 1
            if did not in seen_local:
                seen_local.add(did)
                fresh += 1
                yield slim(it)
        if fresh == 0:          # pagination wrapped / repeated page
            break
        time.sleep(sleep)


def _rows_of(r):
    """Normalise any list-shaped answer into a list of items."""
    if isinstance(r, list):
        return r
    if isinstance(r, dict):
        for k in ("place_list", "list", "drama_list", "items", "data"):
            if isinstance(r.get(k), list):
                return r[k]
    return []


def sweep_tag(c, tag_id, place_name, tag_name, lang, sleep):
    """One category tag, paged while the server says is_more / keeps giving rows."""
    seen = set()
    for page in range(1, MAX_TAG_PAGES + 1):
        r = retry(lambda: c._call("GET", "/v3/drama/bank/drama/list", params={
            "channel_id": 0, "tag_id": tag_id, "page": page, "page_size": 30}),
            label="tag %s/%s p%d %s" % (place_name, tag_id, page, lang))
        rows = _rows_of(r)
        ids = {it.get("drama_id") for it in rows if it.get("drama_id")}
        if not ids:
            break
        new = ids - seen
        seen |= ids
        for it in rows:
            if it.get("drama_id"):
                yield it
        more = r.get("is_more") if isinstance(r, dict) else None
        if not new or more is False or len(rows) < 30:
            break
        time.sleep(sleep)
    return


def sweep_categories(c, lang, sleep):
    """Every page of every tag of every category place (channel list)."""
    data = retry(lambda: c._call("GET", "/v3/drama/bank/channel/list"),
                 label="channel/list %s" % lang)
    places = (data or {}).get("place_list") or []
    for place in places:
        for tag in place.get("list") or []:
            tid = tag.get("id")
            if tid is None:
                continue
            yield from ((it, place.get("name"), tag.get("name"))
                        for it in sweep_tag(c, tid, place.get("name"), tag.get("name"),
                                            lang, sleep))
            time.sleep(sleep)


def sweep_simple(c, path, source, lang, sleep, params=None):
    """A non-paged surface that answers one list of drama items."""
    r = retry(lambda: c._call("GET", path, params=params), label="%s %s" % (path, lang))
    for it in _rows_of(r):
        if it.get("drama_id"):
            yield it, source


def sweep_topics(c, lang, sleep):
    """/v3/special/topic/all blocks, each deep-paged through /v3/special/topic/detail."""
    d = retry(lambda: c._call("GET", "/v3/special/topic/all"), label="topic/all %s" % lang) or {}
    for block in d.get("place_list") or []:
        idx = block.get("index")
        if idx is None:
            continue
        seen = set()
        for it in block.get("list") or []:
            if it.get("drama_id"):
                seen.add(it["drama_id"])
                yield it, "topic:" + str(idx)
        for page in range(1, MAX_TAG_PAGES + 1):
            r = retry(lambda: c._call("GET", "/v3/special/topic/detail",
                                      params={"index": idx, "page": page, "page_size": 20}),
                      label="topic/%s p%d %s" % (idx, page, lang))
            rows = _rows_of(r)
            ids = {it.get("drama_id") for it in rows if it.get("drama_id")}
            new = ids - seen
            if not new:
                break
            seen |= ids
            for it in rows:
                if it.get("drama_id"):
                    yield it, "topic:" + str(idx)
            time.sleep(sleep)


def sweep_discover_channels(c, lang, sleep):
    """/v3/discover/channel?channel=new|dubbed|anime, paged until is_more=false."""
    for ch in DISPLAY_CHANNELS:
        seen = set()
        for page in range(1, MAX_TAG_PAGES + 1):
            r = retry(lambda: c._call("GET", "/v3/discover/channel",
                                      params={"page": page, "page_size": 20, "channel": ch}),
                      label="discover/%s p%d %s" % (ch, page, lang))
            rows = _rows_of(r)
            ids = {it.get("drama_id") for it in rows if it.get("drama_id")}
            new = ids - seen
            if not ids:
                break
            seen |= ids
            for it in rows:
                if it.get("drama_id"):
                    yield it, "discover:" + ch
            more = r.get("is_more") if isinstance(r, dict) else None
            if not new or more is False:
                break
            time.sleep(sleep)


def sweep_rank(c, lang, sleep):
    data = retry(lambda: c._call("GET", "/v3/discover/rank/all"), label="rank %s" % lang)
    for block in (data or {}).get("place_list") or []:
        for it in block.get("list") or []:
            if it.get("drama_id"):
                yield slim(it), block.get("name")
        time.sleep(sleep)


def sweep_language(lang, region, out_dir, sleep, do_health, resume_ids=None):
    db = os.path.join(out_dir, "_db_%s.db" % lang)
    c = Client(db, lang=lang, region=region)
    c.ensure_login()

    cat = {}          # drama_id -> {"title", "sources", "categories", "ranks"}
    order = []
    done_surfaces = []
    resume_ids = list(resume_ids or [])

    def absorb(item, source, extra=None):
        did = item.get("drama_id")
        if not did:
            return
        rec = cat.get(did)
        if rec is None:
            rec = {"item": item, "sources": [], "categories": [], "ranks": []}
            cat[did] = rec
            order.append(did)
        if source not in rec["sources"]:
            rec["sources"].append(source)
        if source in ("category", "topic", "discover", "one_shot") and extra \
                and extra not in rec["categories"]:
            rec["categories"].append(extra)
        if source in ("rank", "topic", "discover", "one_shot") and extra \
                and extra not in rec["ranks"]:
            rec["ranks"].append(extra)

    cfg_dir = os.path.dirname(os.path.abspath(db))
    surface_dir = os.path.join(cfg_dir, "_surfaces")
    os.makedirs(surface_dir, exist_ok=True)
    surface_file = os.path.join(surface_dir, "%s.json" % lang)

    def snapshot():
        """Write the IDs harvested so far for this language.

        A language with 69 category tags takes minutes, and the global
        titles.json is only rewritten once the language finishes. This file keeps
        per-surface progress so an interrupted run still has what it found, and
        --resume can pick it straight back up.
        """
        with open(surface_file, "w", encoding="utf-8") as f:
            json.dump({"lang": lang, "region": region,
                       "done_surfaces": done_surfaces, "order": order,
                       "items": {did: cat[did]["item"] for did in order}}, f,
                      ensure_ascii=False)

    if resume_ids:
        for did in resume_ids:
            absorb({"drama_id": did}, "resumed")
        log("  %s resumed %d ids from %s" % (lang, len(resume_ids), surface_file))

    for path in FEED_PATHS:
        n0 = len(cat)
        for i, it in enumerate(sweep_feed(c, path, lang, sleep)):
            absorb(it, "feed" + path)
            if i and i % 200 == 0:      # ~10 pages: long feeds get a checkpoint too
                snapshot()
        log("  %s %-9s +%d (total %d)" % (lang, path, len(cat) - n0, len(cat)))
        done_surfaces.append("feed" + path)
        snapshot()

    n0 = len(cat)
    for it, place, tag in sweep_categories(c, lang, sleep):
        absorb(it, "category", "%s/%s" % (place, tag))
    log("  %s category  +%d (total %d)" % (lang, len(cat) - n0, len(cat)))
    done_surfaces.append("category")
    snapshot()

    n0 = len(cat)
    for it, block in sweep_rank(c, lang, sleep):
        absorb(it, "rank", block)
    log("  %s rank      +%d (total %d)" % (lang, len(cat) - n0, len(cat)))
    done_surfaces.append("rank")
    snapshot()

    n0 = len(cat)
    for it, src in sweep_topics(c, lang, sleep):
        absorb(it, "topic", src)
    log("  %s topic     +%d (total %d)" % (lang, len(cat) - n0, len(cat)))
    done_surfaces.append("topic")
    snapshot()

    n0 = len(cat)
    for it, src in sweep_discover_channels(c, lang, sleep):
        absorb(it, "discover", src)
    log("  %s discover  +%d (total %d)" % (lang, len(cat) - n0, len(cat)))
    done_surfaces.append("discover")
    snapshot()

    # small one-shot surfaces (top unlocked, personalised top-N, recommendation rank)
    one_shots = [("/v3/top_unlock_list", "top_unlock", None),
                 ("/v3/topn", "topn", None),
                 ("/v3/topn_v1_r3", "topn_v1_r3", {"exclude": "", "order_type": "1"})]
    for path, src, params in one_shots:
        n0 = len(cat)
        for it, s in sweep_simple(c, path, src, lang, sleep, params):
            absorb(it, "one_shot", s)
        log("  %s %-9s +%d (total %d)" % (lang, src, len(cat) - n0, len(cat)))
        done_surfaces.append(src)
        snapshot()

    # /v3/vip_only_drama_list only answers ids; fill titles from whatever else we
    # already saw, and leave the rest for stage 2 (chapters work for any id).
    for did in (retry(lambda: c._call("GET", "/v3/vip_only_drama_list"),
                      label="vip_only %s" % lang) or []):
        if isinstance(did, str) and did and did not in cat:
            absorb({"drama_id": did}, "vip_only")

    hot = retry(lambda: c._call("GET", "/v1/search/hotrecomm"), label="hot %s" % lang) or []
    hot_words = [h.get("word") or h.get("title") for h in hot if (h.get("word") or h.get("title"))]

    rows = []
    for did in order:
        rec = cat[did]
        it = rec["item"]
        rows.append({
            "lang": lang,
            "region": region,
            "drama_id": did,
            "title": it.get("title"),
            "chapters": it.get("chapters"),
            "charge_chapters": it.get("charge_chapters"),
            "unlock_type": it.get("unlock_type"),
            "first_chapter_id": it.get("first_chapter_id"),
            "cover": it.get("cover"),
            "description": (it.get("description") or "").replace("\n", " "),
            "sources": "|".join(rec["sources"]),
            "categories": "|".join(rec["categories"]),
            "ranks": "|".join(rec["ranks"]),
        })
    return c, rows, hot_words


# ------------------------------------------------------------------- health
def probe(c, row, sleep):
    """Chapter list + first free chapter detail for one drama."""
    did = row["drama_id"]
    out = dict(row)
    chs = retry(lambda: c.chapters(did), label="chapters %s" % did) or []
    out["chapter_count"] = len(chs)
    free = [ch for ch in chs if ch.get("is_free")]
    out["free_chapters"] = len(free)
    out["locks_ok"] = bool(chs)
    subs, playable, play_kind = [], None, ""
    target = (free or chs or [None])[0]
    if target:
        d = retry(lambda: c.chapter_detail(did, target["chapter_id"]), label="detail %s" % did) or {}
        pu = d.get("play_url") or ""
        playable = bool(pu)
        if pu:
            play_kind = "hls" if ".m3u8" in pu else ("mp4" if ".mp4" in pu else "other")
        subs = [s.get("language") for s in (d.get("sublist") or []) if s.get("language")]
        out["ep_probed"] = target.get("chapter_index")
        out["ep_is_free"] = bool(target.get("is_free"))
        # /v3/vip_only_drama_list only hands out ids: chapter/detail is the only
        # surface that will tell us a title/cover for them.
        if not out.get("title") and d.get("drama_title"):
            out["title"] = d["drama_title"]
        if not out.get("cover") and d.get("drama_cover"):
            out["cover"] = d["drama_cover"]
        if not out.get("chapters") and d.get("chapters"):
            out["chapters"] = d["chapters"]
        if not out.get("unlock_type") and d.get("unlock_type"):
            out["unlock_type"] = d["unlock_type"]
        out["title_source"] = "detail" if out.get("title") else ""
        if out.get("tags") is None and d.get("drama_tags"):
            out["tags"] = d["drama_tags"]
    out["playable"] = playable
    out["play_kind"] = play_kind
    out["subtitle_count"] = len(subs)
    out["subtitle_langs"] = "|".join(subs)
    time.sleep(sleep)
    return out


# --------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description="Sweep the whole ShortsWave drama catalogue")
    ap.add_argument("--langs", default=",".join(LANGUAGES),
                    help="comma separated language codes (default: all 13)")
    ap.add_argument("--out", default=os.path.join(BASE_DIR, "sweep"))
    ap.add_argument("--sleep", type=float, default=sleep_default)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--no-health", action="store_true", help="stage 1 only")
    ap.add_argument("--health-only", action="store_true",
                    help="reuse titles.json from an earlier run, run stage 2 only")
    ap.add_argument("--health-limit", type=int, default=0, help="probe at most N dramas")
    ap.add_argument("--append-health", action="store_true",
                    help="keep an existing health.csv and only probe the missing ids")
    ap.add_argument("--resume", action="store_true",
                    help="keep languages already present in <out>/titles.json")
    ap.add_argument("--seed-db", default="",
                    help="copy <project>/<seed-db>/_db_<lang>.db session files when missing "
                         "(e.g. work/sweep) so no new account is needed")
    args = ap.parse_args(argv)

    langs = [l.strip().lower() for l in args.langs.split(",") if l.strip()]
    unknown = [l for l in langs if l not in LANGUAGES]
    if unknown:
        ap.error("unknown language(s): %s (known: %s)" % (",".join(unknown), ",".join(LANGUAGES)))

    out_dir = args.out if os.path.isabs(args.out) else os.path.join(PROJECT_DIR, args.out)
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()

    catalog_path = os.path.join(out_dir, "titles.json")

    # ---------------------------------------------------------------- stage 1
    per_lang = {}
    hot_all = {}
    clients = {}

    if args.health_only:
        if not os.path.exists(catalog_path):
            ap.error("--health-only but %s does not exist; run the harvest first" % catalog_path)
        log("[harvest] reusing %s" % catalog_path)
        prev = json.load(open(catalog_path, encoding="utf-8"))
        per_lang = prev["per_language"]
        hot_all = prev.get("hot_search") or {}
        langs = [l for l in langs if l in per_lang] or list(per_lang)
        for lang in langs:
            c = Client(os.path.join(out_dir, "_db_%s.db" % lang),
                       lang=lang, region=LANGUAGES[lang])
            c.ensure_login()
            clients[lang] = c
    else:
        # --resume: keep whatever languages an interrupted earlier run already
        # harvested, and seed the session DBs from the previous sweep so no
        # account has to be re-created for every restart.
        if args.resume and os.path.exists(catalog_path):
            prev = json.load(open(catalog_path, encoding="utf-8"))
            per_lang = prev.get("per_language") or {}
            hot_all = prev.get("hot_search") or {}
            log("[harvest] resuming: already done %s" % ",".join(sorted(per_lang)))
        if args.seed_db:
            import shutil
            for lang in langs:
                src = os.path.join(PROJECT_DIR, args.seed_db, "_db_%s.db" % lang)
                dst = os.path.join(out_dir, "_db_%s.db" % lang)
                if os.path.exists(src) and not os.path.exists(dst):
                    shutil.copyfile(src, dst)
                    log("[harvest] seeded %s from %s" % (lang, src))
        for lang in langs:
            if args.resume and lang in per_lang:
                log("[harvest] %s already done (%d titles), skipping" % (lang, len(per_lang[lang])))
                continue
            log("[harvest] %s (%s)" % (lang, LANGUAGES[lang]))
            partial = []
            if args.resume:
                # a language interrupted halfway keeps its surface snapshot; its
                # title fields were lost though, so stage 2 re-reads them from
                # chapter/detail for any id that arrives without one
                snap = os.path.join(out_dir, "_surfaces", "%s.json" % lang)
                if os.path.exists(snap):
                    partial = (json.load(open(snap, encoding="utf-8")) or {}).get("order") or []
            c, rows, hot = sweep_language(lang, LANGUAGES[lang], out_dir, args.sleep,
                                          not args.no_health, resume_ids=partial)
            clients[lang] = c
            per_lang[lang] = rows
            hot_all[lang] = hot
            log("  %s -> %d titles" % (lang, len(rows)))
            # persist after every language so an interrupted run keeps its harvest
            with open(catalog_path, "w", encoding="utf-8") as f:
                json.dump({"generated_at": int(time.time()), "partial": True,
                           "languages": langs, "per_language": per_lang,
                           "hot_search": hot_all}, f, ensure_ascii=False, indent=1)

    # distinct dramas across all languages (same drama_id == same drama)
    distinct = OrderedDict()
    for lang, rows in per_lang.items():
        for r in rows:
            did = r["drama_id"]
            d = distinct.setdefault(did, {"drama_id": did, "langs": [], "titles": {}, "chapters": r["chapters"],
                                          "charge_chapters": r["charge_chapters"], "unlock_type": r["unlock_type"],
                                          "first_chapter_id": r["first_chapter_id"], "cover": r["cover"]})
            d["langs"].append(lang)
            d["titles"][lang] = r["title"]

    # cross-language collisions (same title text in >1 language)
    title_langs = {}
    for d in distinct.values():
        for lang, t in d["titles"].items():
            title_langs.setdefault((t or "").strip().lower(), set()).add(lang)
    dup_titles = {t: sorted(ls) for t, ls in title_langs.items() if len(ls) > 1}

    # ---------------------------------------------------------------- stage 2
    # One worker per language: a Client (and its sqlite connection) is never
    # touched by two threads at once, which is what deadlocked the first run.
    health_rows = []
    if not args.no_health:
        todo = list(distinct.values())
        if args.health_limit:
            todo = todo[: args.health_limit]
        log("[health] probing %d dramas" % len(todo))
        done = [0]

        buckets = {}
        for d in todo:
            # id-only records (vip_only) carry no language: probe them with the
            # first language we have a client for
            lang = next((l for l in d["langs"] if l in clients), None)
            if lang is None:
                lang = next(l for l in langs if l in clients)
                d["langs"].append(lang)
            buckets.setdefault(lang, []).append(d)

        # results are written incrementally so a crash never loses the probe work
        health_csv = os.path.join(out_dir, "health.csv")
        health_cols = ["drama_id", "title", "lang", "chapters", "chapter_count", "free_chapters",
                       "unlock_type", "charge_chapters", "ep_probed", "ep_is_free", "playable",
                       "play_kind", "subtitle_count", "subtitle_langs", "locks_ok",
                       "first_chapter_id", "cover", "title_source", "tags"]

        lock = threading.Lock()

        # health.csv is append-only: a re-run skips whatever is already probed
        # (a full 4750-drama pass is ~20 minutes on this phone).
        already = set()
        append = args.append_health and os.path.exists(health_csv)
        if append:
            with open(health_csv, encoding="utf-8") as f:
                already = {r["drama_id"] for r in csv.DictReader(f)}
            log("  %d dramas already probed, appending" % len(already))

        def work_one(lang, d):
            """One drama: chapter list + first-free-chapter detail."""
            c = clients[lang]
            row = {"lang": lang, "region": LANGUAGES[lang], "drama_id": d["drama_id"],
                   "title": d["titles"].get(lang)
                   or next((t for t in d["titles"].values() if t), ""),
                   "chapters": d["chapters"], "charge_chapters": d["charge_chapters"],
                   "unlock_type": d["unlock_type"], "first_chapter_id": d["first_chapter_id"],
                   "cover": d["cover"],
                   "sources": "", "categories": "", "ranks": "", "description": ""}
            out = probe(c, row, args.sleep)
            with _print_lock:
                done[0] += 1
                if done[0] % 25 == 0 or done[0] == len(todo):
                    log("  probed %d/%d (%.0fs)" % (done[0], len(todo), time.time() - t0))
            return out

        # One thread pool for the whole stage; a Client is only ever used for one
        # language at a time, and requests.Session is thread safe.
        jobs = []
        for lang, items in buckets.items():
            items = [d for d in items if d["drama_id"] not in already]
            if items:
                jobs.append((lang, items))
        left = sum(len(items) for _, items in jobs)
        log("  %d dramas left to probe (%d workers)" % (left, args.workers))

        with open(health_csv, "a" if append else "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=health_cols, extrasaction="ignore")
            if not append:
                w.writeheader()
            with futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
                futs = {}
                for lang, items in jobs:
                    for d in items:
                        futs[ex.submit(work_one, lang, d)] = d
                for fut in futures.as_completed(futs):
                    try:
                        r = fut.result()
                    except Exception as e:  # noqa: BLE001
                        import traceback
                        log("  ! probe failed: %s" % e)
                        traceback.print_exc()
                        continue
                    with lock:
                        health_rows.append(r)
                        w.writerow({k: r.get(k) for k in health_cols})
                        # titles recovered from chapter/detail (id-only records)
                        rec = distinct.get(r["drama_id"])
                        if rec is not None:
                            if r.get("title") and not rec["titles"].get(r["lang"]):
                                rec["titles"][r["lang"]] = r["title"]
                            for k in ("cover", "chapters", "unlock_type"):
                                if r.get(k) and not rec.get(k):
                                    rec[k] = r[k]
                        f.flush()

    # ----------------------------------------------------------------- write
    with open(catalog_path, "w", encoding="utf-8") as f:
        json.dump({"generated_at": int(time.time()), "languages": langs,
                   "per_language": per_lang, "distinct": list(distinct.values()),
                   "hot_search": hot_all}, f, ensure_ascii=False, indent=1)

    # Compact id -> titles index consumed by server.py (/api/drama/<id> has no
    # title of its own, but the canonical /d/<slug>-<id> URL needs one).
    index = {}
    for rec in distinct.values():
        index[rec["drama_id"]] = {"t": rec.get("titles") or {}, "c": rec.get("cover") or ""}
    with open(os.path.join(out_dir, "title_index.json"), "w", encoding="utf-8") as f:
        json.dump({"generated_at": int(time.time()), "dramas": index}, f, ensure_ascii=False)

    cat_cols = ["lang", "region", "drama_id", "title", "chapters", "charge_chapters", "unlock_type",
                "first_chapter_id", "sources", "categories", "ranks", "cover", "description"]
    with open(os.path.join(out_dir, "titles.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cat_cols, extrasaction="ignore")
        w.writeheader()
        for lang in langs:
            for r in per_lang[lang]:
                w.writerow({k: r.get(k) for k in cat_cols})

    summary = {
        "generated_at": int(time.time()),
        "seconds": round(time.time() - t0, 1),
        "languages": {l: len(per_lang[l]) for l in langs},
        "rows_total": sum(len(per_lang[l]) for l in langs),
        "distinct_dramas": len(distinct),
        "multi_language_dramas": sum(1 for d in distinct.values() if len(set(d["langs"])) > 1),
        "duplicate_title_texts": len(dup_titles),
        "hot_search_words": {l: len(v) for l, v in hot_all.items()},
    }
    if health_rows:
        summary["health"] = {
            "probed": len(health_rows),
            "with_chapters": sum(1 for r in health_rows if r.get("chapter_count")),
            "with_free_episode": sum(1 for r in health_rows if r.get("free_chapters")),
            "playable": sum(1 for r in health_rows if r.get("playable")),
            "with_subtitles": sum(1 for r in health_rows if r.get("subtitle_count")),
            "play_kinds": dict(Counter(r.get("play_kind") or "none" for r in health_rows)),
            "unlock_types": dict(Counter(r.get("unlock_type") or "?" for r in health_rows)),
            "subtitle_langs": dict(Counter(
                s for r in health_rows for s in (r.get("subtitle_langs") or "").split("|") if s)),
        }
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)

    log("")
    log("distinct dramas : %d" % summary["distinct_dramas"])
    log("catalogue rows  : %d  (%s)" % (summary["rows_total"],
                                        ", ".join("%s=%d" % (l, len(per_lang[l])) for l in langs)))
    if health_rows:
        h = summary["health"]
        log("probed          : %d  chapters=%d free=%d playable=%d subs=%d"
            % (h["probed"], h["with_chapters"], h["with_free_episode"], h["playable"], h["with_subtitles"]))
    log("output          : %s" % out_dir)
    log("done in %.1fs" % summary["seconds"])
    return summary


if __name__ == "__main__":
    main()
