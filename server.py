import asyncio
import json
import logging
import os
import signal
import socket
from pathlib import Path

import websockets
from zeroconf.asyncio import AsyncZeroconf
from zeroconf import ServiceInfo

PORT = 8765
CONTROL_PORT = 8766
PID_FILE = Path(".server.pid")
SERVER_VERSION = 1
SERVICE_TYPE = "_bricks._tcp.local."
SERVICE_NAME = "bricks._bricks._tcp.local."
SEND_QUEUE_SIZE = 64
SEND_TIMEOUT = 5.0
DROP_LOG_INTERVAL = 20
# Sensors whose frames are never cached or replayed to new clients (high-frequency streams, not snapshots)
STREAM_ONLY_SENSORS: set[str] = {"camera"}

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
hub_battery_cache: str | None = None  # last hub_battery payload; replayed to new clients
phone_manifest_cache: str | None = None  # last phone_connected manifest; replayed to new clients


class Client:
    def __init__(self, ws: websockets.ServerConnection):
        self.ws = ws
        self.remote_address = ws.remote_address
        self.name: str | None = None  # set on register message
        self._queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=SEND_QUEUE_SIZE)
        self._drops = 0

    def label(self) -> str:
        return self.name if self.name else str(self.remote_address)

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
# active stream interval (ms) per sensor; tracks the fastest rate any subscriber has requested
stream_intervals: dict[str, int] = {}
# extra options forwarded to the phone for each sensor (e.g. {"mode": "saliency"}); last-writer-wins
stream_options: dict[str, dict] = {}


async def _phone_command(command: dict) -> None:
    msg = json.dumps({"target": "phone", "command": command})
    for c in connected_clients.copy():
        c.send(msg)


async def _recover_subscriptions() -> None:
    for sensor in list(subscribers):
        if sensor.startswith("hub:"):
            interval = stream_intervals.get(sensor, 100)
            await _hub_command(f"hub:stream:start:{sensor[4:]}:{interval}")
            log.info("Recovered hub stream subscription: %s at %dms", sensor, interval)


async def _recover_phone_subscriptions() -> None:
    for sensor in list(subscribers):
        if not sensor.startswith("hub:"):
            interval = stream_intervals.get(sensor, 100)
            cmd: dict = {"action": "start", "sensor": sensor, "interval": interval}
            cmd.update(stream_options.get(sensor, {}))
            await _phone_command(cmd)
            log.info("Recovered phone stream subscription: %s at %dms", sensor, interval)


async def _hub_command(command: str) -> None:
    msg = json.dumps({"target": "hub", "data": command})
    for c in connected_clients.copy():
        c.send(msg)


async def _subscribe(client: Client, sensor: str, interval: int = 100, options: dict | None = None) -> None:
    first = sensor not in subscribers
    subscribers.setdefault(sensor, set()).add(client)
    if first:
        stream_intervals[sensor] = interval
        if options:
            stream_options[sensor] = options
        if sensor.startswith("hub:"):
            await _hub_command(f"hub:stream:start:{sensor[4:]}:{interval}")
        else:
            cmd: dict = {"action": "start", "sensor": sensor, "interval": interval}
            cmd.update(options or {})
            await _phone_command(cmd)
        log.info("Started %s stream at %dms (first subscriber: %s)", sensor, interval, client.label())
    elif sensor.startswith("hub:") and interval < stream_intervals.get(sensor, interval):
        stream_intervals[sensor] = interval
        await _hub_command(f"hub:stream:stop:{sensor[4:]}")
        await _hub_command(f"hub:stream:start:{sensor[4:]}:{interval}")
        log.info("Restarted %s stream at %dms (faster request from %s)", sensor, interval, client.label())
    elif not sensor.startswith("hub:") and options and options != stream_options.get(sensor):
        # Last-writer-wins: new subscriber wants different options — update and re-send phone command
        log.warning("Sensor %s options conflict: %s requests %s (was %s)",
                    sensor, client.label(), options, stream_options.get(sensor, {}))
        stream_options[sensor] = options
        cmd = {"action": "start", "sensor": sensor, "interval": stream_intervals.get(sensor, interval)}
        cmd.update(options)
        await _phone_command(cmd)


async def _unsubscribe(client: Client, sensor: str) -> None:
    subs = subscribers.get(sensor)
    if not subs:
        return
    subs.discard(client)
    if not subs:
        del subscribers[sensor]
        stream_intervals.pop(sensor, None)
        stream_options.pop(sensor, None)
        if sensor.startswith("hub:"):
            await _hub_command(f"hub:stream:stop:{sensor[4:]}")
        else:
            await _phone_command({"action": "stop", "sensor": sensor})
        log.info("Stopped %s stream (no subscribers)", sensor)


async def _cleanup_subscriptions(client: Client) -> None:
    for sensor in list(subscribers):
        if client in subscribers.get(sensor, set()):
            await _unsubscribe(client, sensor)


