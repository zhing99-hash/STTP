"""Serve only this local preview and the required vendored Cytoscape file."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import argparse
import json
import mimetypes
import sqlite3
import graph_database
import usage_metrics

HERE = Path(__file__).resolve().parent
FILES = {
    '/demo/': HERE / 'index.html',
    '/demo/index.html': HERE / 'index.html',
    '/demo/app.js': HERE / 'app.js',
    '/demo/style.css': HERE / 'style.css',
    '/demo/data.json': HERE / 'data.json',
    '/demo/usage.js': HERE.parent / 'js' / 'sttp_usage.js',
    '/vendor/cytoscape.min.js': HERE.parent / 'vendor' / 'cytoscape.min.js',
}

# This index is deliberately limited to the curated demo file. It is not an
# authorization mechanism or a production index for the unreviewed corpus.
DATA = json.loads((HERE / 'data.json').read_text(encoding='utf-8'))
NODES = {node['id']: node for node in DATA['nodes']}
ADJACENCY = {node_id: [] for node_id in NODES}
for edge in DATA['edges']:
    ADJACENCY[edge['source']].append(edge)
    if edge['target'] != edge['source']:
        ADJACENCY[edge['target']].append(edge)

def page_params(query):
    try:
        offset = int(query.get('offset', ['0'])[0])
        limit = int(query.get('limit', ['8'])[0])
        if offset < 0 or limit < 1 or limit > 20:
            raise ValueError
        return offset, limit
    except (ValueError, TypeError):
        raise ValueError('offset must be nonnegative and limit must be between 1 and 20')

def page(items, offset, limit):
    end = offset + limit
    return {'items': items[offset:end], 'total': len(items),
            'offset': offset, 'next_offset': end if end < len(items) else None}

def api(path, query):
    if path == '/demo/api/config':
        themes = [{**theme, 'step_titles': {node_id: NODES[node_id]['title'] for node_id in theme['steps']}} for theme in DATA['themes']]
        return 200, {'version': 'sttp-explore-demo-0.4', 'notice': DATA['notice'], 'database_available': graph_database.DB.is_file(),
                     'disciplines': DATA['disciplines'], 'sources': DATA['sources'], 'themes': themes}
    if path == '/demo/api/metrics':
        return 200, {'items': usage_metrics.summary(), 'notice': '仅统计自愿开启的本机页面点击和前台时间；不等于独立用户数量。'}
    if path == '/demo/api/raw/search':
        offset, limit = page_params(query)
        return 200, graph_database.search(query.get('q', [''])[0], query.get('category', ['all'])[0], offset, limit)
    if path == '/demo/api/raw/node':
        offset, limit = page_params(query)
        result = graph_database.node(query.get('id', [''])[0], offset, limit)
        return (200, result) if result else (404, {'error': 'node not found'})
    if path == '/demo/api/search':
        offset, limit = page_params(query)
        term = query.get('q', [''])[0].strip().casefold()
        if not term or len(term) > 100:
            raise ValueError('query must contain 1 to 100 characters')
        matches = []
        for node in DATA['nodes']:
            fields = [node['title'], node.get('english', ''), node.get('formula', ''), node['id'], *node.get('aliases', [])]
            if any(term in str(field).casefold() for field in fields):
                matches.append({key: node.get(key, '') for key in ('id', 'title', 'english', 'type')})
        return 200, page(matches, offset, limit)
    if path == '/demo/api/node':
        offset, limit = page_params(query)
        node_id = query.get('id', [''])[0]
        if node_id not in NODES:
            return 404, {'error': 'node not found'}
        result = page(ADJACENCY[node_id], offset, limit)
        result['items'] = [{'edge': edge, 'other': NODES[edge['target'] if edge['source'] == node_id else edge['source']]} for edge in result['items']]
        result['node'] = NODES[node_id]
        return 200, result
    return 404, {'error': 'not found'}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Avoid putting search query strings and client addresses into console logs.
        pass

    def do_POST(self):
        if urlsplit(self.path).path != '/demo/api/metrics/event':
            self.send_error(404); return
        origin = self.headers.get('Origin', '')
        allowed = {f'http://127.0.0.1:{self.server.server_port}', 'http://127.0.0.1:8765'}
        if origin not in allowed:
            self.send_error(403); return
        try:
            length = int(self.headers.get('Content-Length', '-1'))
            if not 0 < length <= 512: raise ValueError('invalid length')
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
            usage_metrics.record(payload)
        except (ValueError, UnicodeError, json.JSONDecodeError):
            self.send_error(400); return
        except (OSError, sqlite3.Error):
            self.send_error(503); return
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', origin)
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()

    def do_GET(self):
        url = urlsplit(self.path)
        requested = url.path
        if requested.startswith('/demo/api/'):
            try:
                status, payload = api(requested, parse_qs(url.query, keep_blank_values=True))
            except ValueError as error:
                status, payload = 400, {'error': str(error)}
            except (OSError, sqlite3.Error):
                status, payload = 503, {'error': '本机原图库不可用，请检查数据库是否已创建'}
            body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)
            return
        if requested in ('/', '/demo'):
            self.send_response(302)
            self.send_header('Location', '/demo/')
            self.end_headers()
            return
        path = FILES.get(requested)
        if not path or not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        self.send_response(200)
        mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        self.send_header('Content-Type', mime + ('; charset=utf-8' if mime.startswith('text/') or mime in ('application/json', 'application/javascript') else ''))
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(data)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='STTP 科学之间本机预览')
    parser.add_argument('--port', type=int, default=8773)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'STTP 本地预览：http://127.0.0.1:{args.port}/demo/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
