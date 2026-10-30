"""Read-only consistency checks for the paper-pilot review pack."""
import hashlib
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / '06_PoC/etl/normalized.json'
POOL = ROOT / '11_真实数据/phase68_paper_mentions.json'
PACK = ROOT / 'review_20261028_full/paper_pilot_20261030/paper_pilot.json'


class PilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = json.loads(GRAPH.read_bytes())
        cls.pool = json.loads(POOL.read_bytes())
        cls.pack = json.loads(PACK.read_bytes())

    def test_inputs_match_snapshot(self):
        self.assertEqual(self.pack['graph_sha256'], hashlib.sha256(GRAPH.read_bytes()).hexdigest())
        self.assertEqual(self.pack['candidate_pool_sha256'], hashlib.sha256(POOL.read_bytes()).hexdigest())

    def test_seeds_are_unique_and_traceable(self):
        nodes={n['id']:n for n in self.graph['nodes']}
        pools={n['fid']:n for n in self.pool['formulas']}
        seeds=self.pack['seeds']
        self.assertEqual(len(seeds),37)
        self.assertEqual(len({s['id'] for s in seeds}),len(seeds))
        for seed in seeds:
            self.assertIn(seed['id'],nodes)
            self.assertIn(seed['id'],pools)
            self.assertEqual(seed['candidate_count_in_existing_pool'],len(pools[seed['id']]['candidates']))
            self.assertLessEqual(len(seed['selected_existing_candidates']),3)
            self.assertIn('待审',seed['review'])

    def test_new_references_not_in_graph_and_are_unapproved(self):
        graph_dois={str(n.get('props',{}).get('doi','')).lower() for n in self.graph['nodes'] if 'Paper' in n.get('labels',[])}
        for paper in self.pack['new_publisher_page_candidates']:
            self.assertTrue(re.match(r'^10\.\d{4,9}/\S+$',paper['doi']))
            self.assertFalse(any(paper['doi'].lower() in existing for existing in graph_dois))
            self.assertFalse(paper['in_original_paper_nodes'])
            self.assertIn('待独立核对',paper['review'])
            self.assertEqual(paper['target_id'],'CE:fo:arrhenius')


if __name__ == '__main__': unittest.main(verbosity=2)
