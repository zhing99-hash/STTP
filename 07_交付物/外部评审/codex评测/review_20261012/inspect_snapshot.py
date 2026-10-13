import ast, collections, hashlib, json, os, subprocess, sys
from pathlib import Path
ROOT=Path(r'F:\codex\sttp\STTP-review')
OUT=Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding='utf-8')
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'06_PoC'))
import graph_export as gx
def read(p): return json.loads((ROOT/p).read_text(encoding='utf-8-sig'))
def counts(seq,key): return dict(collections.Counter(key(x) for x in seq))
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
g=read('06_PoC/etl/normalized.json')
nodes,edges=g['nodes'],g['edges']
sample=read('06_PoC/release/sample/sample_normalized.json')
m=read('06_PoC/release/manifest.json')
report={
 'date':'2026-10-12','nodes':len(nodes),'edges':len(edges),
 'node_types':counts(nodes,lambda x:gx.pick_type(x.get('labels'))),
 'edge_types':counts(edges,lambda x:x.get('type')),
 'levels':counts(edges,lambda x:(x.get('props')or{}).get('verification_level')),
 'sources':counts(edges,lambda x:(x.get('props')or{}).get('source')),
 'sample':{'nodes':len(sample['nodes']),'edges':len(sample['edges']),
  'node_prefix':counts(sample['nodes'],lambda x:x['id'].split(':')[0]),
  'node_sources':counts(sample['nodes'],lambda x:(x.get('props')or{}).get('source')),
  'edge_sources':counts(sample['edges'],lambda x:(x.get('props')or{}).get('source'))},
 'manifest_code':m.get('code'), 'manifest_build':m.get('build'),
 'manifest_dependencies':m.get('dependencies'),
 'manifest_full':m.get('artifacts',{}).get('full'),
 'actual_full_hashes':{k: digest(ROOT/v['path']) if (ROOT/v['path']).is_file() else None
  for k,v in m.get('artifacts',{}).get('full',{}).items() if isinstance(v,dict)and'path'in v},
}
files=list(ROOT.rglob('*.py'))
errors=[]
for p in files:
 try:ast.parse(p.read_text(encoding='utf-8-sig'),filename=str(p))
 except Exception as e:errors.append({'path':str(p.relative_to(ROOT)),'error':str(e)})
report['python_files']=len(files);report['syntax_errors']=errors
allfiles=[p for p in ROOT.rglob('*')if p.is_file() and '.git' not in p.parts]
report['disk_bytes']=sum(p.stat().st_size for p in allfiles)
report['inventory_by_directory']={k:{'files':len(v),'bytes':sum(p.stat().st_size for p in v)}
 for k,v in ((d.name,[p for p in allfiles if d in p.parents])for d in ROOT.iterdir()if d.is_dir()and d.name!='.git')}
report['top_files']=[{'path':str(p.relative_to(ROOT)),'bytes':p.stat().st_size}for p in sorted(allfiles,key=lambda p:p.stat().st_size,reverse=True)[:12]]
tasks=read('12_评测/tasks.json')
report['tasks_top_keys']=list(tasks)
report['task_details']=[{k:t.get(k)for k in ['id','category','question','method','inputs','expected','split']}for t in tasks['tasks']]
report['gnn_report_keys']=list(read('12_评测/gnn_eval_v2_report.json'))
status=subprocess.check_output(['git','-c','safe.directory='+str(ROOT),'status','--porcelain','-uall'],cwd=ROOT,encoding='utf-8')
report['git_status_count']=dict(collections.Counter(l[:2]for l in status.splitlines()))
report['code_hashes']={str(p.relative_to(ROOT)).replace('\\','/'):digest(p)for p in files}
(OUT/'snapshot_inventory.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items()if k not in ['task_details','code_hashes','sources','inventory_by_directory']},ensure_ascii=False,indent=2))
