"""Read-only paper inventory of the normalized graph and demo; no network access."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / '06_PoC/etl/normalized.json'
DEMO = ROOT / '06_PoC/demo/data.json'


def doi(value):
    text = unquote(str(value or '')).strip().lower()
    text = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', text)
    return text if re.match(r'^10\.\d{4,9}/\S+$', text) else None


raw = GRAPH.read_bytes()
graph = json.loads(raw)
demo = json.loads(DEMO.read_text(encoding='utf-8'))
nodes = graph['nodes']
papers = [n for n in nodes if 'Paper' in n.get('labels', [])]
formulas = [n for n in nodes if 'Formula' in n.get('labels', [])]
paper_ids = {n['id'] for n in papers}
formula_ids = {n['id'] for n in formulas}
refs = [(n['id'], p) for n in formulas for p in (n.get('props', {}).get('papers') or []) if isinstance(p,dict)]
paper_dois = [doi(n.get('props',{}).get('doi')) for n in papers]
ref_dois = [doi(p.get('doi')) for _,p in refs]
paper_doi_set = {x for x in paper_dois if x}
ref_doi_set = {x for x in ref_dois if x}
demo_refs = [p for n in demo['nodes'] for p in n.get('papers',[])]
paper_edges = [e for e in graph['edges'] if e.get('source') in paper_ids or e.get('target') in paper_ids]
paper_formula_edges = [e for e in paper_edges if e.get('source') in formula_ids or e.get('target') in formula_ids]
result = {
    'snapshot_sha256': hashlib.sha256(raw).hexdigest(),
    'paper_nodes': len(papers),
    'paper_node_sources': Counter(n.get('props',{}).get('source','未记录') for n in papers),
    'paper_node_types': Counter(n.get('props',{}).get('pub_type','未记录') for n in papers),
    'paper_nodes_with_valid_shape_doi': sum(x is not None for x in paper_dois),
    'paper_node_distinct_doi': len(paper_doi_set),
    'paper_nodes_without_valid_shape_doi': sum(x is None for x in paper_dois),
    'paper_nodes_stored_doi_verified_true': sum(n.get('props',{}).get('doi_verified') is True for n in papers),
    'paper_nodes_oa_status': Counter(n.get('props',{}).get('oa_status','未记录') for n in papers),
    'formula_nodes': len(formulas),
    'formula_papers_status': Counter(n.get('props',{}).get('papers_status','未记录') for n in formulas),
    'formulas_with_refs': len({node_id for node_id,_ in refs}),
    'formula_paper_reference_entries': len(refs),
    'formula_ref_distinct_doi': len(ref_doi_set),
    'formula_ref_missing_doi': sum(x is None for x in ref_dois),
    'formula_ref_doi_overlaps_paper_nodes': len(ref_doi_set & paper_doi_set),
    'paper_formula_edges_by_type': Counter(e.get('type','未记录') for e in paper_formula_edges),
    'paper_edges_by_type': Counter(e.get('type','未记录') for e in paper_edges),
    'demo_nodes_with_papers': sum(bool(n.get('papers')) for n in demo['nodes']),
    'demo_paper_reference_entries': len(demo_refs),
    'demo_distinct_doi': len({doi(p.get('doi')) for p in demo_refs if doi(p.get('doi'))}),
    'note': 'DOI 格式检查不代表 DOI 真实或引用关系正确；节点、内嵌线索和 Demo 不可直接相加。'
}
print(json.dumps(result, ensure_ascii=False, indent=2))
