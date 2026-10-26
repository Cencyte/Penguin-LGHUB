#!/usr/bin/env python3
"""Run with the existing SystemSage/MCP venv; verify actual read-only metric calls."""
import argparse
import asyncio
import json
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def probe(command):
    async with stdio_client(StdioServerParameters(command=command), errlog=sys.stderr) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            listing = await session.list_tools()
            result = {'tool_count': len(listing.tools), 'metrics': {}}
            for name in ('get_memory_usage', 'get_cpu_usage', 'get_disk_usage'):
                response = await session.call_tool(name, {})
                if response.isError:
                    raise RuntimeError(f'SystemSage {name} failed: {response.content}')
                result['metrics'][name] = [part.text for part in response.content if hasattr(part, 'text')]
            return result

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--command', required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(probe(args.command))))
