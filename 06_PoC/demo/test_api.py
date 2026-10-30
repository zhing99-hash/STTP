"""Exercise the local API, including paging, without touching the data files."""
import json
from decimal import Decimal
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import serve


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), serve.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:%d/demo/api/' % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def get(self, endpoint, **params):
        url = self.base + endpoint + '?' + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def test_config_is_metadata_only(self):
        status, result = self.get('config')
        self.assertEqual(status, 200)
        self.assertEqual(len(result['themes']), 3)
        self.assertNotIn('nodes', result)
        self.assertNotIn('edges', result)

    def test_search_and_pagination(self):
        status, result = self.get('search', q='a', limit=1)
        self.assertEqual(status, 200)
        self.assertEqual(len(result['items']), 1)
        self.assertGreater(result['total'], 1)
        self.assertEqual(result['next_offset'], 1)
        _, second = self.get('search', q='a', limit=1, offset=1)
        self.assertNotEqual(result['items'][0]['id'], second['items'][0]['id'])
        _, none = self.get('search', q='不存在的知识')
        self.assertEqual(none['items'], [])
        self.assertIsNone(none['next_offset'])

    def test_node_relation_pages_and_direction(self):
        status, first = self.get('node', id='TH:fo:ideal_gas', limit=2)
        self.assertEqual(status, 200)
        self.assertEqual(first['total'], 9)
        self.assertEqual(first['next_offset'], 2)
        _, second = self.get('node', id='TH:fo:ideal_gas', limit=2, offset=2)
        batches = [first, second]
        while batches[-1]['next_offset'] is not None:
            _, following = self.get('node', id='TH:fo:ideal_gas', limit=2, offset=batches[-1]['next_offset'])
            batches.append(following)
        last = batches[-1]
        ids = [item['edge']['id'] for batch in batches for item in batch['items']]
        self.assertEqual(len(ids), 9)
        self.assertEqual(len(set(ids)), 9)
        self.assertIsNone(last['next_offset'])
        _, reverse = self.get('node', id='demo:amount')
        item = next(x for x in reverse['items'] if x['edge']['id'] == 'e2')
        self.assertEqual(item['other']['id'], 'demo:molar_mass')
        self.assertEqual(item['edge']['target'], 'demo:amount')

    def test_invalid_inputs(self):
        for params in ({'id':'bad'},):
            self.assertEqual(self.get('node', **params)[0], 404)
        for params in ({'id':'MO:h2o','limit':21}, {'id':'MO:h2o','offset':-1}, {'id':'MO:h2o','limit':'abc'}):
            self.assertEqual(self.get('node', **params)[0], 400)
        self.assertEqual(self.get('search', q='')[0], 400)
        self.assertEqual(self.get('search', q='a'*101)[0], 400)
        self.assertEqual(self.get('unknown')[0], 404)

    def test_water_example_arithmetic(self):
        amount = Decimal('250') / Decimal('18.0')
        entities = amount * Decimal('6.02214076e23')
        self.assertLess(abs(amount - Decimal('13.9')), Decimal('0.05'))
        self.assertLess(abs(entities - Decimal('8.36e24')), Decimal('0.005e24'))

    def test_paper_metadata_and_cross_subject_path(self):
        status, node = self.get('node', id='demo:beer_lambert')
        self.assertEqual(status, 200)
        self.assertEqual(node['node']['papers'][0]['doi'], '10.1098/rsos.211103')
        self.assertTrue(node['node']['papers'][0]['url'].startswith('https://'))
        self.assertTrue(any(x['other']['id'] == 'demo:molarity' and x['edge']['discipline'] == '化学' for x in node['items']))
        self.assertTrue(any(x['other']['id'] == 'demo:absorbance' and x['edge']['discipline'] == '物理' for x in node['items']))

    def test_private_sqlite_is_not_served(self):
        url = 'http://127.0.0.1:%d/demo/db_work/original-graph-probe.sqlite' % self.server.server_port
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(url, timeout=5)
        self.assertEqual(error.exception.code, 404)

    @unittest.skipUnless(serve.graph_database.DB.is_file(), '原图库数据库未构建')
    def test_database_element_molecule_and_formula_search(self):
        for query, category, expected in (('氧','element','raw:EK:el:O'), ('H₂O','molecule','raw:MO:h2o'),
                                          ('PV=nRT','formula','raw:TH:fo:ideal_gas')):
            status, result = self.get('raw/search', q=query, category=category)
            self.assertEqual(status, 200)
            self.assertIn(expected, [item['id'] for item in result['items']])
        _, cobalt = self.get('raw/search', q='Co', category='element')
        self.assertEqual(cobalt['items'][0]['id'], 'raw:EK:el:Co')
        self.assertEqual(cobalt['items'][0]['match'], '符号或公式文本匹配')
        _, uppercase = self.get('raw/search', q='CO', category='element')
        self.assertFalse(any(n['id']=='raw:EK:el:Co' and n['match']=='符号或公式文本匹配' for n in uppercase['items']))

    @unittest.skipUnless(serve.graph_database.DB.is_file(), '原图库数据库未构建')
    def test_daily_language_and_underlying_graph(self):
        status, result = self.get('raw/search', q='气球受热体积变大', category='all')
        self.assertEqual(status, 200)
        self.assertIn('raw:TH:fo:charles', [item['id'] for item in result['items']])
        self.assertTrue(result['clarifications'])
        _, water = self.get('raw/search', q='水分子有几个原子', category='molecule')
        self.assertIn('raw:MO:h2o', [item['id'] for item in water['items']])
        _, node = self.get('raw/node', id='MO:h2o', limit=2)
        self.assertEqual(node['node']['id'], 'raw:MO:h2o')
        self.assertGreater(node['total'], 2)
        self.assertEqual(node['next_offset'], 2)
        self.assertTrue(all(e['edge']['source'].startswith('raw:') for e in node['items']))

    @unittest.skipUnless(serve.graph_database.DB.is_file(), '原图库数据库未构建')
    def test_quarantined_atp_and_raw_input_limits(self):
        _, node = self.get('raw/node', id='BC:mo:atp')
        self.assertEqual(node['node']['formula'], '')
        self.assertIn('矛盾', node['node']['description'])
        self.assertEqual(self.get('raw/search', q='水', category='not-a-category')[0], 400)
        self.assertEqual(self.get('raw/node', id='no-such-node')[0], 404)


if __name__ == '__main__':
    unittest.main(verbosity=2)
