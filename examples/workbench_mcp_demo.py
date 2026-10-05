"""Initialize the real STDIO server and round-trip fictional evidence/records."""
import asyncio
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def payload(result):
    if result.isError:
        raise RuntimeError('synthetic_mcp_call_failed')
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(next(item.text for item in result.content if item.type == 'text'))


async def run():
    root = Path(__file__).resolve().parents[1]
    params = StdioServerParameters(command=sys.executable,
        args=['-B', '-m', 'invest_agent.workbench_mcp.server'],
        env={**os.environ, 'PYTHONPATH': str(root/'src'), 'INVEST_MCP_MODE': 'synthetic',
             'PYTHONDONTWRITEBYTECODE': '1'})
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            listed = await session.list_tools()
            assert len(listed.tools) == 16
            cases = payload(await session.call_tool('analysis_list', {}))
            key = cases['cases'][0]['analysis_key']
            evidence = payload(await session.call_tool('analysis_read', {'analysis_key': key}))
            saved = payload(await session.call_tool('analysis_save_record', {
                'analysis_key': key, 'snapshot_id': evidence['snapshot_id'], 'kind': 'report',
                'title': 'Synthetic report', 'body': 'Fictional evidence; assumptions unresolved.'}))
            history = payload(await session.call_tool('analysis_history', {'analysis_key': key}))
            assert saved['persisted'] is False
            assert history['records'][-1]['record_id'] == saved['record_id']
            print(json.dumps({'synthetic': True, 'tool_count': len(listed.tools),
                'read_save_reread': 'PASS', 'storage': 'process_memory'}, indent=2))


if __name__ == '__main__':
    asyncio.run(run())
