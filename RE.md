# ShortsWave — Reverse Engineering Notes

**Target:** `com.shorts.wave.drama` v2.68.00 (versionCode 206800), Android.
**Source:** XAPK from APKPure (`work/shortswave.xapk`) → base APK + `umur`/density splits.
**Tooling:** `apktool d -f -o apk_full` (smali), `jadx --show-bad-code` (Java), manual probing with Python.

**Bottom line (2026-10-26, corrected):** the API is fully usable and **paid episodes CAN be
opened — they are billed from `bonus`, not `coin`**, and `bonus` is farmable from
`POST /task/v2/submit` because the server never verifies that the ad/watch happened.
A fresh anonymous account yields ~747 bonus (~15 chapters at 50) plus 10 ad-unlocks; the
balance is per account (`user_id`), the ad quota is per device+day, and both reset for a new
`device_uuid`. Details in [Unlock matrix](#unlock-matrix) and [§5](#5-task--bonus-system).

---

## 1. Surface

| Thing | Value |
|---|---|
| API base | `https://api.shortswave.com` (Cloudflare; `GET /` → `Hello World!`) |
| Events | `https://event.shortswave.com` |
| Video CDN | `https://v.shortswave.com` (Google-signed GCS URLs) |
| Images | `https://s.shortswave.com`, `https://static.shortswave.com` |
| Retrofit interface | `com.shorts.wave.drama.net.NetService` (92 endpoints) |

DNS note: `https://v.shortswave.com/v1/play/...` returns 403 — the **m3u8 is served by the API
host**, while the `.ts` segments inside it point at the CDN.

## 2. Request format

Plain JSON `{code, msg, data}`. **No request signing, no response encryption**
(`HttpResponse<T>` is a plain `{code, msg, data}` bean). `sign-code` is sent as an empty string.

Header injection happens in the OkHttp interceptor `dk/c` (auth/refresh in `dk/h`, `dk/e`):

```
app: com.shorts.wave.drama      os: 1            os_ver: 13
mobile-brand: samsung           model: SM-G991B  device-uuid: <32 hex>
version: 2.68.00                lang: en         region: US
width: 1080                     height: 2400     contype: 1
carrier: (empty)                sign-code: (empty, v2 also "")
package-id: 6                   advertising-id / attribution_id / attribution_type / afid / lip: (empty)
User-Agent: ShortsWave/2.68.00 (Android;13;samsung;SM-G991B;)
Content-Type: application/json; charset=utf-8
rule-ab: (empty)
```

Auth is a bearer-ish header `X-SESSION-TOKEN: <session_token>` added by `dk/h`.
Missing headers fail loudly: `code 1 "app is blank"`, `code 20 "not authorization"`.

### Auth flow

```
POST /login/anonymous   {device_type:"android", source_channel:"googleplay",
                         attribution_id:"", attribution_type:"", campaign:"", afid:""}
  → code 0, data.{session_token, refresh_token, user_id, bonus, coin, is_vip, ...}
POST /login/refresh_token {refresh_token}      → same shape
```

Anonymous signup is instant and unlimited per `device-uuid`, which is why a client can be built
without credentials.

## 3. Data model

`GET /v3/all?page=&page_size=` → `data: [DramaItem]` (a bare list, **not** `{list:[...]}`).

Relevant `DramaItem` fields: `drama_id`, `drama_title`, `drama_cover`, `chapters`,
`charge_chapters`, `unlock_type` (`"coin"`), `chapter_id` (first episode), `play_url` (a preview).

`GET /v1/drama/chapter/list?drama_id=&page=1&page_size=100` → list of chapters with
`chapter_id`, `chapter_index`, `chapter_name`, `is_free`, `chapter_price`.

`GET /v1/drama/chapter/detail?drama_id=&chapter_id=` → `DramaChapterInfo` with
`chapter_is_free`, `price`, `unlock_type`, `play_url`, plus the gate fields
`is_ad_unlock_user`, `max_ad_unlock_count`, `current_ad_unlock_count`, `ad_rule{ad_count,
chapter_count, pay_window_count}`, `coin`, `bonus`.

In practice a typical drama: **50 episodes, 7 free, 43 charged at 50 coins each.**

## 4. Media

A *free* chapter returns

```
play_url = /v1/play/<chapter_id>/v.m3u8?auth_key=720p<32 hex>
```

`GET https://api.shortswave.com<play_url>` → HLS playlist whose segment lines are absolute,
Google-signed GCS URLs (`X-Goog-Algorithm=GOOG4-RSA-SHA256`, `X-Goog-Expires=21600`) on
`v.shortswave.com`. Segments are MPEG-TS (`0x47` sync, ~787 KB) and support `Range` → `206`.

Codec: the TS stream is **HEVC/H.265** (`hevc, Main, 720x1280`, 30 fps) + AAC-LC stereo. There is
exactly **one rendition** — no master playlist, no multi-bitrate ladder. Probed for alternates
(`1080p`/`540p`/`480p` auth_key prefixes, `/index.m3u8`, `/master.m3u8`, `h264`/`avc` query flags,
same path with another `auth_key`): every one answers **403**, only the signed `720p…` URL works.

Consequence: browsers without an HEVC decoder (Chrome/Firefox desktop, most WebViews, Chrome on
non-HEVC Android) play the **audio with a black frame** — `videoWidth === 0` while `currentTime`
advances. Safari/macOS and Android devices with HW HEVC play it fine.

**A paid chapter returns `play_url: ""`.** Section 6 explains why that cannot be worked around.

## 5. Task / bonus system

`GET /task/v2/list` → 15 tasks totalling **747 bonus**:

| task | pay | limit |
|---|---|---|
| `new_user` (open_notify, bind_email, cal_remind, collect_5, bind_fb, widgets) | 30–50 | 1 |
| `watch_task` (2001/2002/2003) | 15/45/61 | 1 |
| `ads_task` | 10–18 | 1–10 |

**`POST /task/v2/submit {task_id}` is completely client-trusted** — it pays out without any
proof that the task was done:

```
{'task_id':'1001'}      → {"code":0,"data":{"task_bonus":50,"task_times":1}}
{'task_id':'2001'}      → {"code":0,"data":{"task_bonus":15,"task_times":1}}
repeat                  → code 41 "the task is in limitation"
```

This is the one genuine server weakness found: unlimited bonus across anonymous accounts.
**It does unlock episodes — the chapter price is paid from `bonus`, not `coin`** (see below).

## 5b. Unlock billing: `bonus`, not `coin` (2026-10-26)

Earlier notes missed this because the gate field looked like `coin` (`coin: 0` while
`price: 50`). Measured on drama `6adb02c255756f63a270005d`, ep 8→10 with
`GET /v1/unlock/drama/chapter?unlock_type=coin&auto_pay=1`:

```
ep8  code=0 play_url=True price=50 bonus=102
ep9  code=0 play_url=True price=50 bonus=52
ep10 code=0 play_url=True price=50 bonus=2
user_info            bonus 152 -> 2   coin 0 (never moves)
```

* `unlock_type=coin` is the app's only spend call (`NetService.F0`; `DramaDetailActivity.r()`
  hardcodes `"coin"` and only varies `auto_pay`). It bills **`bonus`**.