async def handle_client(websocket: websockets.ServerConnection) -> None:
    global hub_connected, phone_connected, bridge_client, hub_battery_cache, phone_manifest_cache
    addr = websocket.remote_address
    log.info("Client connected: %s", addr)
    client = Client(websocket)
    drain_task = asyncio.create_task(client._drain())
    connected_clients.add(client)
    try:
        client.send(json.dumps({
            "type": "hello",
            "version": SERVER_VERSION,
            "ws_url": f"ws://{ip}:{PORT}/",
            "hub_connected": hub_connected,
            "phone_connected": phone_connected,
        }))
        if phone_manifest_cache:
            client.send(phone_manifest_cache)
        for cached in phone_state_cache.values():
            client.send(cached)
        if hub_battery_cache:
            client.send(hub_battery_cache)
        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("Non-JSON message from %s: %r", addr, raw)
                continue
            if msg.get("type") == "phone_hardware" and msg.get("sensor") == "camera":
                log.debug("Received from %s: camera frame %dx%d (%d chars)",
                          client.label(), msg.get("width", 0), msg.get("height", 0),
                          len(msg.get("frame", "")))
            else:
                log.info("Received from %s: %s", client.label(), msg)
            if msg.get("type") == "hub_stdout" and not msg.get("data", "").startswith(">"):
                continue

            if msg.get("type") == "register":
                client.name = msg.get("name") or client.name
                desc = msg.get("description", "")
                log.info("Agent registered: %s%s", client.name, f" — {desc}" if desc else "")
                continue

            if msg.get("type") == "phone_connected":
                phone_connected = True
                bridge_client = client
                phone_manifest_cache = raw
                hw = msg.get("hardware", {})
                log.info("Phone connected (bridge: %s) device=%s os=%s gps=%s compass=%s barometer=%s cameras=%s",
                         client.label(), msg.get("device", "?"), msg.get("os", "?"),
                         hw.get("gps"), hw.get("compass"), hw.get("barometer"), hw.get("cameras"))
                for c in (connected_clients - {client}):
                    c.send(raw)
                await _recover_phone_subscriptions()
                continue

            if msg.get("type") == "hub_connected":
                hub_connected = True
                if bridge_client is None:
                    bridge_client = client
                    phone_connected = True
                    log.info("Bridge inferred from hub_connected (phone_connected not received): %s", client.label())
                log.info("Hub connected (via bridge: %s)", client.label())
                broadcast = json.dumps({"type": "hub_connected"})
                for c in (connected_clients - {client}):
                    c.send(broadcast)
                await _recover_subscriptions()
                await _hub_command(
                    "exec:v=hub.battery.voltage();i=hub.battery.current();"
                    "c=hub.charger.connected();"
                    "print('>hub_battery:voltage='+str(v)+':current='+str(i)+':charger='+str(c))"
                )
                continue

            if msg.get("type") == "hub_disconnected":
                hub_connected = False
                log.info("Hub disconnected (via bridge: %s)", client.label())
                broadcast = json.dumps({"type": "hub_disconnected"})
                for c in (connected_clients - {client}):
                    c.send(broadcast)
                continue

            if msg.get("type") == "subscribe":
                sensor = msg.get("sensor", "")
                interval = max(10, int(msg.get("interval", 100)))
                if sensor:
                    options = {k: v for k, v in msg.items() if k not in ("type", "sensor", "interval")}
                    await _subscribe(client, sensor, interval, options or None)
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

                if data.startswith(">hub_battery:"):
                    parts_data = data[13:].split(":")
                    payload = {"type": "hub_battery"}
                    for kv in parts_data:
                        if "=" in kv:
                            k, v = kv.split("=", 1)
                            try:
                                payload[k] = int(v)
                            except ValueError:
                                payload[k] = v
                    payload["charger"] = bool(payload.get("charger", 0))
                    raw_json = json.dumps(payload)
                    hub_battery_cache = raw_json
                    for c in (connected_clients - {client}):
                        c.send(raw_json)
                    continue

            # phone_hardware: cache latest state, then route to subscribers or broadcast
            if msg.get("type") == "phone_hardware":
                sensor = msg.get("sensor", "")
                if sensor and sensor not in STREAM_ONLY_SENSORS:
                    phone_state_cache[sensor] = raw
                targets = subscribers[sensor].copy() if sensor in subscribers else connected_clients - {client}
                for c in targets:
                    c.send(raw)
            else:
                for c in (connected_clients - {client}):
                    c.send(raw)


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
        log.info("Client disconnected: %s", client.label())
        if client is bridge_client:
            bridge_client = None
            phone_connected = False
            hub_connected = False
            phone_manifest_cache = None
            for c in connected_clients.copy():
                c.send(json.dumps({"type": "hub_disconnected"}))
                c.send(json.dumps({"type": "phone_disconnected"}))
            log.info("Bridge disconnected — broadcast hub_disconnected + phone_disconnected")


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

    PID_FILE.write_text(str(os.getpid()))
    log.info("PID %d written to %s", os.getpid(), PID_FILE)

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown_event.set)

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
            await shutdown_event.wait()
            log.info("Shutting down...")
        finally:
            PID_FILE.unlink(missing_ok=True)
            await aiozc.async_unregister_service(info)
            await aiozc.async_close()


if __name__ == "__main__":
    asyncio.run(main())
# zarvox test
