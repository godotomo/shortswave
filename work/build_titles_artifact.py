#!/usr/bin/env python3
"""Merge a sweep run into one consolidated titles list.

Input  (produced by sweep_titles.py):
    <sweep>/titles.json   harvest records (per_language rows + distinct dramas)
    <sweep>/health.csv    stage-2 probe: chapter counts, free episodes, playability

Output (written to <sweep>, and title_index.json is also copied to work/sweep/
because server.py::catalogue_titles() reads that path):
    titles.tsv            one row per drama_id, tab separated (sorted by title)
    titles_all.csv        same data, CSV (kept for spreadsheets)
    title_index.json      {dramas: {id: {t: {lang: title}, c: cover}}}  (server format)
    slug_index.json       {dramas: {id: {slug, t, c, n}}}  canonical /d/<slug>-<id>
    index.md              human report: totals, per-language counts, top tags
    summary.json          counters used by the report

Usage:
    python3 build_titles_artifact.py                 # reads work/sweep4
    python3 build_titles_artifact.py --sweep sweep4 --install
"""
import argparse
import csv
import json
import os
import re
import time
import unicodedata
from collections import Counter, OrderedDict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)

COLS = ["drama_id", "title", "slug", "primary_lang", "languages", "chapters",
        "chapter_count", "free_chapters", "charge_chapters", "unlock_type",
        "playable", "play_kind", "subtitle_count", "subtitle_langs", "tags",
        "cover", "sources", "categories", "ranks"]


def slugify(text):
    """Same shape as the web UI's /d/<slug>-<id> canonicaliser: lowercase ASCII,
    non-alphanumerics collapsed to '-', trimmed, max 60 chars."""
    t = unicodedata.normalize("NFKD", text or "")
    t = t.encode("ascii", "ignore").decode("ascii").lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t[:60].strip("-")


def load_rows(sweep_dir):
    """drama_id -> merged record from titles.json + health.csv."""
    harvest = os.path.join(sweep_dir, "titles.json")
    if not os.path.exists(harvest):
        harvest = os.path.join(sweep_dir, "_prev", "titles.json")   # stage-2 rewrite
    cat = json.load(open(harvest, encoding="utf-8"))
    merged = OrderedDict()

    for lang, rows in (cat.get("per_language") or {}).items():
        for r in rows:
            did = r.get("drama_id")
            if not did:
                continue
            m = merged.setdefault(did, {"drama_id": did, "titles": {}, "langs": [],
                                        "sources": set(), "categories": set(), "ranks": set(),
                                        "chapters": None, "charge_chapters": None,
                                        "unlock_type": None, "cover": None, "tags": None})
            if did not in merged or lang not in m["langs"]:
                m["langs"].append(lang)
            if r.get("title") and not m["titles"].get(lang):
                m["titles"][lang] = r["title"]
            for k in ("sources", "categories", "ranks"):
                m[k].update(s for s in (r.get(k) or "").split("|") if s)
            for k in ("chapters", "charge_chapters", "unlock_type", "cover"):
                if r.get(k) and not m.get(k):
                    m[k] = r[k]

    health_path = os.path.join(sweep_dir, "health.csv")
    if os.path.exists(health_path):
        with open(health_path, encoding="utf-8") as f:
            for h in csv.DictReader(f):
                did = h["drama_id"]
                m = merged.setdefault(did, {"drama_id": did, "titles": {}, "langs": [],
                                            "sources": set(), "categories": set(),
                                            "ranks": set(), "chapters": None,
                                            "charge_chapters": None, "unlock_type": None,
                                            "cover": None, "tags": None})
                lang = h.get("lang") or "en"
                if lang not in m["langs"]:
                    m["langs"].append(lang)
                if h.get("title") and not m["titles"].get(lang):
                    m["titles"][lang] = h["title"]
                m["health"] = h
                for k in ("chapters", "charge_chapters", "unlock_type", "cover"):
                    if h.get(k) and not m.get(k):
                        m[k] = h[k]
                if h.get("tags"):
                    raw_tags = h["tags"]
                    try:                                   # csv holds a python list repr
                        import ast
                        parsed = ast.literal_eval(raw_tags)
                        m["tags"] = "|".join(str(t).strip() for t in parsed if str(t).strip())
                    except (ValueError, SyntaxError):
                        m["tags"] = raw_tags.strip("[]").replace("'", "")
    return merged