* `unlock_type=bonus` → `401 "invalid unlock_type value"`; only `coin` and `ad` exist.
* `auto_pay=0` also answers a `play_url` (with `price: 0`) while the bonus balance is positive;
  with an empty balance it answers `code 0` and `play_url: ""` — that is the "locked" state.
* `unlock_type=ad` spends no bonus but draws from `max_ad_unlock_count` (10, per device+day,
  `402 "too many times"` after that).
* A brand-new anonymous account on a new `device_uuid` starts with a fresh pool:
  farm = **747 bonus** (`/task/v2/submit` loop, 11 rounds, ~50 requests) + 10 ad unlocks
  ⇒ **~15 extra chapters per account-device** (25 with the ad quota).
* `/login/anonymous` on the *same* `device_uuid` always returns the **same** `user_id` (and the
  same balance). The session token can go stale (`code 20 "not authorization"`,
  `login/refresh_token` → "invalid token"); a fresh `/login/anonymous` mints a new token for the
  same account, so unlocks survive.
* Bonus from tasks is per account, not per device: farming a second anonymous account does not
  refill the first one.

## Unlock matrix

| Path | Endpoint | Result | Why |
|---|---|---|---|
| Free chapter | `/v1/drama/chapter/detail` | ✅ **works** | `chapter_is_free=1` → signed `play_url` |
| Bonus-paid unlock | `/v1/unlock/drama/chapter?unlock_type=coin&auto_pay=1` | ✅ **works** | Bills `bonus` (50/chapter); balance 102→52→2, coin untouched |
| Farm the balance | `/task/v2/submit {task_id}` loop | ✅ **works** | No verification; 747 bonus/account/day |
| Ad unlock | `unlock_type=ad` | ⚠️ 10/day | `402 "too many times"` after `max_ad_unlock_count`; per device+day |
| `auto_pay=0` with bonus > 0 | same endpoint | ✅ | Returns `play_url`, `price: 0` |
| `unlock_type=bonus` | — | ❌ | `code 401 "invalid unlock_type value"` |
| Coin-only path | `unlock_type=coin`, bonus = 0 | ❌ | `code 0` + empty `play_url` (`coin` never pays) |
| Ad flag spoof | — | ❌ | `is_ad_unlock_user` is server-read-only |
| Coin purchase | `/order/order_ack` | ❌ | Requires a Google Play receipt; server calls `androidpublisher.googleapis.com/.../purchases/subscriptionsv2/tokens/<token>` and returns the real HTTP 400 |
| VIP / SVIP | `unlock_type=vip`, `svip`, `free` | ❌ | `code 401 "invalid unlock_type value"` |
| VIP umbrella | `/v3/vip_only_drama_list?drama_id=` | ➖ | `[]` for every tested drama; `/order/goods/third/list` only sells coin packs (no `dayNum` product) → no client-reachable VIP |
| **`auth_key` reuse** | `/v1/play/<paid_id>/v.m3u8?auth_key=<key from another chapter>` | ❌ **403** | `auth_key` is bound to one chapter id |
| `auth_key` guessing | no key / dead key | ❌ 403/404 | Key must match the chapter |

Verified from the client side (see `work/t_buy.py`, `t_gate.py`, `t_flags.py`, `t_adunlock.py`):

```
free chapter, no auth_key                        → 403
free chapter, random auth_key                    → 403
chapter B video + chapter A's auth_key           → 403
chapter video + real auth_key, no session/UA     → 200  (media is session-free once unlocked)
```

`auth_key` itself is static per chapter (three consecutive `chapter/detail` calls returned the
same `720p…` key, no TTL inside it) — the time-boxing lives in the CDN: the playlist's
segments are GCS V4 signed URLs with `X-Goog-Expires=21600` (6 h) and `X-Goog-Date`, so a
playlist fetched later than ~6 h after issue must be re-fetched (`/proxy/hls` does this).

**Conclusion (corrected):** unlocking is reachable from a client — **farm `bonus`, then
`unlock_type=coin&auto_pay=1`** — at a cost of ~50 bonus per chapter with a per-device ceiling
of ~25 chapters/day. Unlocks are per account (chapter `play_url` stays empty for another
account), but once issued the signed media URL plays without any session.

