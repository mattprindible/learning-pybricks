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

    uv run python wait_ready.py
}

# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
if [[ "${1:-}" == "--restart-server" ]]; then
    server_restart
    exit 0
fi

# ---------------------------------------------------------------------------
# Full deploy (existing flow, unchanged for now)
# ---------------------------------------------------------------------------
echo "==> Sending hub_disconnect via control port..."
python3 -c "
import socket, sys
try:
    s = socket.socket()
    s.settimeout(3)
    s.connect(('localhost', 8766))
    s.sendall(b'hub_disconnect\n')
    s.recv(16)
    s.close()
    print('hub_disconnect sent')
except Exception as e:
    print(f'WARNING: server.py not reachable ({e})', file=sys.stderr)
    print('WARNING: hub may still be BLE-connected — pybricksdev will likely fail', file=sys.stderr)
    print('Run: uv run python server.py', file=sys.stderr)
" || true
sleep 3

echo "==> Uploading hub program..."
uv run pybricksdev run ble --no-start main.py

echo "==> Building..."
xcodebuild \
  -project "$PROJECT" \
  -scheme bricks \
  -configuration Debug \
  -destination "id=$DEVICE" \
  -derivedDataPath "$DERIVED" \
  build | grep -E "error:|BUILD"

echo "==> Installing..."
xcrun devicectl device install app --device "$DEVICE" "$APP" \
  2>&1 | grep -v "provisioning"

echo "==> Launching..."
xcrun devicectl device process launch \
  --device "$DEVICE" \
  "$BUNDLE" 2>&1 | grep -v "provisioning"

echo "==> Done."
