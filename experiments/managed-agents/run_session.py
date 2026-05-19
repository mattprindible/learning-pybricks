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
You are testing connectivity to a local LEGO hub platform running on this machine.
Run each step in order using bash. Report the result, then move to the next step.

STEP 1 — is the control port reachable?
  nc -z -w2 localhost 8766 && echo PORT_OPEN || echo PORT_CLOSED

STEP 2 — does the WebSocket server accept connections?
  python3 -c "
import asyncio, sys
try:
    import websockets
except ImportError:
    print('websockets not installed'); sys.exit(1)
async def t():
    async with websockets.connect('ws://localhost:8765/') as ws:
        msg = await asyncio.wait_for(ws.recv(), timeout=3)
        print('RECEIVED:', msg[:120])
asyncio.run(t())
"

STEP 3 — read hub battery voltage (only if step 2 succeeded)
  python3 -c "
import asyncio, json, websockets
async def t():
    async with websockets.connect('ws://localhost:8765/') as ws:
        await ws.recv()  # hello
        await ws.send(json.dumps({'target': 'hub', 'data': 'hub:battery:voltage'}))
        for _ in range(15):
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            if msg.get('type') == 'hub_stdout' and 'voltage' in msg.get('data', ''):
                print('VOLTAGE:', msg['data'])
                return
        print('NO_VOLTAGE_RESPONSE')
asyncio.run(t())
"

Report your findings clearly for each step.
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
