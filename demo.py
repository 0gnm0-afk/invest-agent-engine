"""Run the offline workbench from a source checkout."""
import sys
from pathlib import Path
root=Path(__file__).resolve().parent
sys.path[:0]=[str(root/'src'),str(root/'workbench/scripts')]
from public_demo import main
if __name__=='__main__':main()
