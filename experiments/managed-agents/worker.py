"""
Environment worker for self-hosted managed agent sessions.

One-time Console setup required before running:
  1. platform.claude.com → Workspace > Environments > New > Self-hosted
  2. Open the environment → Generate environment key

Then export:
  export ANTHROPIC_ENVIRONMENT_KEY="sk-ant-oat01-..."
  export ANTHROPIC_ENVIRONMENT_ID="env_..."

Run:
  uv run python experiments/managed-agents/worker.py
"""

import asyncio
import os

from anthropic import AsyncAnthropic
from anthropic.lib.environments import EnvironmentWorker

WORKDIR = "/tmp/pybricks-agent-workspace"


async def main() -> None:
    environment_key = os.environ["ANTHROPIC_ENVIRONMENT_KEY"]
    environment_id = os.environ["ANTHROPIC_ENVIRONMENT_ID"]

    os.makedirs(WORKDIR, exist_ok=True)

    print(f"Worker polling environment {environment_id}")
    print(f"Workdir: {WORKDIR}")

    async with AsyncAnthropic(auth_token=environment_key) as client:
        await EnvironmentWorker(
            client,
            environment_id=environment_id,
            environment_key=environment_key,
            workdir=WORKDIR,
        ).run()


asyncio.run(main())
