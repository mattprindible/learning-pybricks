#!/usr/bin/env bash
set -euo pipefail

DEVICE="DA2DBD4E-6935-5372-8D3D-E3A5372ABBD5"
PROJECT="bricks/bricks.xcodeproj"
DERIVED="/tmp/bricks-build"
APP="$DERIVED/Build/Products/Debug-iphoneos/bricks.app"
BUNDLE="haha.computer.bricks"

# --- Tell iOS app to stop hub program and release BLE ---
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

# --- Hub program ---
echo "==> Uploading hub program..."
uv run pybricksdev run ble --no-start main.py

# --- iOS app ---
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