def build(sweep_dir, install):
    merged = load_rows(sweep_dir)
    rows = []
    for did, m in merged.items():
        h = m.get("health") or {}
        titles = {k: v for k, v in m["titles"].items() if v}
        # primary title: prefer the record's own language, then English
        primary_lang = m["langs"][0] if m["langs"] else ""
        title = titles.get(primary_lang) or titles.get("en") or next(iter(titles.values()), "")
        title = (title or "").strip()
        rows.append({
            "drama_id": did,
            "title": title,
            "slug": "%s-%s" % (slugify(title) or "drama", did),
            "primary_lang": primary_lang,
            "languages": "|".join(m["langs"]),
            "chapters": m.get("chapters") or h.get("chapters") or "",
            "chapter_count": h.get("chapter_count") or "",
            "free_chapters": h.get("free_chapters") or "",
            "charge_chapters": m.get("charge_chapters") or "",
            "unlock_type": m.get("unlock_type") or "",
            "playable": h.get("playable") or "",
            "play_kind": h.get("play_kind") or "",
            "subtitle_count": h.get("subtitle_count") or "",
            "subtitle_langs": h.get("subtitle_langs") or "",
            "tags": m.get("tags") or "",
            "cover": m.get("cover") or "",
            "sources": "|".join(sorted(m["sources"])),
            "categories": "|".join(sorted(m["categories"])),
            "ranks": "|".join(sorted(m["ranks"])),
        })

    rows.sort(key=lambda r: ((r["title"] or "~").lower(), r["drama_id"]))

    # ---------------------------------------------------------------- write
    tsv = os.path.join(sweep_dir, "titles.tsv")
    with open(tsv, "w", encoding="utf-8", newline="") as f:
        f.write("\t".join(COLS) + "\n")
        for r in rows:
            f.write("\t".join(str(r.get(c, "")).replace("\t", " ").replace("\n", " ")
                              for c in COLS) + "\n")

    csv_path = os.path.join(sweep_dir, "titles_all.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    index = {r["drama_id"]: {"t": {k: v for k, v in (merged[r["drama_id"]]["titles"]).items() if v},
                             "c": r["cover"]}
             for r in rows}
    index_path = os.path.join(sweep_dir, "title_index.json")
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump({"generated_at": int(time.time()), "count": len(index), "dramas": index},
                  f, ensure_ascii=False)

    slug_path = os.path.join(sweep_dir, "slug_index.json")
    with open(slug_path, "w", encoding="utf-8") as f:
        json.dump({"generated_at": int(time.time()),
                   "dramas": {r["drama_id"]: {"slug": r["slug"], "t": r["title"],
                                              "c": r["cover"], "n": r["chapters"]}
                              for r in rows}}, f, ensure_ascii=False)

    installed = ""
    if install:
        dst = os.path.join(BASE_DIR, "sweep", "title_index.json")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"generated_at": int(time.time()), "count": len(index),
                       "dramas": index}, f, ensure_ascii=False)
        os.replace(tmp, dst)
        installed = dst

    # ------------------------------------------------------------- report
    by_lang = Counter()
    for r in rows:
        for l in r["languages"].split("|"):
            if l:
                by_lang[l] += 1
    tag_counter = Counter(t for r in rows for t in r["tags"].split("|") if t)
    playable = sum(1 for r in rows if r["playable"] == "True")
    no_free = sum(1 for r in rows if r["free_chapters"] in ("", "0"))
    total_ch = sum(int(r["chapter_count"]) for r in rows if str(r["chapter_count"]).isdigit())
    dup = [t for t, n in Counter(r["title"].lower() for r in rows).items() if n > 1 and t]

    summary = {
        "generated_at": int(time.time()),
        "sweep": os.path.relpath(sweep_dir, PROJECT_DIR),
        "dramas": len(rows),
        "with_title": sum(1 for r in rows if r["title"]),
        "playable": playable,
        "no_free_episode": no_free,
        "episodes_total": total_ch,
        "per_language": dict(by_lang.most_common()),
        "duplicate_title_texts": len(dup),
        "top_tags": dict(tag_counter.most_common(25)),
        "outputs": [os.path.basename(p) for p in (tsv, csv_path, index_path, slug_path)],
        "installed_index": installed,
    }
    with open(os.path.join(sweep_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)

    lines = ["# ShortsWave catalogue — %d dramas" % len(rows), "",
             "Generated %s from `%s`." % (time.strftime("%Y-%m-%d %H:%M"),
                                          summary["sweep"]), "",
             "| metric | value |", "| --- | --- |",
             "| dramas | %d |" % len(rows),
             "| with title | %d |" % summary["with_title"],
             "| playable (first ep) | %d |" % playable,
             "| episodes total | %d |" % total_ch,
             "| no free episode | %d |" % no_free,
             "| duplicate title texts | %d |" % len(dup), "",
             "## per language", "", "| lang | dramas |", "| --- | --- |"]
    lines += ["| %s | %d |" % (l, n) for l, n in by_lang.most_common()]
    lines += ["", "## top tags", "", "| tag | dramas |", "| --- | --- |"]
    lines += ["| %s | %d |" % (t, n) for t, n in tag_counter.most_common(25)]
    lines += ["", "## files", "",
              "- `titles.tsv` — one row per drama (tab separated, sorted by title)",
              "- `titles_all.csv` — same columns as CSV",
              "- `title_index.json` — `{dramas: {id: {t: {lang: title}, c: cover}}}`",
              "- `slug_index.json` — `{dramas: {id: {slug, t, c, n}}}` for `/d/<slug>`", ""]
    with open(os.path.join(sweep_dir, "index.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sweep", default="sweep4",
                    help="sweep directory name under work/ (default: sweep4)")
    ap.add_argument("--install", action="store_true",
                    help="also copy title_index.json to work/sweep/ (what server.py reads)")
    args = ap.parse_args(argv)
    if os.path.isabs(args.sweep):
        sweep_dir = args.sweep
    elif os.sep in args.sweep:                       # work/sweep4 given explicitly
        sweep_dir = os.path.join(os.path.dirname(BASE_DIR), args.sweep)
    else:
        sweep_dir = os.path.join(BASE_DIR, args.sweep)
    s = build(sweep_dir, args.install)
    print("dramas           : %d (%d with title)" % (s["dramas"], s["with_title"]))
    print("playable         : %d" % s["playable"])
    print("episodes         : %d" % s["episodes_total"])
    print("per language     : %s" % ", ".join("%s=%d" % kv for kv in s["per_language"].items()))
    print("output           : %s" % sweep_dir)
    if s["installed_index"]:
        print("installed index  : %s" % s["installed_index"])
    return s


if __name__ == "__main__":
    main()
