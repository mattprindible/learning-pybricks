#!/usr/bin/env bash
set -euo pipefail

DEVICE="DA2DBD4E-6935-5372-8D3D-E3A5372ABBD5"
PROJECT="bricks/bricks.xcodeproj"
DERIVED="/tmp/bricks-build"
APP="$DERIVED/Build/Products/Debug-iphoneos/bricks.app"
BUNDLE="haha.computer.bricks"
PID_FILE=".server.pid"
SERVER_LOG="server.log"
LOCK_FILE=".deploy-lock"
CONTROL_PORT=8766

speak()      { osascript -e "say \"$*\" using \"Zarvox\"" & }
speak_wait() { osascript -e "say \"$*\" using \"Zarvox\""; }

log() {
    echo "DEPLOY:$*"
    if [[ "$*" == *":error"* ]]; then
        local reason
        reason=$(echo "$*" | grep -oE 'reason=[^ ]+' | sed 's/reason=//' | tr '_' ' ' | tr '[:upper:]' '[:lower:]')
        speak "Deploy failed. ${reason:-check logs}"
    fi
}

acquire_lock() {
    if [[ -f "$LOCK_FILE" ]]; then
        local holder
        holder=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
        if [[ -n "$holder" ]] && kill -0 "$holder" 2>/dev/null; then
            log "error reason=DEPLOY_ALREADY_RUNNING pid=$holder hint=another_deploy_is_in_progress"
            exit 1
        fi
        log "warning reason=STALE_LOCK_CLEARED pid=$holder"
    fi
    echo $$ > "$LOCK_FILE"
    trap 'rm -f "$LOCK_FILE"' EXIT
}

# ---------------------------------------------------------------------------
# server_restart — stop running server via PID file, start fresh, poll ready.
# ---------------------------------------------------------------------------
server_restart() {
    local start_ts elapsed pid deadline
    start_ts=$(date +%s)

    if [[ -f "$PID_FILE" ]]; then
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            log "server:stop pid=$pid"
            kill -TERM "$pid"
            deadline=$(( $(date +%s) + 5 ))
            while kill -0 "$pid" 2>/dev/null && [[ $(date +%s) -lt $deadline ]]; do
                sleep 0.2
            done
            if kill -0 "$pid" 2>/dev/null; then
                log "server:sigkill pid=$pid"
                kill -KILL "$pid" 2>/dev/null || true
                sleep 0.5
            fi
        fi
        rm -f "$PID_FILE"
    fi

    log "server:start"
    [[ -f "$SERVER_LOG" ]] && mv "$SERVER_LOG" "${SERVER_LOG}.prev"
    uv run python server.py > "$SERVER_LOG" 2>&1 &

    deadline=$(( $(date +%s) + 15 ))
    while ! python3 -c "
import socket, sys
s = socket.socket()
s.settimeout(1)
try:
    s.connect(('localhost', $CONTROL_PORT))
    s.close()
except Exception:
    sys.exit(1)
" 2>/dev/null; do
        if [[ $(date +%s) -gt $deadline ]]; then
            log "server:error reason=TIMEOUT_CONTROL_PORT hint=check_server.log"
            exit 1
        fi
        sleep 0.3
    done

    if [[ ! -f "$PID_FILE" ]]; then
        log "server:error reason=SERVER_PORT_CONFLICT hint=kill_existing_server_on_ports_8765_8766"
        exit 1
    fi

    elapsed=$(( $(date +%s) - start_ts ))
    log "server:ok pid=$(cat $PID_FILE) elapsed=${elapsed}s"
}

# ---------------------------------------------------------------------------
# hub_deploy — release BLE, upload main.py via pybricksdev.
# ---------------------------------------------------------------------------
hub_deploy() {
    local start_ts elapsed
    start_ts=$(date +%s)

    log "hub:disconnect"
    python3 -c "
import socket, sys
s = socket.socket()
s.settimeout(3)
try:
    s.connect(('localhost', $CONTROL_PORT))
    s.sendall(b'hub_disconnect\n')
    s.recv(16)
    s.close()
except Exception as e:
    print(f'DEPLOY:hub:error reason=SERVER_NOT_REACHABLE detail={e}')
    sys.exit(1)
"
    sleep 3

    log "hub:upload"
    if ! uv run pybricksdev run ble --no-start main.py; then
        log "hub:error reason=PYBRICKSDEV_FAILED"
        exit 1
    fi

    elapsed=$(( $(date +%s) - start_ts ))
    log "hub:ok elapsed=${elapsed}s"
}

