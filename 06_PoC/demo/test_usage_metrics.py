"""Use an isolated temporary database; never modify actual user metrics."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import serve
import usage_metrics


class MetricsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.previous = usage_metrics.STORE
        usage_metrics.STORE = Path(cls.tmp.name) / 'metrics.sqlite'
        cls.server = ThreadingHTTPServer(('127.0.0.1',0),serve.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:%d' % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()
        usage_metrics.STORE = cls.previous; cls.tmp.cleanup()

    def post(self, payload, origin):
        req=urllib.request.Request(self.base+'/demo/api/metrics/event',
             data=json.dumps(payload).encode(),method='POST',headers={'Origin':origin,'Content-Type':'text/plain'})
        try:
            with urllib.request.urlopen(req,timeout=5) as response: return response.status, response.headers
        except urllib.error.HTTPError as error: return error.code, error.headers

    def test_aggregates_and_allowlisted_engineering_origin(self):
        for payload in ({'version':'explore','event':'enter'},
                        {'version':'engineering','event':'interface_click'},
                        {'version':'engineering','event':'visible_seconds','seconds':12}):
            status, headers=self.post(payload, 'http://127.0.0.1:8765')
            self.assertEqual(status,204)
            self.assertEqual(headers['Access-Control-Allow-Origin'],'http://127.0.0.1:8765')
        records=usage_metrics.summary()
        self.assertTrue(any(x['version']=='engineering' and x['event']=='visible_seconds' and x['seconds']==12 for x in records))
        self.assertTrue(all(set(x)=={'date','version','event','count','seconds'} for x in records))

    def test_invalid_events_and_origin_are_rejected(self):
        payload={'version':'explore','event':'search','query':'private search'}
        self.assertEqual(self.post(payload,self.base)[0],400)
        self.assertEqual(self.post({'version':'explore','event':'visible_seconds','seconds':31},self.base)[0],400)
        self.assertEqual(self.post({'version':'explore','event':'enter'},'https://example.com')[0],403)


if __name__=='__main__': unittest.main(verbosity=2)
