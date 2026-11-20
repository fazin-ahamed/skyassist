#!/usr/bin/env bash
# SkyAssist demo launcher. Starts ONE process: the API service on 127.0.0.1:8000.
# The API also serves both browser UIs from web/out: the passenger kiosk at / and the
# coordinator dashboard at /staff/. Usage: ./run_demo.sh [up|reset|status|down] [--dry-run]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$ROOT"

API_PORT=8000
API_CLOCK_URL="http://127.0.0.1:$API_PORT/clock"
READY_TIMEOUT=60
STOP_TIMEOUT=10
PY_VERSION="${SKYASSIST_PYTHON:-3.14}"
VENV_PY="$ROOT/.venv/bin/python"
DEMO_DB="${SKYASSIST_DB_PATH:-$ROOT/data/skyassist-demo.sqlite3}"
WEB_OUT="$ROOT/web/out"
RUN_DIR="$ROOT/run"
LOG_DIR="$RUN_DIR/logs"
DEMO_COORDINATOR_TOKEN="demo-coordinator"  # code default in demo mode (skyassist/service.py)
DRY_RUN=0

export SKYASSIST_MODE=demo
export SKYASSIST_SERVICE_URL="http://127.0.0.1:$API_PORT"
export UV_PYTHON_DOWNLOADS="${UV_PYTHON_DOWNLOADS:-never}"
# Frozen demo clock: speed 0 holds C01 at 06:00Z so the 21-29 min transfer and
# 6 min margin do not drift during a rehearsal. Set SKYASSIST_CLOCK_SPEED=1 to run live time.
export SKYASSIST_CLOCK_START="${SKYASSIST_CLOCK_START:-C01}"
export SKYASSIST_CLOCK_SPEED="${SKYASSIST_CLOCK_SPEED:-0}"

usage() {
  cat <<EOF
Usage: ./run_demo.sh [up|reset|status|down] [--dry-run]

  up         (default) sync .venv, seed the demo DB if missing, build web/out if missing,
             then start the API on 127.0.0.1:$API_PORT. The API serves the kiosk at /
             and the coordinator dashboard at /staff/. Ctrl-C stops it.
  reset      rebuild the seeded demo DB. Refuses while the API is running.
  status     show process, port and DB state. Exit 1 unless the API runs.
  down       stop the process recorded in run/api.pid.
  --dry-run  print the commands and URLs that would run. Changes nothing.
EOF
}

die() { printf 'run_demo: %s\n' "$*" >&2; exit 1; }

# Home uv cache may be read-only (sandboxes, locked-down laptops). Fall back to TMPDIR.
choose_uv_cache() {
  [[ -n "${UV_CACHE_DIR:-}" ]] && return 0
  local home_cache="${XDG_CACHE_HOME:-$HOME/.cache}/uv"
  if [[ -d "$home_cache" ]]; then
    if [[ -w "$home_cache" ]]; then return 0; fi
  elif [[ -w "$(dirname "$home_cache")" ]]; then
    return 0
  fi
  export UV_CACHE_DIR="${TMPDIR:-/tmp}/uv-cache"
}

# argv as a shell line (leading space). Args with spaces are quoted so the line is exact.
quote_argv() {
  local arg out=""
  for arg in "$@"; do
    case "$arg" in
      *[[:space:]]*) out+=" '$arg'" ;;
      *) out+=" $arg" ;;
    esac
  done
  printf '%s' "$out"
}

# Print the command, then run it unless --dry-run.
run() {
  echo "+$(quote_argv "$@")"
  if (( ! DRY_RUN )); then "$@"; fi
}

show_env() {
  echo "env: SKYASSIST_MODE=$SKYASSIST_MODE SKYASSIST_SERVICE_URL=$SKYASSIST_SERVICE_URL"
  echo "env: UV_PYTHON_DOWNLOADS=$UV_PYTHON_DOWNLOADS UV_CACHE_DIR=${UV_CACHE_DIR:-<uv default>}"
  echo "env: SKYASSIST_CLOCK_START=$SKYASSIST_CLOCK_START SKYASSIST_CLOCK_SPEED=$SKYASSIST_CLOCK_SPEED"
}

# TCP connect to 127.0.0.1:PORT succeeds only if something listens there.
port_open() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

# Ready means a 2xx or 3xx answer. Connection errors and 4xx/5xx are not ready.
# Proxy is bypassed so 127.0.0.1 is never sent to an outside proxy.
http_answers() {
  "$VENV_PY" -c '
import sys, urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    opener.open(sys.argv[1], timeout=2).close()
except OSError:
    sys.exit(1)
' "$1"
}

pid_of() {
  local file="$RUN_DIR/$1.pid"
  [[ -f "$file" ]] && cat "$file"
}

proc_alive() {
  local pid
  pid="$(pid_of "$1")" && kill -0 "$pid" 2>/dev/null
}

# Guards against killing a reused PID that now belongs to something else.
is_ours() {
  local args
  args="$(ps -o args= -p "$1" 2>/dev/null || true)"
  [[ "$args" == *uvicorn* ]]
}

