"""Test the staging boundary using synthetic records; no approvals are created."""
import copy
import unittest

import curation


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.data = {'nodes': [
            {'id': 'x', 'labels': ['Formula'], 'props': {'name': 'Example', 'latex': 'x=1', 'source': 'test'}},
            {'id': 'y', 'labels': ['Symbol'], 'props': {'name': 'x'}},
        ], 'edges': [{'id': 'xy', 'source': 'x', 'target': 'y', 'type': 'has_symbol', 'props': {}}]}
        self.nodes = {n['id']: n for n in self.data['nodes']}
        self.candidate = curation.stage(self.data, self.nodes, 'hash', ['x', 'y'])

    def test_candidate_defaults_pending_and_keeps_relation(self):
        self.assertEqual(len(self.candidate['edges']), 1)
        blockers = curation.inspect(self.candidate, self.data, self.nodes, 'hash')
        self.assertEqual(len(blockers), 6)
        self.assertIsNone(self.candidate['nodes'][0]['review']['scientific'])

    def test_missing_and_duplicate_inputs_rejected(self):
        for ids in ([], ['x', 'x'], ['missing']):
            with self.assertRaises(ValueError):
                curation.stage(self.data, self.nodes, 'hash', ids)

    def test_detects_changed_source_and_snapshot(self):
        candidate = copy.deepcopy(self.candidate)
        candidate['nodes'][0]['snapshot']['fields']['latex'] = 'x=2'
        candidate['nodes'][0]['snapshot_sha256'] = curation.digest(candidate['nodes'][0]['snapshot'])
        blockers = curation.inspect(candidate, self.data, self.nodes, 'changed')
        self.assertTrue(any('原图文件哈希变化' in b for b in blockers))
        self.assertTrue(any('原始摘录已更改' in b for b in blockers))

    def test_incomplete_review_is_blocked(self):
        self.candidate['nodes'][0]['review']['scientific'] = {'decision': 'approved'}
        blockers = curation.inspect(self.candidate, self.data, self.nodes, 'hash')
        self.assertTrue(any('缺少 reviewer' in b for b in blockers))
        self.assertTrue(any('不是针对当前摘录' in b for b in blockers))

    def test_structural_review_is_not_authentication(self):
        for item in self.candidate['nodes'] + self.candidate['edges']:
            for role in ('scientific', 'license'):
                item['review'][role] = {'decision': 'approved', 'reviewer': 'synthetic-test',
                                        'date': '2026-10-29', 'evidence': 'synthetic',
                                        'scope': 'fixture', 'snapshot_sha256': item['snapshot_sha256']}
        self.assertEqual(curation.inspect(self.candidate, self.data, self.nodes, 'hash'), [])
        # Passing this synthetic fixture means only that required fields are present.

    def test_rejects_edge_without_selected_endpoint(self):
        self.candidate['nodes'].pop()
        blockers = curation.inspect(self.candidate, self.data, self.nodes, 'hash')
        self.assertTrue(any('端点不在候选中' in b for b in blockers))


if __name__ == '__main__':
    unittest.main(verbosity=2)