## 6. What was built

| File | Purpose |
|---|---|
| `work/sw_client.py` | Reconstructed client: header set, anonymous login + refresh, SQLite session/cache, feed/chapters/detail/unlock/tasks, `farm_tasks()` |
| `work/server.py` | Flask API + HLS proxy (rewrites playlists and segments through itself, fixes CDN MIME types, passes `Range` through) |
| `work/ui/` | React + Vite UI, app-shaped: 5-tab bottom nav (Discover / For You / Rewards / My List / Me), language picker, debounced search + hot search, category tabs (localised tag ids), ranking, player with WebVTT subtitle picker, lock panel, task farming. My List + history in `localStorage` |
| `work/probe_*.json` | Saved live responses used as fixtures |

Run:

```bash
./start.sh                   # background, :8000 (API + proxy + built UI)
./start.sh                   # lagi -> "sudah berjalan", tidak spawn duplikat
PORT=8011 SW_DB=sw_test.db ./start.sh
FOREGROUND=1 ./start.sh      # log ke terminal
./stop.sh                    # hentikan instance port ini (PORT=... untuk port lain)
./stop.sh --all              # hentikan semua instance proyek ini

cd work/ui && npm run dev    # optional dev server, proxies to :8000
```

`start.sh` menolak start bila port dipakai proses lain, menunggu `/api/health` siap
(maks 15 s), dan menulis `work/server-<PORT>.pid` + `work/server-<PORT>.log`.
`stop.sh` memverifikasi bahwa PID benar-benar `python3 server.py` dengan cwd `work/`
sebelum membunuh (SIGTERM → SIGKILL), jadi server proyek lain di mesin yang sama aman.

Endpoints: `/api/health`, `/api/login`, `/api/user`, `/api/lang`, `/api/config`,
`/api/search?q=`, `/api/category?tag_id=&channel_id=&page=`, `/api/rank`,
`/api/feed?kind=all|more`, `/api/drama/<id>`, `/api/drama/<id>/title`,
`/api/chapter/<drama>/<chapter>`,
`/api/unlock`, `/api/tasks`, `/api/tasks/farm`, `/proxy/hls?u=<upstream>`, `/proxy/sub?u=<upstream>`.

### 6.1 Localisation (matches the app's header, not a query param)

`lang` / `region` are real request headers. The server keeps one client per
`(lang, region)` pair (`work/server.py::_clients`) and each HTTP call may override them via
`?lang=&region=` or `X-Lang` / `X-Region`, so the UI can switch catalogue without a restart.
`/api/lang` lists the 13 supported codes (en, es, pt, id, th, ja, ko, ar, fr, de, ru, tr, ms).

Switching `lang` changes **the whole catalogue**: feed titles, category names
(`Female/Male/All` → `Femenino/Masculino/Todos`), hot-search words and even the tag ids behind
the categories, so a UI must re-fetch everything after a language change.

### 6.2 Discovery surface

| Endpoint | Upstream | Notes |
|---|---|---|
| `/api/config` | `/v3/drama/bank/channel/list` + `/v1/search/hotrecomm` + `/v3/discover/display/channel` | boot payload: `categories` (3 places × 21–27 tags), `hot_search`, `display_channels` (new / dubbed / anime), `languages` |
| `/api/search?q=` | `/v1/search?word=` | realtime search, 20 hits/page |
| `/api/category?tag_id=&channel_id=` | `/v3/drama/bank/drama/list` | `/v3/rcmd_by_category` 404s on raw tag ids; `bank/drama/list` accepts them |
| `/api/rank` | `/v3/discover/rank/all` | `place_list` → 3 blocks: Most Trending (18), New Release (17), Annually (15) |

### 6.3 Subtitles

`chapter_detail.sublist` carries `{language: "spa-ES", languageId, format: "webvtt",
subtitleDesc, url: https://s.shortswave.com/subtitle/.../<n>.vtt, expire}`. The upstream CDN
serves them as `application/octet-stream`, so `/proxy/sub` re-emits the body with
`text/vtt; charset=utf-8` (host whitelist `*.shortswave.com`) — without that a `<track>`
element silently refuses to render. Not every episode has subtitles (`sublist` is often empty
on episode 1); the player must show "none for this episode" instead of an empty menu.

End-to-end check (through the whole stack):

```
GET  /api/chapter/<drama>/<free>   → playable: true
GET  /proxy/hls?…m3u8              → 200 application/vnd.apple.mpegurl
GET  /proxy/hls?…v0.ts             → 200 786968 bytes  video/mp2t  TS sync 0x47
GET  /proxy/hls?…v0.ts (Range)     → 206 bytes 0-1023/786968
POST /api/unlock (paid)            → ok:false, "empty play_url (insufficient coin / flag-gated)"
GET  /api/config?lang=es           → categories Femenino/Masculino/Todos, feed es ≠ th
GET  /api/chapter/6ad86fde…/…      → subtitles[0] = spa-ES webvtt
GET  /proxy/sub?u=…9_869.vtt       → 200 text/vtt 5688 bytes, starts "WEBVTT"
```

**Codec: the streams are HEVC/H.265, which many browsers cannot decode.** A segment of
`Undercover Billionaire: Claimed by the Mafia Queen` (`6ad07911…`, ep. 1) probes as
`hevc Main 720x1280 yuv420p` + `aac LC` inside an MPEG-TS HLS (a 480p variant exists for some
titles). Chrome/Firefox on desktop and most WebViews have no HEVC decoder, so `<video>` plays the
AAC track and leaves the frame black — "sound but no picture" is a **codec** problem, not a proxy
problem (the same `.ts` opens fine in a player with HEVC support: mpv/VLC/Android/ExoPlayer).
The proxy is byte-transparent (`video/mp2t`, TS sync `0x47`, 1.5 MB first segment, Range OK).
The player now detects it (`videoWidth === 0` while audio time advances) and prints the reason
instead of a silent black box. A server-side fix would be
`ffmpeg -i <playlist> -c:v libx264 -c:a aac -f hls …` (verified: `-protocol_whitelist
file,http,https,tcp,tls` with a locally written `.m3u8` -> `h264 768x480` in ~1 s per 15 s) —
that costs CPU/RAM on the phone, so it is not wired in by default.