need_uv() {
  if command -v uv >/dev/null 2>&1; then return 0; fi
  if (( DRY_RUN )); then
    echo "note: uv not found on PATH; a real run stops here" >&2
    return 0
  fi
  die "uv not found on PATH. Install uv, then run again."
}

check_venv() {
  [[ -x "$VENV_PY" ]] || die "no .venv after uv sync"
  local got
  got="$("$VENV_PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  [[ "$got" == "$PY_VERSION" ]] || die ".venv runs Python $got, need $PY_VERSION (set SKYASSIST_PYTHON to override)"
  "$VENV_PY" -c 'import fastapi, pydantic, segno, uvicorn' \
    || die "required packages missing from .venv. Run ./run_demo.sh up once with network access."
}

# Offline when already synced: --frozen never re-resolves the lock.
preflight() {
  need_uv
  if [[ -f uv.lock ]]; then
    run uv sync --frozen --python "$PY_VERSION" \
      || die "uv sync failed. Python $PY_VERSION must exist locally (UV_PYTHON_DOWNLOADS=never)."
  else
    run uv sync --python "$PY_VERSION" || die "uv sync failed."
  fi
  if (( ! DRY_RUN )); then check_venv; fi
}

build_db() {
  run "$VENV_PY" -c 'from skyassist.seed import build_demo_db; print(build_demo_db())' \
    || die "seeding the demo DB failed. See the traceback above."
}

ensure_db() {
  if [[ -f "$DEMO_DB" ]]; then
    echo "demo DB present: $DEMO_DB"
  elif (( DRY_RUN )); then
    echo "demo DB missing, would seed: $DEMO_DB"
    build_db
  else
    echo "demo DB missing, seeding: $DEMO_DB"
    build_db
  fi
}

# The API serves the BoardUI front end from web/out. Build it once if it is missing.
ensure_web() {
  if [[ -f "$WEB_OUT/index.html" ]]; then
    echo "front end present: $WEB_OUT"
    return 0
  fi
  if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    if (( DRY_RUN )); then
      echo "note: web/out is missing and node or npm is not on PATH; a real up stops here" >&2
      return 0
    fi
    die "web/out is missing and node or npm is not on PATH. Install Node 22, then run: npm --prefix web ci && npm --prefix web run build"
  fi
  echo "front end missing: building web/out once (needs the npm registry and Google Fonts)"
  run npm --prefix web ci || die "npm ci failed. Behind a proxy, retry with NODE_USE_ENV_PROXY=1."
  run npm --prefix web run build || die "front end build failed. Read the output above."
  if (( ! DRY_RUN )) && [[ ! -f "$WEB_OUT/index.html" ]]; then
    die "build finished but $WEB_OUT/index.html is missing."
  fi
}

assert_stopped() {
  if proc_alive api; then
    if (( DRY_RUN )); then
      echo "note: api is running (pid $(pid_of api)); a real up would refuse"
    else
      die "api is already running (pid $(pid_of api)). Run ./run_demo.sh down first."
    fi
  elif (( ! DRY_RUN )); then
    rm -f "$RUN_DIR/api.pid"
  fi
}

assert_port_free() {
  if port_open "$API_PORT"; then
    if (( DRY_RUN )); then
      echo "note: port $API_PORT already answers; a real up would refuse"
    else
      die "port $API_PORT already answers. Stop the process holding it, then run again."
    fi
  fi
}

start_proc() {  # name, command...
  local name="$1"; shift
  local log="$LOG_DIR/$name.log"
  if (( DRY_RUN )); then
    echo "+$(quote_argv "$@") >> $log 2>&1 &"
    return 0
  fi
  printf '=== %s start ===\n' "$(date '+%Y-%m-%dT%H:%M:%S')" >>"$log"
  "$@" >>"$log" 2>&1 &
  echo "$!" >"$RUN_DIR/$name.pid"
  echo "+ started $name (pid $!), log $log"
}

wait_ready() {
  local deadline=$((SECONDS + READY_TIMEOUT))
  until http_answers "$API_CLOCK_URL"; do
    proc_alive api || die "api exited before it answered at $API_CLOCK_URL. Read $LOG_DIR/api.log"
    if (( SECONDS >= deadline )); then
      die "api did not answer at $API_CLOCK_URL within ${READY_TIMEOUT}s. Read $LOG_DIR/api.log"
    fi
    sleep 0.5
  done
  echo "ready: api at $API_CLOCK_URL"
}

# The coordinator link carries the demo token only in demo mode. When SKYASSIST_COORDINATOR_TOKEN
# is set, the token is a real credential: the link has no fragment and the token is never printed.
print_urls() {
  echo "  Passenger kiosk   http://127.0.0.1:$API_PORT/"
  if [[ -n "${SKYASSIST_COORDINATOR_TOKEN:-}" ]]; then
    echo "  Coordinator       http://127.0.0.1:$API_PORT/staff/"
    echo "                    sign in with the token in SKYASSIST_COORDINATOR_TOKEN (not printed here)"
  else
    echo "  Coordinator       http://127.0.0.1:$API_PORT/staff/#token=$DEMO_COORDINATOR_TOKEN"
  fi
  echo "  API clock         $API_CLOCK_URL"
}

