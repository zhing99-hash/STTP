"""Focused offline checks for the local preview data and HTTP boundary."""
import collections
import hashlib
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
data = json.loads((HERE / 'data.json').read_text(encoding='utf-8'))
nodes = {n['id']: n for n in data['nodes']}
assert len(nodes) == len(data['nodes']), 'duplicate node id'
assert len({e['id'] for e in data['edges']}) == len(data['edges']), 'duplicate edge id'
assert len(set(data['disciplines'])) == len(data['disciplines']), 'duplicate discipline'
assert all(e['source'] in nodes and e['target'] in nodes for e in data['edges']), 'dangling edge'
assert all(e['discipline'] in data['disciplines'] for e in data['edges']), 'unknown subject'
assert all(e.get('explanation') and e.get('condition') and e.get('source_ref') in data['sources'] for e in data['edges']), 'edge missing explanation/condition/source'
assert all(n.get('source') in data['sources'] and n.get('description') and n.get('conditions') for n in data['nodes']), 'node missing content/source'
assert all(key in data['sources'] for n in data['nodes'] for key in n.get('example_sources', [])), 'example source missing'
assert all(all(p.get(k) for k in ('title', 'authors', 'year', 'journal', 'doi', 'url', 'relation')) and p['url'].startswith('https://') for n in data['nodes'] for p in n.get('papers', [])), 'invalid paper metadata'
assert all(t['start'] in nodes and all(i in nodes for i in t['steps']) for t in data['themes']), 'theme has missing node'
assert 'BC:mo:atp' not in nodes and not any('atp' in x.lower() for x in nodes), 'ATP leaked into demo'
sample = json.loads((ROOT / '06_PoC/release/sample/sample_normalized.json').read_text(encoding='utf-8'))
old_ids = {n['id'] for n in sample['nodes']}
assert all(n['legacy_id'] in old_ids for n in data['nodes'] if n.get('legacy_id')), 'legacy mapping missing'
assert all(str(s['url']).startswith('https://') for s in data['sources'].values()), 'source url not https'
for path in ('/demo/', '/demo/app.js', '/demo/style.css', '/demo/data.json', '/vendor/cytoscape.min.js'):
    with urllib.request.urlopen(os.environ.get('STTP_DEMO_TEST_BASE', 'http://127.0.0.1:8773') + path, timeout=5) as response:
        assert response.status == 200, path
for path in ('/etl/normalized.json', '/.env', '/demo/serve.py', '/release/sample/sample_normalized.json', '/demo/%2e%2e/etl/normalized.json', '/demo/curation_work/pilot-with-relations-20261029.json', '/demo/draft_work/pilot-editable-20261029.json'):
    try:
        urllib.request.urlopen(os.environ.get('STTP_DEMO_TEST_BASE', 'http://127.0.0.1:8773') + path, timeout=5)
        raise AssertionError('unexpected exposure ' + path)
    except urllib.error.HTTPError as e:
        assert e.code == 404, (path, e.code)
baseline_path = ROOT / 'review_20261028_full/demo_baseline.json'
if baseline_path.is_file():
    baseline = json.loads(baseline_path.read_text(encoding='utf-8-sig'))
    for item in baseline:
        digest = hashlib.sha256()
        with (ROOT / item['path']).open('rb') as f:
            for block in iter(lambda: f.read(1024 * 1024), b''):
                digest.update(block)
        assert digest.hexdigest().upper() == item['sha256'], 'original changed: ' + item['path']
        assert item['matches'], 'backup hash mismatch: ' + item['path']
else:
    print('SKIP: local original/backup hash comparisons; baseline not distributed')
print('PASS: %d themes, %d nodes, %d edges, %d disciplines' % (len(data['themes']), len(nodes), len(data['edges']), len(data['disciplines'])))
print('PASS: ids, references, conditions, sources, legacy mappings, ATP exclusion')
print('PASS: 5 served resources, 7 blocked paths')
