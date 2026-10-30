"""Checks the local SQLite import and a few explicit retrieval counterexamples."""
import hashlib
import json
import sqlite3
import unittest

import database_probe as probe


@unittest.skipUnless(probe.SOURCE.is_file() and probe.DB.is_file(), '需要原图文件及已构建的数据库')
class ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = json.loads(probe.SOURCE.read_text(encoding='utf-8'))
        cls.nodes = {n['id']: n for n in cls.graph['nodes']}

    def test_import_counts_and_source_hash(self):
        with sqlite3.connect(f'{probe.DB.as_uri()}?mode=ro', uri=True) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM nodes').fetchone()[0], len(self.graph['nodes']))
            self.assertEqual(con.execute('SELECT count(*) FROM edges').fetchone()[0], len(self.graph['edges']))
            self.assertIsNone(con.execute('PRAGMA foreign_key_check').fetchone())
            source_hash = con.execute("SELECT value FROM meta WHERE key='source_sha256'").fetchone()[0]
            self.assertEqual(source_hash, hashlib.sha256(probe.SOURCE.read_bytes()).hexdigest())
            for item_id in ('TH:fo:ideal_gas', 'MO:h2o', 'BC:mo:atp'):
                stored = con.execute('SELECT original_json FROM nodes WHERE id=?', (item_id,)).fetchone()[0]
                self.assertEqual(json.loads(stored), self.nodes[item_id])

    def test_rule_returns_candidates_and_condition(self):
        result = probe.search('气球受热体积变大是什么原因')
        self.assertEqual([x['id'] for x in result['items'][:2]], ['TH:fo:charles', 'TH:fo:ideal_gas'])
        self.assertTrue(result['clarifications'])

    def test_out_of_vocabulary_question_fails_honestly(self):
        result = probe.search('水分子有几个原子')
        self.assertEqual(result['items'], [])
        self.assertIn('不等于', result['notice'])

    def test_known_bad_atp_marked_and_formula_hidden(self):
        item = next(x for x in probe.search('ATP')['items'] if x['id'] == 'BC:mo:atp')
        self.assertIn('矛盾', item['status'])
        self.assertIsNone(item['formula'])

    def test_id_and_input_boundary(self):
        self.assertEqual(probe.search('TH:fo:ideal_gas')['items'][0]['id'], 'TH:fo:ideal_gas')
        for value in ('', 'x' * 101):
            with self.assertRaises(ValueError):
                probe.search(value)
        # SQL metacharacters are data rather than SQL.
        self.assertEqual(probe.search("' OR 1=1 --")['items'], [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