# Stops the recorded process. Never fails: called from the EXIT trap.
stop_proc() {
  local name="$1" pid
  pid="$(pid_of "$name")" || return 0
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "$name: pid $pid already exited"
  elif ! is_ours "$pid"; then
    echo "$name: pid $pid is not the SkyAssist API; left running"
  elif (( DRY_RUN )); then
    echo "+ kill -TERM $pid   # stop $name"
    return 0
  else
    # Remove the PID file before signalling, so the monitor reads this stop as requested.
    rm -f "$RUN_DIR/$name.pid"
    kill -TERM "$pid" 2>/dev/null || true
    local tries=0
    while kill -0 "$pid" 2>/dev/null && (( tries < STOP_TIMEOUT * 2 )); do
      sleep 0.5
      tries=$((tries + 1))
    done
    if kill -0 "$pid" 2>/dev/null; then
      kill -KILL "$pid" 2>/dev/null || true
      echo "$name: pid $pid ignored SIGTERM and was killed"
    fi
    echo "$name: stopped (pid $pid)"
  fi
  if (( ! DRY_RUN )); then rm -f "$RUN_DIR/$name.pid"; fi
  return 0
}

cleanup() {
  stop_proc api
}

# Exits 1 if the API dies, so the EXIT trap stops it. Exits 0 after `down`.
monitor() {
  while :; do
    if ! proc_alive api; then
      # `down` removes the PID file before it signals the API. A missing file means a requested stop.
      if [[ -f "$RUN_DIR/api.pid" ]]; then
        die "api stopped unexpectedly. Read $LOG_DIR/api.log."
      fi
      echo "stopped by ./run_demo.sh down"
      exit 0
    fi
    sleep 1
  done
}

cmd_up() {
  if (( DRY_RUN )); then show_env; fi
  assert_stopped
  preflight
  assert_port_free
  ensure_db
  ensure_web
  if (( DRY_RUN )); then
    start_proc api "$VENV_PY" -m uvicorn skyassist.service_read:app_from_env --factory \
      --host 127.0.0.1 --port "$API_PORT" --timeout-graceful-shutdown 5 --no-access-log
    echo "wait: $API_CLOCK_URL answers HTTP within ${READY_TIMEOUT}s"
    echo "urls:"
    print_urls
    return 0
  fi

  mkdir -p "$LOG_DIR"
  # Traps go in before the launch so a signal cannot orphan the started process.
  trap 'exit 130' INT
  trap 'exit 143' TERM
  trap 'exit 129' HUP
  trap cleanup EXIT

  start_proc api "$VENV_PY" -m uvicorn skyassist.service_read:app_from_env --factory \
    --host 127.0.0.1 --port "$API_PORT" --timeout-graceful-shutdown 5 --no-access-log
  wait_ready
  echo
  echo "SkyAssist demo is up (SKYASSIST_MODE=demo, DB $DEMO_DB)"
  print_urls
  echo "Log: $LOG_DIR/api.log   Stop: Ctrl-C here, or ./run_demo.sh down from another terminal."
  monitor
}

cmd_reset() {
  if (( DRY_RUN )); then show_env; fi
  if proc_alive api || port_open "$API_PORT"; then
    if (( DRY_RUN )); then
      echo "note: API answers on $API_PORT; a real reset would refuse"
    else
      die "API is running on 127.0.0.1:$API_PORT. Run ./run_demo.sh down first."
    fi
  fi
  preflight
  build_db
  if (( ! DRY_RUN )); then echo "reset: demo DB rebuilt at $DEMO_DB"; fi
}

cmd_status() {
  local state port_state rc=0
  if proc_alive api; then
    state="running (pid $(pid_of api))"
  else
    state="stopped"
    rc=1
  fi
  if port_open "$API_PORT"; then port_state="port open"; else port_state="port closed"; fi
  printf '%-7s %-22s %-12s %s\n' api "$state" "$port_state" "$API_CLOCK_URL"
  if [[ -f "$DEMO_DB" ]]; then
    echo "demo DB: $DEMO_DB"
  else
    echo "demo DB: missing (./run_demo.sh up creates it)"
    rc=1
  fi
  exit "$rc"
}

cmd_down() {
  if [[ -f "$RUN_DIR/api.pid" ]]; then
    stop_proc api
  else
    echo "nothing to stop: run/api.pid is missing"
  fi
  return 0
}

COMMAND=""
for arg in "$@"; do
  case "$arg" in
    up|reset|status|down)
      [[ -z "$COMMAND" ]] || { echo "run_demo: give one subcommand" >&2; exit 2; }
      COMMAND="$arg"
      ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "run_demo: unknown subcommand or option: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

choose_uv_cache
case "${COMMAND:-up}" in
  up) cmd_up ;;
  reset) cmd_reset ;;
  status) cmd_status ;;
  down) cmd_down ;;
esac
