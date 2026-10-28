"""H.264 HLS transcoder + disk cache for browsers without a HEVC decoder.

Upstream serves every episode as a single-bitrate HLS of HEVC/H.265 MPEG-TS
segments (720x1280 + AAC). Chrome/Firefox/WebView cannot decode HEVC, so they
play the audio and keep a black frame. There is no alternate rendition upstream
(no master playlist, no 480p/h264 path: everything but the signed 720p URL
answers 403), so the only fix is transcoding on this machine.

Design
------
* One ffmpeg run per episode, writing a normal VOD HLS playlist + fMP4 segments
  into work/.cache/h264/<chapter_id>/.
* `-hls_playlist_type event` while running: the playlist is rewritten as
  segments land, so the browser can start playing while the tail is still
  encoding (`-hls_init_time` 2 s makes the very first segment short).
  Do NOT add `append_list`: with it ffmpeg writes the segment list once and
  later just appends bytes -> the player stalls after the first chunk.
* The signed `play_url` (auth_key, 6 h TTL) expires long before a warm cache
  does, so the signed master is fetched once and the absolute segment URLs are
  written to a local playlist; ffmpeg reads that with
  `-protocol_whitelist file,http,https,tcp,tls,crypto`.
* A lock per chapter means N concurrent requests share one ffmpeg process
  (the first one waits, the rest tail the growing playlist).
"""
import json
import os
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, ".cache", "h264")
UA = "ShortsWave/2.68.00 (Android;13;samsung;SM-G991B;)"

_jobs = {}
_jobs_lock = threading.Lock()
CHUNK_SECONDS = 4


def cache_dir(chapter_id):
    return os.path.join(CACHE_DIR, re.sub(r"[^0-9a-zA-Z_-]", "", chapter_id or ""))


def playlist_path(chapter_id):
    return os.path.join(cache_dir(chapter_id), "index.m3u8")


def live_playlist_path(chapter_id):
    """ffmpeg writes here while running; server.py serves whichever exists."""
    return os.path.join(cache_dir(chapter_id), "live.m3u8")


def inline_init_map(text, base):
    """Carry the fMP4 init segment inside the FIRST media segment.

    `#EXT-X-MAP:URI="init.mp4"` is 404 for players that resolve the URI against
    the page/document instead of the playlist, which aborts playback. So the
    init (ftyp+moov) is merged in front of seg00000 (see
    `_merge_init_into_first_segment`) and the tag is dropped."""
    if "#EXT-X-MAP" not in text:
        return text
    m = re.search(r"/proxy/h264\?ch=([^&\"]+)", base)
    if m:
        _merge_init_into_first_segment(cache_dir(urllib.parse.unquote(m.group(1))))
    return re.sub(r"^#EXT-X-MAP:[^\n]*\n", "", text, flags=re.M)


def _merge_init_into_first_segment(d):
    """init.mp4 (ftyp+moov) ++ seg00000.m4s (moof+mdat) -> merged first segment."""
    init = os.path.join(d, "init.mp4")
    first = os.path.join(d, "seg00000.m4s")
    if not (os.path.exists(init) and os.path.exists(first)):
        return None
    out = first
    tmp = os.path.join(d, "seg00000.merged.tmp")
    with open(tmp, "wb") as w:
        with open(init, "rb") as f:
            w.write(f.read())
        with open(first, "rb") as f:
            w.write(f.read())
    os.replace(tmp, out)
    return out


