#!/data/data/com.termux/files/usr/bin/bash
# start.sh -- jalankan ShortsWave server (Flask API + HLS proxy + UI) di background.
#
# Pemakaian:
#   ./start.sh              # port 8000, DB work/sw.db
#   PORT=8011 ./start.sh    # port lain
#   SW_DB=sw_test.db ./start.sh
#   FOREGROUND=1 ./start.sh # jangan background (log ke terminal)
#   SW_BUILD_UI=0 ./start.sh # jangan bangun UI otomatis walau dist belum ada
#
# Lingkungan (opsional):
#   PORT     port HTTP            (default 8000)
#   HOST     bind address         (default 0.0.0.0)
#   SW_DB    path DB SQLite       (default work/sw.db)
#   SW_H264  1|0 transcode H.264   (default 1: fallback untuk browser tanpa HEVC)
#   SW_H264_HEIGHT / SW_H264_CRF / SW_H264_PRESET
#            tinggi & kualitas hasil transcode (default 480 / 26 / ultrafast)
#   SW_H264_CACHE_DAYS  umur cache transcode dalam hari (default 7, 0 = simpan)
#   SW_FARM_ACCOUNTS  akun anonim minimum per sweep unlock (default 4; 0 = matikan)
#   SW_FARM_MAX_ACCOUNTS  batas keras akun per sweep (default 12)
#            satu akun ~747 bonus = tepat 14 episode (50 each), jadi 61 episode
#            berbayar butuh 5 akun; sweep menaikkan budget sendiri sesuai ukuran drama
#   PIDFILE  file PID             (default work/server.pid dihitung dari PORT)
#   LOGFILE  file log             (default work/server-<PORT>.log)

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$ROOT/work"
PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"
PIDFILE="${PIDFILE:-$WORK/server-$PORT.pid}"
LOGFILE="${LOGFILE:-$WORK/server-$PORT.log}"
export PORT HOST
export SW_DB="${SW_DB:-$WORK/sw.db}"
export SW_H264="${SW_H264:-1}"
export SW_H264_HEIGHT="${SW_H264_HEIGHT:-480}"
export SW_H264_CRF="${SW_H264_CRF:-26}"
export SW_H264_PRESET="${SW_H264_PRESET:-ultrafast}"
export SW_H264_CACHE_DAYS="${SW_H264_CACHE_DAYS:-7}"
export SW_FARM_ACCOUNTS="${SW_FARM_ACCOUNTS:-4}"
export SW_FARM_MAX_ACCOUNTS="${SW_FARM_MAX_ACCOUNTS:-12}"

log() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# --- sanity ----------------------------------------------------------------
[ -f "$WORK/server.py" ] || die "work/server.py tidak ditemukan (root=$ROOT)"
command -v python3 >/dev/null 2>&1 || die "python3 tidak ada di PATH"
python3 -c 'import flask, requests' 2>/dev/null \
  || die "dependency kurang: pip install flask requests"

# ffmpeg hanya dibutuhkan untuk fallback H.264 (SW_H264=1)
if [ "${SW_H264:-1}" != "0" ] && ! command -v ffmpeg >/dev/null 2>&1; then
  log "PERINGATAN: ffmpeg tidak ada -- fallback H.264 nonaktif (browser tanpa HEVC akan hitam)."
  log "           pasang: pkg install ffmpeg    atau jalankan dengan SW_H264=0"
fi

# UI statis. Kalau belum dibangun (clone baru), bangun sendiri supaya
# "git clone && ./start.sh" langsung menyajikan aplikasi yang sama seperti di
# mesin pembuatnya. Butuh node/npm; tanpa itu server tetap jalan sebagai API.
if [ ! -f "$WORK/ui/dist/index.html" ]; then
  if [ "${SW_BUILD_UI:-1}" != "0" ] && command -v npm >/dev/null 2>&1; then
    log "UI belum dibangun -- membangun work/ui (npm install && npm run build)…"
    UIBUILD="$WORK/ui-build.log"   # jangan /tmp: di Termux sering tidak bisa ditulis
    if ( cd "$WORK/ui" \
         && { [ -d node_modules ] || npm install --no-audit --no-fund >/dev/null 2>&1; } \
         && npm run build >"$UIBUILD" 2>&1 ); then
      log "UI siap: work/ui/dist/index.html"
    else
      log "PERINGATAN: gagal membangun UI (lihat $UIBUILD)."
      log "           jalankan manual: cd work/ui && npm install && npm run build"
    fi
  else
    log "PERINGATAN: work/ui/dist/index.html belum ada -- UI tidak akan disajikan."
    log "           bangun dengan: cd work/ui && npm install && npm run build"
  fi
fi

# --- sudah jalan? ----------------------------------------------------------
is_ours() { # $1 = pid
  local pid="$1"
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  local cmd
  cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)" || return 1
  case "$cmd" in *serve[r].py*) return 0 ;; esac
  return 1
}

if [ -f "$PIDFILE" ]; then
  old="$(cat "$PIDFILE" 2>/dev/null | tr -dc '0-9')"
  if is_ours "$old"; then
    log "Server sudah berjalan (PID $old, port $PORT). Pakai ./stop.sh dulu."
    exit 0
  fi
  rm -f "$PIDFILE"
fi

# port sudah dipakai proses lain? (jangan sampai stop.sh membunuh app lain)
if command -v python3 >/dev/null 2>&1; then
  python3 - "$PORT" <<'PY' || die "port $PORT sudah dipakai proses lain. Set PORT=... untuk port lain."
import socket, sys
p = int(sys.argv[1])
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(("0.0.0.0", p))
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
fi

# --- jalankan --------------------------------------------------------------
cd "$WORK" || die "tidak bisa masuk $WORK"

if [ "${FOREGROUND:-0}" = "1" ]; then
  log "Menjalankan di foreground: port $PORT, DB $SW_DB"
  exec python3 server.py
fi

: > "$LOGFILE"
nohup python3 server.py >>"$LOGFILE" 2>&1 &
pid=$!
echo "$pid" > "$PIDFILE"

# tunggu /api/health siap (maks ~15 s)
ok=0
for _ in $(seq 1 30); do
  sleep 0.5
  if ! kill -0 "$pid" 2>/dev/null; then
    log "Server mati saat start. 40 baris terakhir $LOGFILE:"
    tail -n 40 "$LOGFILE"
    rm -f "$PIDFILE"
    exit 1
  fi
  if curl -fsS -m 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then ok=1; break; fi
done

if [ "$ok" = "1" ]; then
  log "ShortsWave server jalan (PID $pid)"
  log "  UI     : http://127.0.0.1:$PORT/"
  log "  health : http://127.0.0.1:$PORT/api/health"
  log "  LAN    : http://$(hostname -I 2>/dev/null | awk '{print $1}'):$PORT/"
  log "  DB     : $SW_DB"
  log "  log    : $LOGFILE"
  log "  PID    : $PIDFILE"
else
  log "Server jalan (PID $pid) tapi /api/health belum merespons. Cek: tail -f $LOGFILE"
fi
