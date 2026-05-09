import asyncio
import json
import logging
import socket

import websockets
from zeroconf.asyncio import AsyncZeroconf
from zeroconf import ServiceInfo

PORT = 8765
CONTROL_PORT = 8766
SERVICE_TYPE = "_bricks._tcp.local."
SERVICE_NAME = "bricks._bricks._tcp.local."

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def local_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.254.254.254", 1))
            return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"


ip = local_ip()
connected_clients: set[websockets.ServerConnection] = set()


async def handle_client(websocket: websockets.ServerConnection) -> None:
    addr = websocket.remote_address
    log.info("Client connected: %s", addr)
    connected_clients.add(websocket)
    try:
        await websocket.send(json.dumps({"type": "hello", "ws_url": f"ws://{ip}:{PORT}/"}))
        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("Non-JSON message from %s: %r", addr, raw)
                continue
            log.info("Received from %s: %s", addr, msg)
            if msg.get("type") == "hub_stdout" and not msg.get("data", "").startswith(">"):
                continue
            others = connected_clients - {websocket}
            if others:
                await asyncio.gather(*(c.send(raw) for c in others))
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        connected_clients.discard(websocket)
        log.info("Client disconnected: %s", addr)
        if connected_clients:
            stop = json.dumps({"target": "hub", "data": "exec:[m.stop() for m in motors.values()]"})
            await asyncio.gather(*(c.send(stop) for c in connected_clients.copy()))
            log.info("Sent safe-state stop to %d remaining client(s)", len(connected_clients))


async def handle_control(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=5)
        cmd = line.decode().strip()
        if cmd == "hub_disconnect":
            if connected_clients:
                msg = json.dumps({"type": "hub_disconnect"})
                await asyncio.gather(*(c.send(msg) for c in connected_clients.copy()))
                log.info("Sent hub_disconnect to %d client(s)", len(connected_clients))
            else:
                log.info("hub_disconnect received but no clients connected")
            writer.write(b"ok\n")
        else:
            log.warning("Unknown control command: %r", cmd)
            writer.write(b"unknown_command\n")
        await writer.drain()
    except Exception as e:
        log.warning("Control error: %s", e)
    finally:
        writer.close()


async def main() -> None:
    hostname = socket.gethostname()

    aiozc = AsyncZeroconf()
    info = ServiceInfo(
        SERVICE_TYPE,
        SERVICE_NAME,
        addresses=[socket.inet_aton(ip)],
        port=PORT,
        properties={"ip": ip, "port": str(PORT)},
        server=f"{hostname}.local.",
    )

    control = await asyncio.start_server(handle_control, "127.0.0.1", CONTROL_PORT)

    async with websockets.serve(handle_client, "0.0.0.0", PORT), control:
        log.info("WebSocket server listening on 0.0.0.0:%d", PORT)
        log.info("Control server listening on 127.0.0.1:%d", CONTROL_PORT)
        await aiozc.async_register_service(info)
        log.info("Bonjour: %s on %s:%d", SERVICE_NAME, ip, PORT)
        try:
            await asyncio.Future()
        finally:
            await aiozc.async_unregister_service(info)
            await aiozc.async_close()


if __name__ == "__main__":
    asyncio.run(main())
