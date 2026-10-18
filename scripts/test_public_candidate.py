"""Run the source-checkout suites without installing private adapters."""
import argparse,os,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--suite',choices=['all','engine','workbench'],default='all');a=p.parse_args()
env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONUTF8':'1','PYTHONPATH':str(root/'src')}
paths={'engine':'tests','workbench':'workbench/tests'}
for name,path in paths.items():
    if a.suite not in ('all',name):continue
    print('Running '+name,flush=True)
    result=subprocess.run([sys.executable,'-B','-m','unittest','discover','-s',path],cwd=root,env=env)
    if result.returncode:raise SystemExit(result.returncode)
