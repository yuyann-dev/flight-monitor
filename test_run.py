import sys
sys.path.insert(0, "/opt/flight-monitor")

import asyncio
from app.scheduler import run_single_monitor

async def main():
    await run_single_monitor(1)

asyncio.run(main())
