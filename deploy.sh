#!/usr/bin/env bash
set -euo pipefail

DEVICE="DA2DBD4E-6935-5372-8D3D-E3A5372ABBD5"
PROJECT="bricks/bricks.xcodeproj"
DERIVED="/tmp/bricks-build"
APP="$DERIVED/Build/Products/Debug-iphoneos/bricks.app"
BUNDLE="haha.computer.bricks"
PID_FILE=".server.pid"
SERVER_LOG="server.log"
CONTROL_PORT=8766

log() { echo "DEPLOY:$*"; }

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
    uv run python server.py >> "$SERVER_LOG" 2>&1 &

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
            log "server:error reason=TIMEOUT_CONTROL_PORT"
            exit 1
        fi
        sleep 0.3
    done

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
# ios_deploy — build, install, and launch the iOS app.
# ---------------------------------------------------------------------------
ios_deploy() {
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
if [[ "${1:-}" == "--restart-server" ]]; then
    server_restart
    uv run python wait_ready.py
    exit 0
fi

if [[ "${1:-}" == "--hub" ]]; then
    hub_deploy
    uv run python wait_ready.py
    exit 0
fi

if [[ "${1:-}" == "--ios" ]]; then
    ios_deploy
    uv run python wait_ready.py
    exit 0
fi

# ---------------------------------------------------------------------------
# Full deploy
# ---------------------------------------------------------------------------
CHANGED="${DEPLOY_CHANGED:-all}"
START_TS=$(date +%s)
log "start changed=$CHANGED"

server_restart
hub_deploy
ios_deploy
uv run python wait_ready.py

ELAPSED=$(( $(date +%s) - START_TS ))
log "success elapsed=${ELAPSED}s"
