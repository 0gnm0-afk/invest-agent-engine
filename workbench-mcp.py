"""Latest workbench MCP; explicit offline demo must already be running."""
import runpy,sys
from pathlib import Path
root=Path(__file__).resolve().parent
sys.path[:0]=[str(root/'src'),str(root/'workbench/scripts')]
if __name__=='__main__':runpy.run_module('analysis_mcp',run_name='__main__')