### 6.4 Full catalogue sweep (`work/sweep_titles.py`)

Walks every catalogue surface for all 13 languages and probes each distinct drama.

```bash
python3 work/sweep_titles.py                  # harvest + health, all 13 languages
python3 work/sweep_titles.py --no-health      # harvest only (~12 min)
python3 work/sweep_titles.py --health-only    # reuse titles.json, probe only (~10 min, 8 workers)
python3 work/sweep_titles.py --langs en,es --workers 8 --sleep 0.05
```

Surfaces harvested: `/v3/all` and `/v3/more` paged until a page yields no new ids,
every tag of `/v3/drama/bank/channel/list` via `/v3/drama/bank/drama/list`, every block of
`/v3/discover/rank/all`, plus `/v1/search/hotrecomm`. Real numbers (2026-10-26 run):

| | |
|---|---|
| distinct dramas | **4559** (dedup on `drama_id`; no drama appears in two languages) |
| per language | en 2561, es 512, pt 294, fr 244, ja 232, th 177, ar 151, id 109, ko 101, de 82, tr 67, ru 17, ms 12 |
| `/v3/all` pages | `page_size` is **ignored** (always 20 rows); en runs ~120+ pages, small languages end after 1–15 |
| episodes | 255 757 total, 39 900 free |
| playable | 4546 / 4559 (99.7 %) — all HLS |
| subtitles | 1268 / 4559 dramas have `sublist` on their first free episode |
| `unlock_type` | `coin` for 100 % of dramas |

Outputs in `work/sweep/`: `titles.json` (+ `distinct[]` and `hot_search`), `titles.csv`
(one row per drama), `health.csv` (chapter/free/playable/subtitle probe), `summary.json`.

Anomalies worth knowing: 1 drama has zero chapters (`45 Nights With My Vampire Boss`),
12 dramas have chapters but **no free episode and no play_url** (`Hidden Queen`,
`Dying to Be Free`, `Wet Nurse`, `Wedding Night Deal`, `Rainha oculta`, `Cœurs glacés`, …) —
i.e. the server withholds `play_url` for the whole title, not just for paid episodes.

Pitfalls hit while building it: `log()` was called while already holding the print lock →
`threading.Lock` deadlock (use `RLock`); a single work bucket per language means the huge `en`
bucket runs for ~40 min with no output (chunk into ≤20-drama tasks); `titles.json` is now
written after every language so an interrupted harvest is not lost.

### 6.5 Pretty drama URLs (`/d/<title-slug>-<id>`) and the title index

Every drama opens at `/d/<slug>-<drama_id>`, e.g.
`/d/marry-a-frog-my-husband-is-the-earth-god-6adb02c255756f63a270005d`.

- `work/server.py` serves `index.html` for any `/d/...` path (SPA fallback, 200) and answers
  `/api/drama/<id>` with `title` + `cover` taken from `work/sweep/title_index.json`.
  `/api/drama/<id>/title` returns just `{title, titles{lang→title}, cover}` (no upstream call).
- `work/sweep/title_index.json` — `{dramas: {<id>: {t: {<lang>: <title>}, c: <cover>}}}`,
  4559 dramas / ~600 KB, written by `sweep_titles.py` (and rebuildable from `titles.json`).
  The server loads it lazily with an mtime check and degrades to "no title" if it is missing.
- `work/ui/src/slug.js`: `slugify()` keeps Unicode letters (th/ar/ja/ko/ru titles have no ASCII
  form; stripping them would leave an empty slug — `/d/<id>` alone is still valid),
  `NFC`-normalises (Hangul stays whole), drops apostrophes, `&` → `and`, max 80 chars.
  `parseDramaPath()` takes the **last** 8+ alnum token as the id, so a stale/hand-edited slug
  still resolves; `dramaPath()` builds the canonical URL.
- The header logo (`ShortsWave`) is an `<a href="/">` that routes home client-side
  (`pushState('/')`, no reload); modifier-clicks / middle-click still open `/` normally.
- The client rewrites the address bar with `replaceState` when the slug is not canonical
  (only if the location still is the URL it was asked to resolve — a click during the
  in-flight `/api/drama` must not be undone). Opening a drama and the Back button use
  `pushState('/d/…')` / `pushState('/')`, so no reload; `popstate` re-runs the router.

### 6.6 Headless UI test (`work/ui/test/deeplink-smoke.mjs`)

Termux has no jsdom/browser here, so the test runs the *built* `dist/` bundle in a `vm` context
against `work/ui/test/dom-shim.mjs` (a flat DOM: elements, events, `location`/`history`,
`localStorage`) and asserts on rendered text and `location.pathname`:

```
cd work && PORT=8031 SW_DB=swtest.db ./start.sh     # server for the test
cd work/ui && npm run build && node test/deeplink-smoke.mjs
```

