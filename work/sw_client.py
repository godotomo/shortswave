"""ShortsWave API client reconstructed from com.shorts.wave.drama v2.68.00.

No request signing, no response encryption: the API is plain JSON
{code,msg,data} over HTTPS with a fixed header set + X-SESSION-TOKEN.
"""
import json
import os
import random
import sqlite3
import string
import threading
import time
import uuid

import requests

API = "https://api.shortswave.com"
EVENT = "https://event.shortswave.com"

APP = "com.shorts.wave.drama"
VERSION = "2.68.00"
PACKAGE_ID = "6"
TASK_LIST_PATH = os.environ.get(
    "SW_TASK_LIST_CACHE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".task_list.json"))
FARM_ROUNDS = 60          # /task/v2/submit loop cap (server stops claiming earlier)
FARM_WORKERS = 8          # concurrent /task/v2/submit calls per round
SUBMIT_RETRIES = 2        # a socket reset must not lose a task's bonus
TASK_LIST_TTL = 600       # /task/v2/list is static per app version (measured)

BRANDS = [("samsung", "SM-G991B"), ("Xiaomi", "M2101K7AG"),
          ("OnePlus", "LE2115"), ("Google", "Pixel 6"), ("OPPO", "CPH2219")]


def _rand_hex(n=32):
    return "".join(random.choice("0123456789abcdef") for _ in range(n))


def base_headers(device_uuid=None, lang="en", region="US", brand=None, model=None,
                 width=1080, height=2400):
    brand = brand or random.choice(BRANDS)[0]
    model = model or next(m for b, m in BRANDS if b == brand)
    return {
        "app": APP,
        "os": "1",
        "os_ver": str(random.choice([12, 13, 14])),
        "mobile-brand": brand,
        "model": model,
        "device-uuid": device_uuid or _rand_hex(32),
        "version": VERSION,
        "lang": lang,
        "region": region,
        "width": str(width),
        "height": str(height),
        "contype": "1",
        "carrier": "",
        "sign-code": "",
        "package-id": PACKAGE_ID,
        "advertising-id": "",
        "attribution_id": "",
        "attribution_type": "",
        "afid": "",
        "lip": "",
        "User-Agent": f"ShortsWave/{VERSION} (Android;13;{brand};{model};)",
        "Content-Type": "application/json; charset=utf-8",
        "rule-ab": "",
    }


class ShortsWaveError(Exception):
    def __init__(self, code, msg, payload=None):
        super().__init__(f"[{code}] {msg}")
        self.code = code
        self.msg = msg
        self.payload = payload


class Client:
    """Single-user API client. Re-auths automatically with the refresh token."""

    def __init__(self, db_path="sw.db", device_uuid=None, lang="en", region="US"):
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._init_db()
        self.http = requests.Session()
        self.lang, self.region = lang, region
        stored = self._load_session()
        self.device_uuid = device_uuid or (stored["device_uuid"] if stored else _rand_hex(32))
        self.headers = base_headers(self.device_uuid, lang, region)
        self.session_token = stored["session_token"] if stored else None
        self.refresh_token = stored["refresh_token"] if stored else None
        self.user = json.loads(stored["user_json"]) if stored and stored["user_json"] else None
        # per account: task ids whose daily limit is spent (farm_tasks), plus a
        # short-lived cache of /task/v2/list (static per app version).
        self.spent = {}
        self._list_lock = threading.RLock()
        self._task_cache = None

    # ---------------------------------------------------------------- storage
    def _init_db(self):
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS session(
                id INTEGER PRIMARY KEY CHECK(id=1),
                device_uuid TEXT, session_token TEXT, refresh_token TEXT,
                user_id TEXT, user_json TEXT, updated_at INTEGER);
            CREATE TABLE IF NOT EXISTS cache(
                key TEXT PRIMARY KEY, body TEXT, expires_at INTEGER);
            CREATE TABLE IF NOT EXISTS drama(
                drama_id TEXT PRIMARY KEY, title TEXT, cover TEXT, chapters INTEGER,
                charge_chapters INTEGER, unlock_type TEXT, raw TEXT, fetched_at INTEGER);
            CREATE TABLE IF NOT EXISTS chapter(
                chapter_id TEXT PRIMARY KEY, drama_id TEXT, idx INTEGER, name TEXT,
                is_free INTEGER, price INTEGER, raw TEXT, fetched_at INTEGER);
            """
        )
        self.db.commit()

    def _load_session(self):
        r = self.db.execute("SELECT * FROM session WHERE id=1").fetchone()
        return r

    def _save_session(self):
        self.db.execute(
            "INSERT INTO session(id,device_uuid,session_token,refresh_token,user_id,user_json,updated_at)"
            " VALUES(1,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET"
            " device_uuid=excluded.device_uuid, session_token=excluded.session_token,"
            " refresh_token=excluded.refresh_token, user_id=excluded.user_id,"
            " user_json=excluded.user_json, updated_at=excluded.updated_at",
            (self.device_uuid, self.session_token, self.refresh_token,
             (self.user or {}).get("user_id"),
             json.dumps(self.user) if self.user else None, int(time.time())))
        self.db.commit()

    # ------------------------------------------------------------- http core
    def _raw(self, method, url, **kw):
        h = dict(self.headers)
        if kw.pop("auth", True) and self.session_token:
            h["X-SESSION-TOKEN"] = self.session_token
        return self.http.request(method, url, headers=h, timeout=30, **kw)

    def _call(self, method, path, *, params=None, body=None, auth=True,
              retry_auth=True, absolute=False):
        url = path if absolute else API + path
        r = self._raw(method, url, params=params, data=json.dumps(body) if body is not None else None, auth=auth)
        try:
            payload = r.json()
        except ValueError:
            raise ShortsWaveError(r.status_code, r.text[:300])
        code = payload.get("code")
        if code == 0:
            return payload.get("data")
        # token expired -> refresh once, then fall back to a fresh anonymous
        # login (same device_uuid => same user_id, unlocks stay attached)
        if retry_auth and auth and code in (20, 401):
            if self.refresh():
                return self._call(method, path, params=params, body=body, auth=auth,
                                  retry_auth=False, absolute=absolute)
            if path != "/login/anonymous":
                try:
                    self.rotate_account()
                except ShortsWaveError:
                    pass
                else:
                    return self._call(method, path, params=params, body=body, auth=auth,
                                      retry_auth=False, absolute=absolute)
        raise ShortsWaveError(code, payload.get("msg"), payload)

    # ------------------------------------------------------------------ auth
    def login_anonymous(self, source_channel="googleplay"):
        data = self._call("POST", "/login/anonymous", auth=False, body={
            "device_type": "android",
            "source_channel": source_channel,
            "attribution_id": "", "attribution_type": "", "campaign": "", "afid": "",
        })
        self._absorb_login(data)
        return self.user

    def refresh(self):
        if not self.refresh_token:
            return False
        try:
            data = self._call("POST", "/login/refresh_token", auth=False, retry_auth=False,
                              body={"refresh_token": self.refresh_token})
        except ShortsWaveError:
            return False
        self._absorb_login(data)
        return True

    def _absorb_login(self, data):
        self.session_token = data.get("session_token") or self.session_token
        self.refresh_token = data.get("refresh_token") or self.refresh_token
        self.user = data
        self._save_session()

    def ensure_login(self):
        if not self.session_token:
            self.login_anonymous()
        return self

    def user_info(self, refresh=False):
        if refresh or not self.user:
            self.user = self._call("GET", "/user/user_info")
            self._save_session()
        return self.user

    def rotate_account(self):
        """Start a new anonymous session.

        Note: /login/anonymous on the same device_uuid always answers the *same*
        user_id (measured), so this does not refresh the daily bonus pool --
        only a new device_uuid does. It is still the way to recover from an
        expired session token (`code 20 not authorization`, the refresh_token
        call answers "invalid token" in that case)."""
        self.session_token = None
        self.refresh_token = None
        self.user = None
        self.login_anonymous()
        return self.user or {}

    def new_device(self, farm=True):
        """Switch this client to a brand-new anonymous account.

        A new device_uuid is the only thing that yields a fresh daily bonus pool
        (/login/anonymous on the same device_uuid answers the same user_id), and
        one session DB keys one account, so it is rewritten in place.
        Returns the bonus farmed on the new account."""
        self.device_uuid = _rand_hex(32)
        self.headers = base_headers(self.device_uuid, self.lang, self.region)
        self.session_token = None
        self.refresh_token = None
        self.user = None
        self.login_anonymous()
        if not farm:
            return 0
        return int((self.farm_tasks() or {}).get("bonus") or 0)

    # --------------------------------------------------------------- catalog
    def feed(self, page=1, page_size=20, path="/v3/all"):
        return self._call("GET", path, params={"page": page, "page_size": page_size})

    def more(self, page=1, page_size=20):
        return self.feed(page, page_size, "/v3/more")

    def chapters(self, drama_id, page=1, page_size=100):
        data = self._call("GET", "/v1/drama/chapter/list",
                          params={"drama_id": drama_id, "page": page, "page_size": page_size})
        rows = data if isinstance(data, list) else (data.get("list") or [])
        for c in rows:
            self.db.execute(
                "INSERT INTO chapter(chapter_id,drama_id,idx,name,is_free,price,raw,fetched_at)"
                " VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(chapter_id) DO UPDATE SET"
                " idx=excluded.idx, name=excluded.name, is_free=excluded.is_free,"
                " price=excluded.price, raw=excluded.raw, fetched_at=excluded.fetched_at",
                (c.get("chapter_id"), drama_id, c.get("chapter_index"), c.get("chapter_name"),
                 1 if c.get("is_free") else 0, c.get("chapter_price"), json.dumps(c), int(time.time())))
        self.db.commit()
        return rows

    def chapter_detail(self, drama_id, chapter_id):
        return self._call("GET", "/v1/drama/chapter/detail",
                          params={"drama_id": drama_id, "chapter_id": chapter_id})

    def unlock(self, drama_id, chapter_id, unlock_type="coin", auto_pay=0):
        return self._call("GET", "/v1/unlock/drama/chapter", params={
            "drama_id": drama_id, "chapter_id": chapter_id,
            "unlock_type": unlock_type, "auto_pay": auto_pay})

    # ----------------------------------------------------------------- tasks
    def task_list(self):
        """/task/v2/list, cached in memory and on disk (it is static per version).

        The disk cache is what makes a *new* client cheap: the sweep creates one
        new account per worker, and each of them used to re-download and re-parse
        the whole task list (measured ~0.15 s parse + a round trip)."""
        now = time.time()
        with self._list_lock:
            if self._task_cache and now - self._task_cache[0] < TASK_LIST_TTL:
                return self._task_cache[1]
            try:
                with open(TASK_LIST_PATH, encoding="utf-8") as f:
                    disk = json.load(f)
                if now - (disk.get("at") or 0) < TASK_LIST_TTL and disk.get("list"):
                    self._task_cache = (now, disk["list"])
                    return disk["list"]
            except (OSError, ValueError):
                pass
        data = self._call("GET", "/task/v2/list") or []
        with self._list_lock:
            self._task_cache = (now, data)
        try:
            tmp = TASK_LIST_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"at": now, "list": data}, f)
            os.replace(tmp, TASK_LIST_PATH)
        except OSError:
            pass
        return data

    def task_submit(self, task_id):
        return self._call("POST", "/task/v2/submit", body={"task_id": task_id})

    def farm_tasks(self, workers=FARM_WORKERS):
        """Claim every claimable bonus task. Server does not verify completion.

        /task/v2/submit only checks the task's daily/global limit, not whether
        the action happened (no ad, no watch, no bind), so looping the list
        until the server answers "the task is in limitation" for every task
        drains the whole ad-bonus pool of the day: measured 747 bonus in
        ~50 requests on a brand-new anonymous account (each paid chapter of
        the catalogue costs 50 -> ~15 chapters/day per account).

        Speed + robustness:
          * submits run `workers` at a time -- the daily limit stays enforced
            upstream, so this only removes the round-trip latency;
          * a task that answered "in limitation" is remembered in `self.spent`
            for the account it was spent on, so a later farm on the same account
            only re-checks what may have reset -- the 60 s sequential farm
            becomes a <1 s no-op instead of blocking the unlock sweep;
          * a socket error is *not* treated as an exhausted limit."""
        import concurrent.futures as _cf
        got, done, dry = 0, [], False
        key = (self.user or {}).get("user_id") or self.device_uuid
        spent = self.spent.setdefault(key, set())
        lst = self.task_list() or []
        rounds = 0
        while rounds < FARM_ROUNDS:
            rounds += 1
            todo = [t.get("id") for t in lst if t.get("id") and t.get("id") not in spent]
            if not todo:
                if rounds > 1:
                    dry = True      # every task is at its limit for this account
                    break
                todo = [t.get("id") for t in lst if t.get("id")]
            claimed, refused = 0, 0

            def _one(tid):
                last = None
                for attempt in range(SUBMIT_RETRIES + 1):
                    try:
                        return tid, (self.task_submit(tid) or {}), None
                    except ShortsWaveError as e:
                        # code 41 "the task is in limitation": that task's limit
                        # for this account/device is spent (measured on a drained
                        # client: every task answers 41). Not worth a retry.
                        return tid, None, getattr(e, "code", None) or "error"
                    except Exception as e:      # noqa: BLE001 — timeout/reset
                        last = str(e)[:60] or "request failed"
                        time.sleep(0.4 * (attempt + 1))
                return tid, None, last

            with _cf.ThreadPoolExecutor(max_workers=max(1, min(workers, len(todo)))) as ex:
                for tid, res, code in ex.map(_one, todo):
                    if code is not None:
                        if code == 41:
                            spent.add(tid)
                            refused += 1
                        continue
                    gain = int(res.get("task_bonus") or 0)
                    if gain:
                        got += gain
                        claimed += gain
                        done.append(tid)
                    else:
                        spent.add(tid)      # accepted, worth 0 from now on
            if not claimed:
                dry = refused >= len(todo)
                break
        return {"bonus": got, "tasks": done, "dry": dry}
