#!/usr/bin/env python3
"""Sapu bersih reward -> buka semua episode berbayar satu drama.

Kenapa perlu banyak akun: bonus harian ada di akun (per `device_uuid`), bukan
per drama. Satu akun baru bisa mengklaim ~747 bonus dari /task/v2/submit
(server tidak memverifikasi pengerjaan task), dan satu episode berbayar
berharga 50 bonus. Jadi 40 episode = 2000 bonus = minimal 3 akun.

Cara pakai:
    python3 farm_unlock.py --drama 6adb02c255756f63a270005d
    python3 farm_unlock.py --drama <id> --max-accounts 5 --state farm_state.json

Progres disimpan di `--state` supaya bisa dijalankan ulang: episode yang sudah
terbuka dilewati, akun yang sudah dikuras dibuang.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sw_client as S  # noqa: E402

PRICE_DEFAULT = 50


def load_state(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"open": {}, "accounts": [], "spent": 0}


def save_state(path, st):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def new_client(db_dir):
    """Akun baru = device_uuid baru; DB per device supaya sesi tidak tertukar."""
    os.makedirs(db_dir, exist_ok=True)
    return S.Client(db_path=os.path.join(db_dir, "session.db"))


def balance(c):
    return int((c.user_info(refresh=True) or {}).get("bonus") or 0)


def farm_account(db_dir, log=print):
    c = new_client(db_dir)
    u = c.login_anonymous()
    uid = u.get("user_id") or u.get("id")
    before = int(u.get("bonus") or 0)
    log(f"  akun baru {uid} (bonus awal {before})")
    got = c.farm_tasks() or {}
    gained = int((got.get("bonus") if isinstance(got, dict) else got) or 0)
    bal = balance(c)
    log(f"  farm: +{gained} bonus ({len(got.get('tasks', []) if isinstance(got, dict) else [])} klaim) -> saldo {bal}")
    return c, uid, bal


def unlock_with(c, drama_id, chapters, st, state_path, log=print):
    """Belanjakan saldo akun ini ke episode berbayar yang belum terbuka."""
    spent = 0
    for ch in chapters:
        cid = ch["chapter_id"]
        if cid in st["open"]:
            continue
        price = int(ch.get("chapter_price") or ch.get("price") or PRICE_DEFAULT)
        bal = balance(c)
        if bal < price:
            log(f"  saldo {bal} < {price}: berhenti untuk akun ini")
            break
        try:
            r = c.unlock(drama_id, cid, unlock_type="coin", auto_pay=1) or {}
        except S.ShortsWaveError as e:
            log(f"  {ch.get('chapter_index')} gagal: {str(e)[:60]}")
            continue
        play = r.get("play_url") or r.get("play_url_720p") or ""
        if not play:
            log(f"  {ch.get('chapter_index')} tidak dapat play_url (tetap terkunci)")
            continue
        now = balance(c)
        if now >= bal:                       # tidak terpotong -> tidak benar-benar terbuka
            log(f"  {ch.get('chapter_index')} tidak terpotong (bonus tetap {now})")
            continue
        st["open"][cid] = {"index": ch.get("chapter_index"), "play_url": play,
                           "at": int(time.time())}
        st["spent"] = st.get("spent", 0) + (bal - now)
        spent += 1
        log(f"  E{ch.get('chapter_index')} TERBUKA (bonus {bal}->{now})")
        save_state(state_path, st)
    return spent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drama", required=True)
    ap.add_argument("--state", default="farm_state.json")
    ap.add_argument("--db-dir", default=".farm")
    ap.add_argument("--max-accounts", type=int, default=10)
    ap.add_argument("--lang", default="en")
    ap.add_argument("--region", default="US")
    args = ap.parse_args()

    st = load_state(args.state)
    c0 = new_client(os.path.join(args.db_dir, "probe"))
    chapters = c0.chapters(args.drama) or []
    paid = [ch for ch in chapters if not ch.get("is_free")]
    free = [ch for ch in chapters if ch.get("is_free")]
    print(f"drama {args.drama}: {len(chapters)} episode, {len(free)} gratis, {len(paid)} berbayar")
    print(f"sudah terbuka tersimpan: {len(st['open'])}")

    need = [ch for ch in paid if ch["chapter_id"] not in st["open"]]
    price = int(next((ch.get("chapter_price") for ch in paid), PRICE_DEFAULT) or PRICE_DEFAULT)
    print(f"perlu dibuka: {len(need)} x {price} = {len(need) * price} bonus\n")
    if not need:
        print("semua episode berbayar sudah terbuka.")
        return 0

    used = 0
    for i in range(args.max_accounts):
        left = [ch for ch in paid if ch["chapter_id"] not in st["open"]]
        if not left:
            break
        used += 1
        db_dir = os.path.join(args.db_dir, f"acct{i + 1:02d}")
        print(f"[akun {used}]")
        try:
            c, uid, bal = farm_account(db_dir, log=print)
        except Exception as e:  # noqa: BLE001 — satu akun gagal tidak menghentikan sisanya
            print(f"  akun gagal dibuat: {str(e)[:80]}")
            continue
        st["accounts"].append({"user_id": uid, "farm_balance": bal, "dir": db_dir})
        save_state(args.state, st)
        n = unlock_with(c, args.drama, left, st, args.state, log=print)
        print(f"  -> {n} episode terbuka dari akun ini\n")

    done = [ch for ch in paid if ch["chapter_id"] in st["open"]]
    print(f"HASIL: {len(done)}/{len(paid)} episode berbayar terbuka "
          f"({len(st['open'])}/{len(chapters)} total termasuk gratis), "
          f"{st.get('spent', 0)} bonus terpakai, {used} akun dipakai")
    if len(done) < len(paid):
        print("masih ada yang terkunci -- jalankan ulang script ini (state disimpan) "
              "atau naikkan --max-accounts")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
