"""Run read-only project checks and save their outputs outside the reviewed tree."""
import ast
import collections
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(r"F:\codex\sttp\STTP-review")
OUT = Path(__file__).resolve().parent
os.environ['PYTHONPATH'] = r'F:\codex\sttp\review-deps'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.stdout.reconfigure(encoding='utf-8')

def run(name, args):
    start = time.monotonic()
    try:
        p = subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True,
                           text=True, encoding='utf-8', errors='replace', timeout=900)
        result = dict(name=name, command=args, rc=p.returncode,
                      elapsed_s=round(time.monotonic()-start,2))
        (OUT/f'{name}.log').write_text(p.stdout+'\n[STDERR]\n'+p.stderr,encoding='utf-8')
    except Exception as exc:
        result = dict(name=name,command=args,error=str(exc))
    print(json.dumps(result,ensure_ascii=False),flush=True)
    return result

checks = [
 ('frozen_gate',['06_PoC/frozen_gate.py','--release','--json']),
 ('release_full',['06_PoC/release/verify_release.py','--full','--release','--json']),
 ('eval',['12_评测/eval_runner.py','--strict','--json',str(OUT/'eval_rerun.json')]),
 ('source_registry',['11_真实数据/source_registry.py','--check']),
 ('lineage',['11_真实数据/lineage_invalidation.py','--json','--out',str(OUT/'lineage_rerun.json')]),
 ('connectivity',['06_PoC/connectivity_audit.py','--json',str(OUT/'connectivity_rerun.json')]),
 ('task_trust',['06_PoC/task_trust_audit.py','--graph','06_PoC/etl/normalized.json','--json',str(OUT/'trust_rerun.json')]),
]
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    results = list(pool.map(lambda pair:run(*pair),checks))
(OUT/'check_results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