Covers: deep link to a canonical URL, auto-play of the first free episode, stale slug → canonical
rewrite, id-only URL → canonical, unknown id → "Drama not found" (upstream answers 200 with an
empty chapter list, so `ok` alone is not enough), Back → `/`, clicking the header logo → `/`
(via `pushState`, i.e. no reload), and th/ar/ja titles round-tripping
through a percent-encoded URL, plus the H.264 fallback: `h264Url()` helper, playlist served as
`#EXTM3U`, URIs pointing at `/proxy/h264`, job actually started, `f=../../server.py` rejected (400),
and the unlock sweep: `/api/unlock_all` answers in well under a second while `running: true`,
reports `total` up front, `/api/unlock_status` finishes with `open + failed == total`, every failed
chapter carries a reason, and the detail page renders progress (never "all unlocked" while
chapters stay locked). 43–46 checks depending on how many failures the sweep reports, run from a
cold `work/.cache/h264`.
Shim notes: React 18 dispatches through its own fiber tree, so
`click()` re-fires the event on every ancestor until a listener reacts, and the shim keeps a
*list* of listeners per (target, type). The test picks the first *feed* drama that actually
resolves: upstream occasionally lists a drama whose chapter list is empty (pulled/private), which
otherwise fails the H.264 cases for reasons that have nothing to do with the code. Among the first
eight feed dramas it prefers the **shortest**, because the sweep case waits for the whole drama to
open and a 60–70 episode drama costs 6–8 minutes of account rotation (budget: 12 min).

Two practical notes: `SW_FARM_ACCOUNTS` must be >= `1` on the test server when the sweep case is
meaningful (0 makes a long drama end in `too many times` immediately), and **run only one suite at
a time** — two concurrent runs join the same `_unlock_jobs` entry, one finishes the sweep while the
other reads an empty state and reports bogus "0+0 != N" failures.

### 6.7 H.264 fallback (server-side transcode) — `work/transcode.py`

Because §4 shows the stream is HEVC-only, the server renders each episode to H.264 on demand:

| route | meaning |
|---|---|
| `GET /proxy/hls?u=<play>&h264=1` | the same playlist URL the app already uses, plus `h264=1` → the transcoded playlist |
| `GET /proxy/h264?ch=<chapter_id>&f=index.m3u8` | that playlist |
| `GET /proxy/h264?ch=<chapter_id>&f=init.mp4` / `f=seg00000.ts` | media segments (`video/mp2t`, `Range`/`conditional`); the fMP4/`init.mp4` path is legacy, renditions are mpegts |
| `GET /api/h264/status?ch=<chapter_id>` | `{ready, done, running, segments, ok}` for the cache/job |

How it works (`work/.cache/h264/<chapter_id>/`):

- First request for a chapter starts **one** `ffmpeg` (`libx264 -preset ultrafast -crf 26
  -profile:v main -pix_fmt yuv420p -vf scale=-2:480`, AAC 96k, 4 s chunks, `-hls_init_time 1`) and
  returns immediately. Later requests tail the growing playlist.
- The signed master is downloaded once and its absolute segment URLs are written to a local
  playlist, so ffmpeg keeps working after the 6 h `auth_key` would have expired
  (`-protocol_whitelist file,http,https,tcp,tls,crypto`).
- Playlist is written to `live.m3u8` while running and renamed to `index.m3u8` (with
  `EXT-X-ENDLIST`) on exit; the server rewrites `seg*.ts` to `/proxy/h264?ch=…&f=…`. A playlist
  without `ENDLIST` counts as usable *only* while a job is running — otherwise it came from a dead
  job or an older rendition and is reported not-ready so it gets re-encoded.
- The signed play URL usually carries no `drama_id`, and the local `chapter` table is filled lazily,
  so `_h264_playlist` resolves the owner via `_drama_id_for_chapter()` → DB, then
  `work/sweep/titles.csv` column 8 (`first_chapter_id`). Without that, `_fresh_play_url` returned
  `None`, ffmpeg got `''` and died with `unknown url type: ''` (playlist stayed empty forever).
- `SW_H264=0` disables the feature (503), `SW_H264_HEIGHT` / `SW_H264_CRF` / `SW_H264_PRESET`
  tune quality, `SW_H264_CACHE_DAYS` (default 7, `0` = keep forever) drives an hourly sweeper.

Measured on this phone (Helio-class, Termux): **~4.5x realtime** — a 3 min 17 s episode finishes
in ~48 s, the first segment is playable at **t+3 s**. Output probes as `h264 270x480 @30` + `aac`,
and a full decode of the playlist is clean.

Gotcha worth remembering: **do not add `-hls_flags append_list`**. With it ffmpeg writes the
`EXTINF` list once and then appends bytes, so the playlist never grows and every player stalls
after the first chunk (this cost one debugging round).

Player side (`work/ui/src/App.jsx`): `hevcSupported()` (`MediaSource.isTypeSupported('video/mp4;
codecs="hvc1.1.6.L93.B0"')`) decides up front — no HEVC → `h264Url(play)` (`…&h264=1`); HEVC-capable
→ the original stream. If the picture still stays black while audio plays, the probe switches to
H.264 once and then shows a message instead of a silent black box. A "switch" link toggles both ways.

### 6.8 Unlocking every episode when the detail page opens

Opening `/d/<slug>-<id>` must unlock the whole drama. Doing that inline blocked the request for
minutes (one upstream call per chapter + bonus farming), so it runs as a job:

| route | meaning |
|---|---|
| `GET /api/unlock_all/<drama_id>` | starts (or joins) the sweep, answers in ~0.4 s with `{running, phase, total, open, bonus, price, bonus_short, quota_hit}` |
| `GET /api/unlock_all/<drama_id>?refresh=1` | the UI's Retry: re-runs even when a cached result exists |
| `GET /api/unlock_status/<drama_id>` | same payload while polling (no side effects) |
| `GET /api/unlock_all/<drama_id>?wait=1` | old blocking behaviour, for scripts |

- `_unlock_jobs[<drama_id>]` + `_start_unlock_job()` hold the state; the worker thread gets its own
  client (`client_for(lang, region)`) because there is no Flask request context in a thread.
