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
SEND_QUEUE_SIZE = 64
SEND_TIMEOUT = 5.0
DROP_LOG_INTERVAL = 20

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
# last-known phone_hardware payload per sensor; sent to new clients on connect
phone_state_cache: dict[str, str] = {}

hub_connected: bool = False
phone_connected: bool = False
bridge_client = None  # the Client that is the iOS bridge


class Client:
    def __init__(self, ws: websockets.ServerConnection):
        self.ws = ws
        self.remote_address = ws.remote_address
        self._queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=SEND_QUEUE_SIZE)
        self._drops = 0

    def send(self, msg: str) -> None:
        try:
            self._queue.put_nowait(msg)
        except asyncio.QueueFull:
            self._drops += 1
            if self._drops % DROP_LOG_INTERVAL == 1:
                log.warning(
                    "Send queue full for %s (%d total drops)", self.remote_address, self._drops
                )

    async def _drain(self) -> None:
        while True:
            msg = await self._queue.get()
            if msg is None:
                return
            try:
                await asyncio.wait_for(self.ws.send(msg), timeout=SEND_TIMEOUT)
            except (websockets.exceptions.ConnectionClosed, asyncio.TimeoutError):
                try:
                    await self.ws.close()
                except Exception:
                    pass
                return


connected_clients: set[Client] = set()
# sensor name → set of subscribed clients; key present only while at least one subscriber exists
subscribers: dict[str, set[Client]] = {}


async def _phone_command(command: str) -> None:
    msg = json.dumps({"target": "phone", "command": command})
    for c in connected_clients.copy():
        c.send(msg)


async def _recover_subscriptions() -> None:
    for sensor in list(subscribers):
        if sensor.startswith("hub:"):
            await _hub_command(f"hub:stream:start:{sensor[4:]}:100")
            log.info("Recovered stream subscription: %s", sensor)


async def _hub_command(command: str) -> None:
    msg = json.dumps({"target": "hub", "data": command})
    for c in connected_clients.copy():
        c.send(msg)


async def _subscribe(client: Client, sensor: str) -> None:
    first = sensor not in subscribers
    subscribers.setdefault(sensor, set()).add(client)
    if first:
        if sensor.startswith("hub:"):
            await _hub_command(f"hub:stream:start:{sensor[4:]}:100")
        else:
            await _phone_command(f"start_{sensor}")
        log.info("Started %s stream (first subscriber: %s)", sensor, client.remote_address)


async def _unsubscribe(client: Client, sensor: str) -> None:
    subs = subscribers.get(sensor)
    if not subs:
        return
    subs.discard(client)
    if not subs:
        del subscribers[sensor]
        if sensor.startswith("hub:"):
            await _hub_command(f"hub:stream:stop:{sensor[4:]}")
        else:
            await _phone_command(f"stop_{sensor}")
        log.info("Stopped %s stream (no subscribers)", sensor)


async def _cleanup_subscriptions(client: Client) -> None:
    for sensor in list(subscribers):
        if client in subscribers.get(sensor, set()):
            await _unsubscribe(client, sensor)


