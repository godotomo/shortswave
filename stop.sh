#!/data/data/com.termux/files/usr/bin/bash
# stop.sh -- hentikan ShortsWave server yang dijalankan oleh start.sh.
#
# Pemakaian:
#   ./stop.sh          # port 8000 (mengikuti PORT bila di-set)
#   PORT=8011 ./stop.sh
#   ./stop.sh --all    # hentikan semua instance server.py di work/ (semua PORT)
#
# Hanya proses milik proyek ini (cmdline *server.py* dengan cwd = work/) yang
# dibunuh -- server proyek lain di mesin ini tidak disentuh.

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$ROOT/work"
PORT="${PORT:-8000}"
PIDFILE="${PIDFILE:-$WORK/server-$PORT.pid}"

log() { printf '%s\n' "$*"; }

is_ours() { # $1 = pid  -> 0 bila itu server.py proyek ini
  local pid="$1" cmd cwd
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)" || return 1
  case "$cmd" in *serve[r].py*) ;; *) return 1 ;; esac
  cwd="$(readlink "/proc/$pid/cwd" 2>/dev/null)" || return 1
  [ "$cwd" = "$WORK" ] || return 1
  return 0
}

kill_pid() { # $1 = pid, $2 = label
  local pid="$1" label="$2" i
  if ! is_ours "$pid"; then
    return 1
  fi
  log "Menghentikan PID $pid ($label) ..."
  kill -TERM "$pid" 2>/dev/null
  for i in $(seq 1 20); do            # tunggu maks ~10 s
    sleep 0.5
    kill -0 "$pid" 2>/dev/null || break
  done
  if kill -0 "$pid" 2>/dev/null; then
    log "  masih hidup, kirim SIGKILL"
    kill -KILL "$pid" 2>/dev/null
    sleep 0.5
  fi
  kill -0 "$pid" 2>/dev/null && log "  GAGAL menghentikan PID $pid" || log "  berhenti."
  return 0
}

stopped=0
targets=""
port_of() { # $1 = pid -> nilai PORT dari environ proses
  tr '\0' '\n' < "/proc/$1/environ" 2>/dev/null | sed -n 's/^PORT=//p' | head -1
}

if [ "${1:-}" = "--all" ]; then
  for d in /proc/[0-9]*; do
    pid="${d#/proc/}"
    if is_ours "$pid"; then targets="$targets $pid"; fi
  done
else
  if [ -f "$PIDFILE" ]; then
    pid="$(tr -dc '0-9' < "$PIDFILE")"
    targets="$pid"
  fi
  # fallback: pidfile hilang / server dijalankan manual -> cocokkan PORT proses
  if [ -z "$targets" ] && command -v pgrep >/dev/null 2>&1; then
    for pid in $(pgrep -f 'serve[r].py' 2>/dev/null); do
      is_ours "$pid" || continue
      p="$(port_of "$pid")"; p="${p:-8000}"
      [ "$p" = "$PORT" ] && targets="$targets $pid"
    done
    [ -n "$targets" ] && log "PID ditemukan dari pemindaian proses (pidfile hilang)."
  fi
fi

for pid in $targets; do
  p="$(port_of "$pid")"; p="${p:-8000}"
  if [ -f "$PIDFILE" ] && [ "$(tr -dc '0-9' < "$PIDFILE")" = "$pid" ]; then
    label="$PIDFILE"
  else
    label="python3 server.py, PORT=$p"
  fi
  if kill_pid "$pid" "$label"; then stopped=$((stopped+1)); fi
done

rm -f "$PIDFILE"

# hapus pidfile milik proyek ini -- untuk --all semua, selain itu hanya port ini
if [ "${1:-}" = "--all" ]; then
  for f in "$WORK"/server-*.pid; do [ -e "$f" ] && rm -f "$f"; done
else
  rm -f "$WORK/server-$PORT.pid"
fi

if [ "$stopped" -gt 0 ]; then
  log "Selesai: $stopped instance dihentikan."
else
  log "Tidak ada server ShortsWave yang berjalan untuk port $PORT."
  log "(Instance di port lain: ./stop.sh --all)"
fi