- `sweep_unlock()` reports `phase` (`starting` / `unlocking` / `farming` / `done`). With rotation on
  it keeps going until every chapter is open, so a long drama takes minutes; the budget of accounts
  is derived from the drama size (see below), not a fixed number.
- The payload is deliberately honest: `open + failed == total`, every failure carries `msg`, and
  `bonus_short` / `quota_hit` say *why* the rest cannot open (bonus 47 < 50 per episode, ad-unlock
  quota 10/10, all daily tasks claimed). Measured on a 50-episode drama: 21 open, 29 `too many
  times`, sweep done in ~16 s instead of 200 s+.
- UI (`App.jsx`): `unlockNote()` turns that into one sentence, polling follows `running` every 2 s,
  and a Retry button re-runs with `?refresh=1`. The grid fills in as chapters open.

#### Account rotation (how a long drama gets *fully* opened)
`bonus` is per account **and** per `device_uuid`: a fresh anonymous account/device always farms
**exactly 747 bonus = 14 chapters** (50 each, 47 left over). Ad-unlock is useless as a fallback
(402 `too many times`, bound to the IP/day). There is **no per-account cap per IP**: accounts 8..11
created back-to-back all farmed 747 (~56 s each), so the only thing standing between a 61-chapter
drama and "all open" is the number of accounts a sweep is allowed to create.

- `sweep_unlock(..., rotate=True)` therefore derives its own budget from the drama size:
  `budget = min(SW_FARM_MAX_ACCOUNTS, max(SW_FARM_ACCOUNTS, chapters//14 + 2))`, up to
  `FARM_MAX_ROUNDS = 3` passes over the list, stopping as soon as every chapter id is in `open`.
  `SW_FARM_ACCOUNTS=0` disables rotation entirely (used by the test server for fast runs).
- History of the blocker: the fixed cap (2, then 4 accounts) *was* the "quota". A 69-episode drama
  (61 paid) needs 5 accounts; with a cap of 4 the sweep stopped and reported the leftovers as
  `too many times`, which looked like an upstream quota but was our own budget.
- **Measured end-to-end (drama `6adacb81…`, 69 chapters / 61 paid):** `open 69, failed 0`,
  5 rotated accounts, 3735 bonus farmed, ~960 s (~16 min). Over HTTP afterwards: 61/61 paid
  chapters `playable: true`, playlists of E9/E39/E69 answer 200 `#EXTM3U`.
- `_farm_account()` creates `work/.farm/autoNNN.db`, logs in anonymously (fresh device_uuid → fresh
  pool) and drains the task list. **Never reuse a DB file name**: `Client()` adopts the session kept
  in it, so a collision silently continues the *old*, already-spent account. The old
  `len(listdir)+1` naming did exactly that (`auto015.db` was left over from an earlier run → every
  rotation farmed 0, unlocks kept answering `too many times`). It now walks forward to the first
  free name.
- `farm_tasks()` returns `{bonus, tasks, dry}`; `dry` = every submit answered `code 41 "the task is
  in limitation"`, i.e. this account's pool is spent. The sweep then reports
  `daily task pool for this account is spent` instead of pretending "no bonus left", and treats
  `dry` as another reason to rotate (a reused session can be broke while `bonus` still looks fine).
- Silent failure worth knowing: with `bonus 47 < price 50`, `GET /v1/unlock/drama/chapter`
  (`unlock_type=coin&auto_pay=1`) answers **HTTP 200 with `play_url: ""`** and no code/message.
  `_try_unlock()` now treats an empty play URL as a failure and says
  `not enough bonus (bonus 47 < price)` instead of reporting a phantom unlock.
- Cross-account playback: a chapter paid by a rotated account is invisible to the default one, so
  `/api/chapter` falls back to the play URL that unlock returned (`work/play_urls.json`,
  `_remember_play_url` / `_stored_play_url`); the sweep records every URL it gets.

#### Titles for freshly listed dramas (`work/feed_titles.json`)
`/api/drama` cannot return a title (upstream chapter list has none) and
`work/sweep/title_index.json` is only as fresh as the last sweep. A drama that just entered the feed
therefore had `title: null`, so the UI could never canonicalise `/d/<slug>-<id>` (a stale/legacy
slug stayed in the address bar). `/api/feed` now remembers every `drama_title` it serves
(`feed_titles.json`) and `title_for()` falls back to it — then to one upstream feed call — before
giving up. The cover comes from the same cache.

### 6.9 Bonus economy audit (2026-10-26) — the whole ceiling, measured

Every stop on the earn/spend path was re-measured with the real client, so this is the complete
budget of a free account, not a sample. Nothing below is guessed.

**Earn — one fresh account/device = exactly 747 bonus / day.** That is *not* a cap imposed on the
loop: it is the sum of every task's `limit × bonus` from `GET /task/v2/list`, and the raw loop
collects every single one of them (`n = limit` exactly, then `code 41`):

| task | limit | bonus | total |
|---|---|---|---|
| `3003` Watch ADs | 10 | 10 | 100 |
| `tb_ad2_…` | 9 | 14 | 126 |
| `tb_cd2_…` | 9 | 10 | 90 |
| `2003` Watch 25 min | 1 | 61 | 61 |
| `2001`/`2002` | 1 | 15/45 | 60 |
| 5× new-user tasks (`1001`,`1003`,`1004`,`1004_…`,`010101010`) | 1 | 30–50 | 210 |
| `999888`, `202202098` (widgets) | 1 | 50 | 100 |

- `farm_tasks()` already returns the maximum possible `{bonus: 747, dry: True}` — verified task by
  task against a hand-written loop, so nothing was being left on the table by the client.
