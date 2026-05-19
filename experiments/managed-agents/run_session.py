"""
Create and run a managed agent session targeting the local hub platform.

Requires:
  export ANTHROPIC_API_KEY="sk-ant-..."        # org API key (not env key)
  export ANTHROPIC_ENVIRONMENT_ID="env_..."

Also needs an agent ID — create one at:
  platform.claude.com → Workspace > Agents > New
  Or set ANTHROPIC_AGENT_ID if you already have one.

Run:
  uv run python experiments/managed-agents/run_session.py
"""

import asyncio
import os

import anthropic

TASK = """
You are testing connectivity to a local LEGO hub platform running on this machine.
Run each step, report the result, then continue to the next only if the previous succeeded.

STEP 1 — control port reachable?
  Run: nc -z -w2 localhost 8766 && echo PORT_OPEN || echo PORT_CLOSED

STEP 2 — WebSocket server reachable?
  Run: python3 -c "
import asyncio, websockets, json

async def test():
    async with websockets.connect('ws://localhost:8765/') as ws:
        msg = await asyncio.wait_for(ws.recv(), timeout=3)
        print('RECEIVED:', msg[:120])

asyncio.run(test())
" 2>&1

STEP 3 — read hub battery voltage (only if step 2 succeeded)
  Run: python3 -c "
import asyncio, websockets, json

async def test():
    async with websockets.connect('ws://localhost:8765/') as ws:
        await ws.recv()  # hello
        await ws.send(json.dumps({'target': 'hub', 'data': 'hub:battery:voltage'}))
        for _ in range(10):
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            if msg.get('type') == 'hub_stdout' and 'voltage' in msg.get('data', ''):
                print('BATTERY:', msg['data'])
                return
        print('NO_VOLTAGE_RESPONSE')

asyncio.run(test())
" 2>&1

Report your findings clearly for each step.
"""


async def main() -> None:
    api_key = os.environ["ANTHROPIC_API_KEY"]
    environment_id = os.environ["ANTHROPIC_ENVIRONMENT_ID"]
    agent_id = os.environ["ANTHROPIC_AGENT_ID"]

    client = anthropic.AsyncAnthropic(api_key=api_key)

    print(f"Creating session for agent {agent_id} in environment {environment_id}")

    session = await client.beta.sessions.create(
        agent=agent_id,
        environment_id=environment_id,
        metadata={"experiment": "managed-agents-connectivity"},
    )

    print(f"Session created: {session.id}")
    print("Waiting for worker to pick up and run...")
    print("(worker.py must be running in another terminal)")

    # Poll session status until terminal state
    while True:
        await asyncio.sleep(2)
        s = await client.beta.sessions.retrieve(session.id)
        print(f"  status: {s.status}")
        if s.status in ("completed", "failed", "cancelled"):
            print(f"\nDone: {s.status}")
            if hasattr(s, "output"):
                print("\n--- Agent output ---")
                print(s.output)
            break


asyncio.run(main())
