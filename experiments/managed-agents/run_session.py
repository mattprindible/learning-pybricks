"""
Create and run a managed agent session targeting the local hub platform.

Requires:
  export ANTHROPIC_API_KEY="sk-ant-..."        # org API key (not env key)
  export ANTHROPIC_ENVIRONMENT_ID="env_..."

Run with worker.py running in another terminal:
  uv run python experiments/managed-agents/run_session.py
"""

import asyncio
import json
import os

import anthropic

TASK = """\
You have access to a LEGO Inventor Hub connected via a WebSocket server running
on this machine at ws://localhost:8765/.

Your goal: discover what hardware is connected, then design and implement a
real-time control loop that maps some input to some output using what you find.

## Protocol

Connect with websockets (pip install if needed). The first message is a JSON
hello with hub_connected, phone_connected, etc.

Send commands as JSON: {"target": "hub", "data": "COMMAND"}
Hub responds with {"type": "hub_stdout", "data": ">RESPONSE"}

Useful discovery commands:
  exec:print(">motors:" + str(list(motors.keys())))
  exec:print(">sensors:" + str(list(sensors.keys())))
  hub:imu:tilt          -> >hub:imu:tilt:pitch=F:roll=F
  hub:battery:voltage   -> >hub:battery:voltage=INT

Motor commands:
  motor:PORT:run:SPEED           non-blocking (SPEED deg/s)
  motor:PORT:run:SPEED:DURATION  blocking with duration ms
  motor:PORT:stop
  motor:PORT:angle               -> >motor:PORT:angle=INT

Sensor commands:
  sensor:PORT:distance    -> >sensor:PORT:distance=INT  (mm; 2000=nothing)
  sensor:PORT:color       -> >sensor:PORT:color=Color.NAME
  sensor:PORT:reflection  -> >sensor:PORT:reflection=INT (0-100)

Hub light/sound:
  hub:light:on:COLOR    (RED/GREEN/BLUE/YELLOW/ORANGE/CYAN/WHITE)
  hub:speaker:beep:HZ:MS

Exec arbitrary Python on hub:
  exec:EXPRESSION  -> print() output then >exec:ok or >exec:error:MSG
  Note: print() output only forwarded if the string starts with ">"

Helper to send a command and collect response lines until a terminal marker:

  async def send_cmd(ws, cmd, timeout=5):
      await ws.send(json.dumps({"target": "hub", "data": cmd}))
      lines = []
      while True:
          msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
          if msg.get("type") == "hub_stdout":
              data = msg["data"]
              lines.append(data)
              if any(data.startswith(p) for p in [">exec:", ">error:", ">motor:", ">sensor:", ">hub:"]):
                  break
      return lines

## Your task

1. Connect and discover what is plugged in (motors, sensors).
2. Based on what is available, pick an interesting input->output mapping.
3. Write a Python control script to a file and run it for ~15 seconds.
4. Clean up (stop motors if any were running).
5. Report: what you found, what you built, and what you observed on the hardware.

All logic runs locally via websockets — no deployment needed.
"""

TERMINAL_STATUSES = {"completed", "failed", "cancelled", "error", "terminated"}


async def main() -> None:
    api_key = os.environ["ANTHROPIC_API_KEY"]
    environment_id = os.environ["ANTHROPIC_ENVIRONMENT_ID"]

    client = anthropic.AsyncAnthropic(api_key=api_key)

    print("Creating agent...")
    agent = await client.beta.agents.create(
        model="claude-sonnet-4-6",
        name="pybricks-connectivity-test",
        system=TASK,
        tools=[{"type": "agent_toolset_20260401"}],
    )
    print(f"Agent: {agent.id}")

    print(f"Creating session in environment {environment_id}...")
    session = await client.beta.sessions.create(
        agent=agent.id,
        environment_id=environment_id,
    )
    print(f"Session: {session.id}")

    print("Sending initial user message to start the agent...")
    await client.beta.sessions.events.send(
        session.id,
        events=[{
            "type": "user.message",
            "content": [{"type": "text", "text": "Begin the test sequence."}],
        }],
    )
    print("Streaming events (worker must be running)...\n")

    seen_event_ids: set[str] = set()
    last_cursor: str | None = None

    while True:
        await asyncio.sleep(2)

        kwargs: dict = {"order": "asc", "limit": 50}
        if last_cursor:
            kwargs["created_at_gt"] = last_cursor

        page = await client.beta.sessions.events.list(session.id, **kwargs)

        async for event in page:
            if event.id in seen_event_ids:
                continue
            seen_event_ids.add(event.id)
            last_cursor = getattr(event, "created_at", last_cursor)

            t = getattr(event, "type", None) or getattr(event, "event_type", "?")
            if hasattr(event, "model_dump"):
                dump = event.model_dump(exclude_none=True)
                dump.pop("id", None)
                dump.pop("type", None)
                suffix = f" {json.dumps(dump, default=str)[:300]}" if dump else ""
            else:
                suffix = ""
            print(f"[{t}]{suffix}")

        s = await client.beta.sessions.retrieve(session.id)
        if s.status in TERMINAL_STATUSES:
            print(f"\nSession {s.status}.")
            break


asyncio.run(main())