def _fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _write_source_playlist(chapter_id, master_url):
    """Signed master -> local playlist of absolute segment URLs (no proxying)."""
    text = _fetch(master_url)
    if "#EXT-X-STREAM-INF" in text:          # master: follow the first variant
        variants = [l.strip() for l in text.splitlines() if l and not l.startswith("#")]
        if variants:
            text = _fetch(urllib.parse.urljoin(master_url, variants[0]))
    out = [l for l in text.splitlines() if l.strip()]
    src = os.path.join(cache_dir(chapter_id), "source.m3u8")
    with open(src, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    return src


def _run(chapter_id, master_url, vf, crf, preset):
    d = cache_dir(chapter_id)
    os.makedirs(d, exist_ok=True)
    src = _write_source_playlist(chapter_id, master_url)
    state = os.path.join(d, "state.json")
    cmd = [
        "ffmpeg", "-hide_banner", "-v", "error", "-y",
        "-protocol_whitelist", "file,http,https,tcp,tls,crypto",
        "-i", src,
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-profile:v", "main", "-pix_fmt", "yuv420p",
        "-vf", vf,
        "-force_key_frames", f"expr:gte(t,n_forced*{CHUNK_SECONDS})",
        "-c:a", "aac", "-b:a", "96k", "-ac", "2",
        "-f", "hls", "-hls_time", str(CHUNK_SECONDS),
        # first chunk is cut after ~1 s, so playback starts ~2 s after the job
        # starts instead of after a full 4 s segment has been encoded
        "-hls_init_time", "1",
        # Plain (re-written) playlist, NO append_list: append_list records the
        # segment list once and then appends segments to a playlist that never
        # gets the new EXTINF lines -> player stalls after the first chunk.
        "-hls_playlist_type", "event",
        "-hls_flags", "independent_segments+temp_file",
        # mpegts, not fmp4: a TS segment is self-contained (PAT/PMT inline), so
        # players that mis-resolve EXT-X-MAP keep working, and browsers that can
        # demux TS still get H.264.
        "-hls_segment_type", "mpegts",
        "-hls_segment_filename", os.path.join(d, "seg%05d.ts"),
        "-progress", os.path.join(d, "progress.txt"), "-nostats",
        live_playlist_path(chapter_id),
    ]
    with open(os.path.join(d, "ffmpeg.log"), "wb") as log:
        rc = subprocess.call(cmd, stdout=log, stderr=log)
    ok = rc == 0 and os.path.exists(live_playlist_path(chapter_id))
    if ok:
        # the event playlist already carries ENDLIST once ffmpeg exits
        os.replace(live_playlist_path(chapter_id), playlist_path(chapter_id))
    with open(state, "w") as f:
        json.dump({"ok": ok, "rc": rc, "finished": time.time()}, f)
    return ok


def start(chapter_id, master_url, vf="scale=-2:480", crf=26, preset="ultrafast"):
    """Kick off (or join) a transcode job. Returns the job dict."""
    with _jobs_lock:
        job = _jobs.get(chapter_id)
        if job and not job["done"]:
            return job
        if job and job.get("ok"):          # succeeded: nothing to redo
            return job
        # failed / never started -> fall through and retry with a fresh job
        if job:
            _jobs.pop(chapter_id, None)

        def worker():
            try:
                ok = _run(chapter_id, master_url, vf, crf, preset)
            except Exception as e:  # noqa: BLE001 — never kill the server thread
                ok = False
                with open(os.path.join(cache_dir(chapter_id), "state.json"), "w") as f:
                    json.dump({"ok": False, "error": str(e)}, f)
            with _jobs_lock:
                _jobs[chapter_id]["done"] = True
                _jobs[chapter_id]["ok"] = ok
                _jobs[chapter_id]["ended"] = time.time()

        t = threading.Thread(target=worker, daemon=True, name=f"hls264-{chapter_id[:8]}")
        job = {"started": time.time(), "done": False, "ok": None, "proc": t}
        _jobs[chapter_id] = job
        t.start()
        return job


def status(chapter_id):
    d = cache_dir(chapter_id)
    pl = playlist_path(chapter_id)
    live = live_playlist_path(chapter_id)
    if not os.path.exists(pl) and os.path.exists(live):
        pl = live
    ready = os.path.exists(pl)
    finished = False
    segs = 0
    if ready:
        with open(pl, encoding="utf-8") as f:
            text = f.read()
        finished = "#EXT-X-ENDLIST" in text
        segs = len(re.findall(r"\.(?:m4s|ts)", text))
        # A playlist left behind without ENDLIST is only usable while ffmpeg is
        # still writing it. Once nothing is running it will never grow: either
        # the job died or the cache came from an older rendition (the abandoned
        # fMP4/EXT-X-MAP experiment). Report it as not-ready so the caller
        # re-encodes instead of freezing the player a few seconds in.
        job = _jobs.get(chapter_id)
        outdated = "EXT-X-MAP" in text and ".ts" in text   # abandoned rendition
        if outdated or (not finished and not (job and not job.get("done"))):
            ready = False
            segs = 0
    job = _jobs.get(chapter_id)
    return {"ready": ready, "done": finished, "segments": segs,
            "running": bool(job and not job.get("done")),
            "ok": (job or {}).get("ok"), "dir": d}


def sweep(max_age_seconds):
    """Delete caches older than max_age_seconds (0 disables)."""
    if not max_age_seconds:
        return 0
    cutoff = time.time() - max_age_seconds
    removed = 0
    if not os.path.isdir(CACHE_DIR):
        return 0
    for name in os.listdir(CACHE_DIR):
        p = os.path.join(CACHE_DIR, name)
        try:
            if os.path.getmtime(p) < cutoff:
                import shutil
                shutil.rmtree(p, ignore_errors=True)
                removed += 1
                with _jobs_lock:
                    _jobs.pop(name, None)   # a swept chapter may be re-encoded
        except OSError:
            pass
    return removed
