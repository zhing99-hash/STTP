"""Memory-only counterexamples; does not alter source files or source data."""
import collections, copy, hashlib, importlib.util, json, os, sys
from pathlib import Path
ROOT=Path(r'F:\codex\sttp\STTP-review')
OUT=Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding='utf-8')
sys.dont_write_bytecode=True
sys.path[:0]=[str(ROOT/'11_真实数据'),str(ROOT/'06_PoC'),str(ROOT/'03_知识层'),r'F:\codex\sttp\review-deps']
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,ROOT/path)
 mod=importlib.util.module_from_spec(spec);sys.modules[name]=mod;spec.loader.exec_module(mod);return mod
ev=load('review_eval','12_评测/eval_runner.py')
vr=load('review_release','06_PoC/release/verify_release.py')
import lineage_invalidation as li
import source_registry as sr
g=json.loads((ROOT/'06_PoC/etl/normalized.json').read_text(encoding='utf-8'))
doc=json.loads((ROOT/'12_评测/tasks.json').read_text(encoding='utf-8'))
results={}
world=ev.World()
rows,summary=ev.run(doc,world,verbose=False)
results['baseline']=summary['overall']
world.edges=[];world._edge_types=collections.defaultdict(set)
rows,summary=ev.run(doc,world,verbose=False)
results['remove_all_edges']={'summary':summary['overall'],'failed':[r['id']for r in rows if r['verdict']!='correct']}
world.nodes={}
rows,summary=ev.run(doc,world,verbose=False)
results['remove_all_nodes_and_edges']={'summary':summary['overall'],'failed':[r['id']for r in rows if r['verdict']!='correct']}
world=ev.World()
noquestion=copy.deepcopy(doc)
for t in noquestion['tasks']:t['question']='THIS IS NOT A SCIENCE QUESTION'
_,summary=ev.run(noquestion,world,verbose=False)
results['replace_all_question_text']=summary['overall']
results['numeric_domain_probes']={}
for temp in [-298.15,0.0]:
 try:res=ev.solve({'method':'compute_dG_from_K','inputs':{'T_K':temp,'K':10}},world)
 except Exception as exc:res={'exception':str(exc)}
 results['numeric_domain_probes'][str(temp)]=res
dict_tasks=[t for t in doc['tasks'] if t['expected']['kind']=='value' and isinstance(t['expected']['value'],dict)]
results['nonfinite_grader']=[{'id':t['id'],'payload':'all expected numeric fields = NaN','verdict':ev.grade(t,'answer',{k:float('nan')for k in t['expected']['value']})}for t in dict_tasks]
m=json.loads((ROOT/'06_PoC/release/manifest.json').read_text(encoding='utf-8'))
bad=copy.deepcopy(m);bad['artifacts']['full']['snapshot']['path']='review_missing_snapshot.json'
rep=vr.Report();vr.check_full(rep,bad)
results['full_missing_snapshot']=rep.payload()
norm,view=vr.load_sample();view['edges'][0]['type']='MUTATED_RELATION'
rep=vr.Report();vr.check_same_version(rep,norm,view)
results['same_ids_changed_relation']=rep.payload()
results['evidence_impl_source_counts']=[{'impl':k[0],'src':k[1],'n':v} for k,v in collections.Counter(
 (e.get('impl'),e.get('src'))for edge in g['edges']for e in edge.get('props',{}).get('verification_evidence',[])if isinstance(e,dict)).most_common()]
results['evidence_current_impl_versions']=dict(collections.Counter(e.get('impl_ver')for edge in g['edges']for e in edge.get('props',{}).get('verification_evidence',[])if isinstance(e,dict)))
results['verifier_actual_hash']=hashlib.sha256((ROOT/'11_真实数据/verification_model.py').read_bytes()).hexdigest()[:12]
# A verifier-version change must invalidate its evidence even if inputs are unchanged.
edge=copy.deepcopy(next(e for e in g['edges']if e.get('props',{}).get('verification_evidence')))
for e in edge['props']['verification_evidence']:e['impl_ver']='obsolete-verifier-version'
changed,affected,diag=li.analyze(edges=[edge])
results['obsolete_impl_version']={'id':edge['id'],'n_affected':len(affected),'changed_sources':sorted(changed)}
# Simulate independent reference-table hash change without touching files.
orig=sr.hash_map;hm=orig();changed_hashes=dict(hm);changed_hashes['dimension_table']='0'*64
sr.hash_map=lambda base:changed_hashes
try:
 changed,affected,diag=li.analyze(edges=g['edges'])
 results['dimension_table_hash_change']={'affected':len(affected),'source_distribution':dict(collections.Counter(e.get('props',{}).get('source')for e in affected)),'diag':diag}
finally:sr.hash_map=orig
results['raw_node_examples']=[{k:n.get(k)for k in ['id','labels','props']}for n in g['nodes'][:2]]
(OUT/'targeted_evidence.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in results.items()if k not in ['evidence_impl_source_counts','raw_node_examples']},ensure_ascii=False,indent=2))