- Language/region do not change it: `en/US`, `es/MX`, `id/ID` all farm **747**. No per-IP limit
  either: 4 accounts created **in parallel** farmed 747 each (and 8…11 sequentially did before).
- **Race/burst on `/task/v2/submit` does not over-credit.** 32 parallel submits of the same id
  gave exactly `limit × bonus` (`3003` → 100, `tb_ad2` → 126); a 32-way burst on `2003` gave 61 +
  31×`code 41`. One single anomaly was seen once (`2003` credited 122 = 2×61) and could not be
  reproduced in three further 8/4/2-way bursts or a 32-way one — treat it as an upstream glitch,
  not a strategy.
- Other endpoints carry **no bonus at all**: `/task/v2/type_list` returns the five task types but
  `/task/v2/list?type=…` answers the same list for every one; `/task/subcoin/sign/info`
  `{"bonus": 0, "bonus_list": []}`; `/task/v2/sign/{id}/{info,checkin}` → `code 30 "sign task is
  not exists"` / 405; `/user/get_conf` `{}` for any `type`; `/user/attr/report`, `/user/is_shown`,
  `/user/charge/history`, `/user/abtest/list`, `/v1/fbgroup/activity`,
  `/activity/purchase/user_profile` all empty; `/activity/wheel/*` → `code 206 "current date is not
  matched"`; `/activity/mix/pack/get` and `/activity/subscribe/pack/get` → `{"package": {}}`.
  There is **no** invite/share/redeem/transfer endpoint in `NetService` to abuse.

**Spend — 50 bonus per paid chapter, no cheaper path.**
- `unlock_type` is an enum of two: `coin` (paid from bonus) and `ad` (per device+day, already at
  `402 too many times` on this IP). `vip`, `subscribe`, `external_org`, `free`, `bonus`, empty → `401
  "invalid unlock_type value"`.
- `auto_pay` is a switch, not a mode: `0` → `play_url: ""` (price 50) for a locked chapter, `1` →
  charges 50, and `-1` / `2` / `99` behave exactly like `1`. There is no "look without paying".
- `POST /v1/drama/chapter/detail/part {drama_id: <intid>, chapter_indexes:[..]}` is a batch unlock:
  it charges 50 per index in order (747 → 647 → 597 → 547 …) and answers `{}`. Same price, no gain.
  (The string `drama_id` makes it a 500 — the app sends the numeric `drama_intid`.)
- `/v3/vip_only_drama_list` (9 ids) contains ordinary priced chapters; VIP is a client flag only.

**Cross-account.** `bonus` cannot be moved between accounts (no endpoint exists), and
`chapter/detail` for a chapter opened by account A answers `play_url: ""` for account B. But the
issued media URL `/v1/play/<chapter_id>/v.m3u8?auth_key=…` plays **for any account** (200 with a
different session, 404 without the `auth_key`), which is what makes `work/play_urls.json` a valid
cache for chapters paid by rotated accounts.

**Ceiling:** 747 bonus ≈ 14 paid chapters per account-device, ~56 s to farm, unlimited accounts per
IP (no measured cap) — a 56-chapter drama needs 4 accounts and opened 56/56 in one sweep.
The only free addition is `unlock_type=ad`, 10 chapters, and it is spent after the first session.

**Fixes that came out of the audit** (`work/server.py`):
- `absolute()` now rejects a play URL whose `auth_key` has no signature (`auth_key=720p`): the API
  hands those out on the *first* fetch after an unlock even to the account that owns the chapter,
  and they answer 403 — caching one made a paid chapter look dead. `_try_unlock()` re-asks
  `chapter/detail` once instead of reporting a failure (and losing the 50).
- `_fresh_play_url()` falls back to the last `chapter/detail` stored in the local `chapter` table
  (`_stored_detail`) when the default account answers empty for a chapter paid by a rotated one.
- `sweep_unlock()`: `bonus_short` / `quota_hit` are now derived from the chapters that are *still
  locked*, not from the whole `failed` list (a sweep that ended with `open 37 / failed 19 / bonus
  47` used to keep both flags `True` after a later retry had opened everything).

## 7. Notes / caveats

- `auth_key` are short-lived (`X-Goog-Expires=21600` = 6 h); fetch chapter detail right before playback.
- `GET /v3/all` returns a bare JSON list; chapter list does too — do not assume `{list: [...]}`.
  `GET /v1/drama/chapter/list` **requires** `page` + `page_size`, otherwise `code 1 "invalid page
  and page_size"`.
- `/activity/wheel/*` is region/date gated (`code 206 "current date is not matched"`);
  `/activity/mix/pack/get` and `/activity/subscribe/pack/get` answer `{"package": {}}`
  (UTC `server_now`), i.e. no activity running in this region, and
  `/activity/subscribe/user_profile` → `code 30 "can not find any subscribe activity info"`.
  `/v3/vip_only_drama_list` does return 9 ids, but their chapters are ordinary priced chapters
  (`is_free 0`, price 50) — VIP is only a client flag (`subscribe_expire`), the server does not
  hand out free `play_url` for them.
- `DramaPayCoinDialogNewUiActivity` also references a StarMobi H5 fallback for low-eCPM ad
  traffic; that path awards a few bonus, never coin. Ad/H5 tasks (`GET /v1/ad/fallback/config`,
  `POST /task/v2/independent/list {type:"sm"}`) hand out `taskId`/`taskOpenUrl`/`tk` but the
  reward needs a server-to-server ad-network postback — this stub is *advertising*, not drama
  data, and is not needed because the plain `/task/v2/submit` loop already pays without proof.