# ---------------------------------------------------------------------------
# ios_build — compile only; safe to run while hub is still BLE-connected.
# ---------------------------------------------------------------------------
ios_build() {
    local start_ts elapsed
    start_ts=$(date +%s)

    log "ios:build"
    if ! xcodebuild \
        -project "$PROJECT" \
        -scheme bricks \
        -configuration Debug \
        -destination "id=$DEVICE" \
        -derivedDataPath "$DERIVED" \
        build 2>&1 | grep -E "error:|BUILD"; then
        log "ios:error reason=XCODEBUILD_FAILED"
        exit 1
    fi

    elapsed=$(( $(date +%s) - start_ts ))
    log "ios:build:ok elapsed=${elapsed}s"
}

# ---------------------------------------------------------------------------
# ios_install_launch — install and launch the pre-built app.
# ---------------------------------------------------------------------------
ios_install_launch() {
    local start_ts elapsed
    start_ts=$(date +%s)

    log "ios:install"
    if ! xcrun devicectl device install app --device "$DEVICE" "$APP" \
        2>&1 | grep -v "provisioning"; then
        log "ios:error reason=INSTALL_FAILED"
        exit 1
    fi

    log "ios:launch"
    if ! xcrun devicectl device process launch \
        --device "$DEVICE" \
        "$BUNDLE" 2>&1 | grep -v "provisioning"; then
        log "ios:error reason=LAUNCH_FAILED"
        exit 1
    fi

    elapsed=$(( $(date +%s) - start_ts ))
    log "ios:ok elapsed=${elapsed}s"
}

# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
if [[ "${1:-}" == "--stop" ]]; then
    if [[ ! -f "$PID_FILE" ]]; then
        log "server:stop skipped reason=NOT_RUNNING"
        exit 0
    fi

    pid=$(cat "$PID_FILE")
    if ! kill -0 "$pid" 2>/dev/null; then
        log "server:stop skipped reason=PROCESS_GONE pid=$pid"
        rm -f "$PID_FILE"
        exit 0
    fi

    speak_wait "Shutting down"
    log "server:stop pid=$pid"
    kill -TERM "$pid"

    deadline=$(( $(date +%s) + 5 ))
    while kill -0 "$pid" 2>/dev/null && [[ $(date +%s) -lt $deadline ]]; do
        sleep 0.2
    done

    if kill -0 "$pid" 2>/dev/null; then
        log "server:sigkill pid=$pid"
        kill -KILL "$pid" 2>/dev/null || true
        sleep 0.5
    fi

    rm -f "$PID_FILE"

    # Verify port is actually free
    if python3 -c "
import socket, sys
s = socket.socket()
s.settimeout(1)
try:
    s.connect(('localhost', $CONTROL_PORT))
    s.close()
    sys.exit(0)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
        log "server:error reason=PORT_STILL_BOUND hint=check_for_zombie_process"
        speak "Shutdown failed. port still bound"
        exit 1
    fi

    log "server:stopped pid=$pid"
    speak "Server stopped"
    exit 0
fi

if [[ "${1:-}" == "--restart-server" ]]; then
    acquire_lock
    speak_wait "${DEPLOY_MESSAGE:-Restarting server}"
    server_restart
    uv run python wait_ready.py
    speak "System ready"
    exit 0
fi

if [[ "${1:-}" == "--hub" ]]; then
    acquire_lock
    hub_deploy
    uv run python wait_ready.py
    speak "System ready"
    exit 0
fi

if [[ "${1:-}" == "--ios" ]]; then
    acquire_lock
    ios_build
    ios_install_launch
    uv run python wait_ready.py
    speak "System ready"
    exit 0
fi

# ---------------------------------------------------------------------------
# Full deploy — build iOS first (hub BLE still held), then hub upload,
# then install+launch (hub idle only for the short ~5s install window).
# ---------------------------------------------------------------------------
acquire_lock
CHANGED="${DEPLOY_CHANGED:-all}"
START_TS=$(date +%s)
log "start changed=$CHANGED"
speak_wait "${DEPLOY_MESSAGE:-Deploying}"

server_restart
uv run python wait_ready.py --phone  # iOS must be connected before hub_disconnect
ios_build                            # slow step; hub still BLE-connected to old app
hub_deploy                           # hub idle window starts here
ios_install_launch                   # ~5s — well within hub auto-shutoff threshold
uv run python wait_ready.py

ELAPSED=$(( $(date +%s) - START_TS ))
log "success elapsed=${ELAPSED}s"
speak "System ready"
