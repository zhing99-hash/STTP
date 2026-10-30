"""Synthetic tests: mocked reviewers are never real approvals."""
import copy
import unittest

import curation
import drafts


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.data = {'nodes': [
            {'id': 'x', 'labels': ['Formula'], 'props': {'name': 'X', 'latex': 'x=1'}},
            {'id': 'y', 'labels': ['Symbol'], 'props': {'name': 'Y'}},
        ], 'edges': [{'id': 'xy', 'source': 'x', 'target': 'y', 'type': 'has_symbol', 'props': {}}]}
        self.nodes = {n['id']: n for n in self.data['nodes']}
        self.candidate = curation.stage(self.data, self.nodes, 'sourcehash', ['x', 'y'])
        self.draft = drafts.create(self.candidate, self.data, self.nodes, 'sourcehash')
        ref = {'title': 'Synthetic source', 'url': 'https://example.org/reference'}
        for item in self.draft['nodes']:
            item['proposal'].update({'title': item['id'], 'type': '概念', 'domain': '数学',
                                     'description': 'Synthetic description', 'conditions': 'Synthetic condition', 'source': ref})
        self.draft['edges'][0]['proposal'].update({'discipline': '数学', 'relation': '包含',
                                                  'explanation': 'Synthetic', 'condition': 'Synthetic', 'reference': ref})

    def approve_fixture(self):
        _, hashes = drafts.inspect(self.draft, self.data, self.nodes, 'sourcehash')
        for kind in ('nodes', 'edges'):
            for item in self.draft[kind]:
                for role in ('scientific', 'license'):
                    item['review'][role] = {'decision': 'approved', 'reviewer': 'synthetic-test', 'date': '2026-10-29',
                                            'scope': 'fixture', 'evidence': 'synthetic',
                                            'content_sha256': hashes[f'{kind}:{item["id"]}']}

    def test_pending_never_builds(self):
        blockers, _ = drafts.inspect(self.draft, self.data, self.nodes, 'sourcehash')
        self.assertEqual(len(blockers), 6)
        with self.assertRaises(ValueError):
            drafts.bundle(self.draft, self.data, self.nodes, 'sourcehash')

    def test_complete_synthetic_records_allow_offline_bundle_only(self):
        self.approve_fixture()
        blockers, _ = drafts.inspect(self.draft, self.data, self.nodes, 'sourcehash')
        self.assertEqual(blockers, [])
        bundle = drafts.bundle(self.draft, self.data, self.nodes, 'sourcehash')
        self.assertEqual(len(bundle['nodes']), 2)
        self.assertIn('不得自动公开', bundle['notice'])

    def test_change_invalidates_node_and_adjacent_edge_reviews(self):
        self.approve_fixture()
        self.draft['nodes'][0]['proposal']['description'] = 'Changed explanation'
        blockers, _ = drafts.inspect(self.draft, self.data, self.nodes, 'sourcehash')
        self.assertTrue(any('nodes:x scientific 审核不匹配' in b for b in blockers))
        self.assertTrue(any('edges:xy scientific 审核不匹配' in b for b in blockers))
        with self.assertRaises(ValueError):
            drafts.bundle(self.draft, self.data, self.nodes, 'sourcehash')

    def test_original_and_direction_cannot_be_silently_changed(self):
        self.draft['nodes'][0]['snapshot']['fields']['name'] = 'Edited original'
        self.draft['edges'][0]['proposal']['source'] = 'y'
        blockers, _ = drafts.inspect(self.draft, self.data, self.nodes, 'sourcehash')
        self.assertTrue(any('原始摘录不匹配' in b for b in blockers))
        self.assertTrue(any('方向与原图不一致' in b for b in blockers))

    def test_changed_source_and_bad_reference_are_blocked(self):
        self.draft['nodes'][0]['proposal']['source']['url'] = 'javascript:alert(1)'
        blockers, _ = drafts.inspect(self.draft, self.data, self.nodes, 'newhash')
        self.assertTrue(any('原图哈希变化' in b for b in blockers))
        self.assertTrue(any('HTTPS' in b for b in blockers))

    def test_tampered_candidate_rejected(self):
        candidate = copy.deepcopy(self.candidate)
        candidate['nodes'][0]['snapshot']['fields']['name'] = 'changed'
        with self.assertRaises(ValueError):
            drafts.create(candidate, self.data, self.nodes, 'sourcehash')

    def test_extra_output_fields_and_bad_date_are_blocked(self):
        self.approve_fixture()
        self.draft['nodes'][0]['proposal']['id'] = 'another'
        self.draft['nodes'][0]['review']['scientific']['date'] = 'not-a-date'
        blockers, _ = drafts.inspect(self.draft, self.data, self.nodes, 'sourcehash')
        self.assertTrue(any('不支持的草稿字段' in b for b in blockers))
        self.assertTrue(any('日期格式无效' in b for b in blockers))

    def test_revision_preserves_parent_and_clears_reviews(self):
        self.approve_fixture()
        revised = drafts.revise_draft(self.draft)
        self.assertEqual(revised['revision'], 2)
        self.assertEqual(revised['parent_draft_sha256'], curation.digest(self.draft))
        self.assertIsNone(revised['nodes'][0]['review']['scientific'])
        self.assertEqual(self.draft['nodes'][0]['review']['scientific']['decision'], 'approved')


if __name__ == '__main__':
    unittest.main(verbosity=2)