- **Re-checked 2026-10-26 (so nobody repeats it):** the body of every `/task/v2/start|refresh|query`,
  `/task/v2/independent/*` and `/v1/ad/task/start` is `ck.f0 {task_id, task_type}` and the app
  hardcodes `task_type = SRStrategy.KEY_SR_STRATEGY_MODE = "sm"`
  (`ul/r3.java` → `NetService.o()`, same class used by the StarMobi SDK in `com/onesignal/g4.java`).
  With that body: `start` → HTTP 200 `data: null`, `refresh` → `code 40 "refresh task failed"`
  on a brand-new account (i.e. the network postback is what is missing), unknown ids →
  `code 40 "start task failed"`, missing `task_type` → `code 30 "task_type is invalid"`.
  No bonus was ever credited. Daily pools are untouched by this path; the earn side stays
  `/task/v2/submit` (747/account) and the spend side stays `GET /v1/unlock/drama/chapter`.
- Unlock economics (2026-10-26): chapter price 50 **bonus**; a fresh account-device yields
  ~747 bonus + 10 ad unlocks ≈ 25 chapters/day, and `bonus` is not transferable between
  accounts. `coin` is only sold in Google Play packs and never billed by the unlock endpoint.
- UI parity with the Android app is now near-complete on *reading* surfaces (5 tabs, language,
  categories, search, ranking, subtitles, wallet, task farming) and `bulk unlock` on the detail
  page (farm → `unlock_type=coin&auto_pay=1` → ad fallback). Still missing by design:
  Google Play purchase and the `/activity/wheel` mini-games (region/date gated upstream).
  My List / history are client-side only — the API exposes no watchlist.
- **Parallel sweep (2026-10-26, `work/server.py:sweep_unlock`)** — the detail page never waits for
  the unlock: `/api/unlock_all/<id>` starts a background job and answers in ~0.4 s, the UI polls
  `/api/unlock_status/<id>`. Inside the job `SW_UNLOCK_WORKERS` (default 6) workers each stream
  chapters with their **own** account/device, so farm latency overlaps the pay calls: the caller's
  account starts opening immediately while `SW_UNLOCK_WORKERS-1` fresh accounts are farmed in a
  thread pool (`SW_FARM_ACCOUNT_TIMEOUT` 90 s each); a worker whose balance runs dry waits for the
  next farmed account. `sw_client.farm_tasks()` submits the ~30 tasks concurrently
  (`FARM_WORKERS=8`) and caches each task's per-day limit, so a repeat farm costs < 1 s instead of
  ~60 s; `task_list()` is memoised in memory + `work/.task_list.json` (`TASK_LIST_TTL` 600 s).
  Chapters already paid for are read from `play_urls.json` and skipped, `_pay_for_chapter()` also
  returns a replacement client when the balance is short, `SW_UNLOCK_RETRY_ROUNDS` (default 1)
  retries only the leftovers, and a `"too many times"` answer stops the workers early.
  Measured: 45-chapter drama (39 paid) → 45/45 open, 0 failed, 3 accounts, ~56 s; 71-chapter drama
  → 71/71 in ~84 s with 5 accounts. Headless suite `work/ui/test/deeplink-smoke.mjs` still passes
  all 45 checks against the parallel sweep.
- **UI stays in sync with the sweep (2026-10-26)** — the detail page used to look frozen: it polled
  `/api/unlock_status` every 2 s but stopped for good on the first failed/aborted poll, and there was
  no visible progress line, so the only way to see anything was a manual refresh. Now: `api()` takes
  `{nocache:true}` (live endpoints never join the sticky in-flight memo), polls run every 1.5 s,
  a failed poll retries with backoff 2 s→15 s instead of ending the loop, a page opened while a job
  is already running re-attaches to it, and the header shows `N episodes · M free · unlocking 12/45 · 34s`
  (`started` is now part of `_unlock_state`). Suite checks: "running sweep is visible without a
  manual refresh" and "progress counter advances on its own (polling, not refresh)".
  Caveat found while testing: the *chapter list* of a stale id from an old feed snapshot answers
  `200 []` — a sweep on such an id legitimately reports `total 0` (nothing to open), it is not a
  bug; always pick the id from a fresh `/api/feed`.
- **Full catalogue enumeration (2026-10-28, `work/sweep_titles.py` → `work/sweep4/`)** — the
  harvest now walks every surface per language: `/v3/all` + `/v3/more` paged to exhaustion
  (`/v3/more` is session-randomised, so re-running it yields a different slice), every category tag,
  every rank block, every topic, `discover/channel?channel=new|dubbed|anime`, `top_unlock_list`,
  `topn`, `topn_v1_r3` and `vip_only_drama_list`. Result: **4750 distinct dramas, 267 104 episodes,
  4738 playable on the first episode**, per language en=2737, pt=779, es=519, fr=243, ja=232,
  th=179, ar=153, id=110, ko=100, de=83, tr=69, ru=15, ms=11 (479 dramas carry a title in >1
  language, 138 duplicate title texts = translated releases of the same show). `unlock_type` is
  `coin` for 4743, `ad` for 6; 1293 dramas expose at least one subtitle track (2 rows were
  re-probed by hand because the first `auth_key=720p` 403 had left them blank).
  Stage 2 (`--health-only --append-health`) resumes a crashed probe instead of redoing it: the
  3985 already-probed rows were kept and the remaining 765 took 129 s at 6 workers.
  `work/build_titles_artifact.py --sweep sweep4 --install` merges `titles.json` + `health.csv`
  into `titles.tsv` / `titles_all.csv` (one row per drama, sorted by title), `slug_index.json`
  (`/d/<slug>-<id>`) and `index.md`, and copies `title_index.json` to `work/sweep/`, which is the
  path `server.py::catalogue_titles()` reloads on mtime change — after installing it
  `/api/drama/<id>` returns `title` for all 4750 ids and `/d/<slug>` answers 200.
