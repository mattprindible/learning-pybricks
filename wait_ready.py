#!/usr/bin/env python3
"""
Block until the requested components are confirmed connected.

Usage:
  wait_ready.py [timeout] [--phone] [--hub]

  --phone   wait for phone_connected only
  --hub     wait for hub_connected only
  (neither) wait for both (default)

Exit 0: ready.
Exit 1: prints DEPLOY:ready:error reason=TOKEN ... to stdout.

Error tokens and what to do with them:
  CANNOT_CONNECT_WEBSOCKET  — server not reachable; check server.log
  CONNECTION_CLOSED         — server dropped connection mid-wait; may be restarting
  TIMEOUT                   — connected but event(s) never arrived; waiting= names which ones
"""

import asyncio
import json
import subprocess
import sys
import time

WS_URL = "ws://localhost:8765/"

args = sys.argv[1:]
want_phone = "--phone" in args or ("--phone" not in args and "--hub" not in args)
want_hub   = "--hub"   in args or ("--phone" not in args and "--hub" not in args)
timeout_args = [a for a in args if a.lstrip("-").isdigit()]
TIMEOUT = int(timeout_args[0]) if timeout_args else 30


def speak(text: str) -> None:
    subprocess.Popen(["osascript", "-e", f'say "{text}" using "Zarvox"'])


def missing_list(phone_ok: bool, hub_ok: bool) -> str:
    m = []
    if want_phone and not phone_ok:
        m.append("phone_connected")
    if want_hub and not hub_ok:
        m.append("hub_connected")
    return ",".join(m)


def humanize(token: str) -> str:
    return token.replace("_", " ").lower()


async def wait() -> None:
    import websockets
    import websockets.exceptions

    phone_ok = not want_phone
    hub_ok   = not want_hub
    start = time.monotonic()

    while True:
        elapsed = time.monotonic() - start
        if elapsed >= TIMEOUT:
            print(f"DEPLOY:ready:error reason=CANNOT_CONNECT_WEBSOCKET"
                  f" elapsed={int(elapsed)}s hint=check_server.log")
            speak("Deploy failed. cannot connect to server")
            sys.exit(1)

        try:
            async with websockets.connect(WS_URL, open_timeout=5) as ws:
                while True:
                    elapsed = time.monotonic() - start
                    remaining = TIMEOUT - elapsed
                    if remaining <= 0:
                        missing = missing_list(phone_ok, hub_ok)
                        print(f"DEPLOY:ready:error reason=TIMEOUT"
                              f" waiting={missing}"
                              f" elapsed={int(elapsed)}s")
                        speak(f"Deploy failed. timeout waiting for {humanize(missing)}")
                        sys.exit(1)

                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 2.0))
                    except asyncio.TimeoutError:
                        continue

                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    t = msg.get("type")
                    if t == "hello":
                        if want_phone:
                            phone_ok = msg.get("phone_connected", False)
                        if want_hub:
                            hub_ok = msg.get("hub_connected", False)
                    elif t == "phone_connected":
                        phone_ok = True
                    elif t == "hub_connected":
                        hub_ok = True

                    if phone_ok and hub_ok:
                        elapsed = time.monotonic() - start
                        confirmed = []
                        if want_phone:
                            confirmed.append("phone_connected=true")
                        if want_hub:
                            confirmed.append("hub_connected=true")
                        print(f"DEPLOY:ready:ok {' '.join(confirmed)} elapsed={int(elapsed)}s")
                        return

        except (OSError, websockets.exceptions.WebSocketException) as exc:
            elapsed = time.monotonic() - start
            if elapsed >= TIMEOUT:
                print(f"DEPLOY:ready:error reason=CANNOT_CONNECT_WEBSOCKET"
                      f" elapsed={int(elapsed)}s hint=check_server.log")
                speak("Deploy failed. cannot connect to server")
                sys.exit(1)
            await asyncio.sleep(0.5)


asyncio.run(wait())