async def handle_client(websocket: websockets.ServerConnection) -> None:
    global hub_connected, phone_connected, bridge_client
    addr = websocket.remote_address
    log.info("Client connected: %s", addr)
    client = Client(websocket)
    drain_task = asyncio.create_task(client._drain())
    connected_clients.add(client)
    try:
        client.send(json.dumps({
            "type": "hello",
            "ws_url": f"ws://{ip}:{PORT}/",
            "hub_connected": hub_connected,
            "phone_connected": phone_connected,
        }))
        for cached in phone_state_cache.values():
            client.send(cached)
        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("Non-JSON message from %s: %r", addr, raw)
                continue
            log.info("Received from %s: %s", addr, msg)
            if msg.get("type") == "hub_stdout" and not msg.get("data", "").startswith(">"):
                continue

            if msg.get("type") == "phone_connected":
                phone_connected = True
                bridge_client = client
                log.info("Phone connected (bridge: %s)", addr)
                broadcast = json.dumps({"type": "phone_connected"})
                for c in (connected_clients - {client}):
                    c.send(broadcast)
                continue

            if msg.get("type") == "hub_connected":
                hub_connected = True
                log.info("Hub connected (via bridge: %s)", addr)
                broadcast = json.dumps({"type": "hub_connected"})
                for c in (connected_clients - {client}):
                    c.send(broadcast)
                await _recover_subscriptions()
                continue

            if msg.get("type") == "hub_disconnected":
                hub_connected = False
                log.info("Hub disconnected (via bridge: %s)", addr)
                broadcast = json.dumps({"type": "hub_disconnected"})
                for c in (connected_clients - {client}):
                    c.send(broadcast)
                continue

            if msg.get("type") == "subscribe":
                sensor = msg.get("sensor", "")
                if sensor:
                    await _subscribe(client, sensor)
                continue

            if msg.get("type") == "unsubscribe":
                sensor = msg.get("sensor", "")
                if sensor:
                    await _unsubscribe(client, sensor)
                continue

            # hub_stdout >stream: lines — parse and route to hub: subscribers
            if msg.get("type") == "hub_stdout":
                data = msg.get("data", "")
                if data.startswith(">stream:"):
                    parts_data = data[8:].split(":")
                    sensor_key = "hub:" + parts_data[0] if parts_data else ""
                    payload: dict = {"type": "hub_stream", "sensor": sensor_key}
                    for kv in parts_data[1:]:
                        if "=" in kv:
                            k, v = kv.split("=", 1)
                            try:
                                payload[k] = float(v)
                            except ValueError:
                                payload[k] = v
                    raw_json = json.dumps(payload)
                    for c in subscribers.get(sensor_key, set()).copy():
                        c.send(raw_json)
                    continue

            # phone_hardware: cache latest state, then route to subscribers or broadcast
            if msg.get("type") == "phone_hardware":
                sensor = msg.get("sensor", "")
                if sensor:
                    phone_state_cache[sensor] = raw
                targets = subscribers[sensor].copy() if sensor in subscribers else connected_clients - {client}
                for c in targets:
                    c.send(raw)
            else:
                for c in (connected_clients - {client}):
                    c.send(raw)

            # phone battery state → hub status light
            if msg.get("type") == "phone_hardware" and msg.get("sensor") == "battery":
                color = {"charging": "GREEN", "full": "GREEN", "unplugged": "RED"}.get(msg.get("state"))
                if color:
                    cmd = json.dumps({"target": "hub", "data": f"hub:light:on:{color}"})
                    for c in connected_clients:
                        c.send(cmd)
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        connected_clients.discard(client)
        drain_task.cancel()
        try:
            await drain_task
        except asyncio.CancelledError:
            pass
        await _cleanup_subscriptions(client)
        log.info("Client disconnected: %s", addr)
        if client is bridge_client:
            bridge_client = None
            phone_connected = False
            hub_connected = False
            for c in connected_clients.copy():
                c.send(json.dumps({"type": "hub_disconnected"}))
                c.send(json.dumps({"type": "phone_disconnected"}))
            log.info("Bridge disconnected — broadcast hub_disconnected + phone_disconnected")
        if connected_clients:
            stop = json.dumps({"target": "hub", "data": "exec:[m.stop() for m in motors.values()]"})
            for c in connected_clients.copy():
                c.send(stop)
            log.info("Sent safe-state stop to %d remaining client(s)", len(connected_clients))


async def handle_control(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=5)
        cmd = line.decode().strip()
        if cmd == "hub_disconnect":
            if connected_clients:
                msg = json.dumps({"type": "hub_disconnect"})
                for c in connected_clients.copy():
                    c.send(msg)
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
