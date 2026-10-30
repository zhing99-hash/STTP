"""Local-only database retrieval experiment; never serves or changes the original graph."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / 'etl' / 'normalized.json'
WORK = HERE / 'db_work'
DB = WORK / 'original-graph-probe.sqlite'

# Tiny, transparent demo rules, not a general language understanding system.
RULES = [
    (('气球', '受热'), [('TH:fo:charles', '人工规则：气球和受热'), ('TH:fo:ideal_gas', '人工规则：气球和受热')], '请确认压强是否近似恒定，以及气体物质的量是否变化。'),
    (('气体', '压缩'), [('TH:fo:boyle', '人工规则：气体和压缩'), ('TH:fo:ideal_gas', '人工规则：气体和压缩')], '请确认压缩过程是否近似恒温，气体物质的量是否固定。'),
    (('电压', '电流'), [('EM:fo:ohm', '人工规则：电压和电流')], '请说明是电阻、电流还是电压，并确认元件是否遵循欧姆定律。'),
    (('质量', '速度'), [('CM:fo:momentum', '人工规则：质量和速度')], '质量与速度也可能用于动能等公式，需确认具体问题。'),
]


def build():
    WORK.mkdir(exist_ok=True)
    if DB.exists():
        raise FileExistsError(f'目标已存在，拒绝覆盖：{DB}')
    raw = SOURCE.read_bytes()
    graph = json.loads(raw)
    digest = hashlib.sha256(raw).hexdigest()
    tmp = WORK / ('.build-' + uuid.uuid4().hex + '.sqlite')
    fd = os.open(str(tmp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with closing(sqlite3.connect(tmp)) as con, con:
            con.executescript('''
              PRAGMA foreign_keys=ON;
              CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
              CREATE TABLE nodes(id TEXT PRIMARY KEY, name TEXT NOT NULL, labels TEXT NOT NULL,
                 domain TEXT NOT NULL, source TEXT NOT NULL, description TEXT NOT NULL,
                 formula TEXT NOT NULL, original_json TEXT NOT NULL);
              CREATE TABLE edges(id TEXT PRIMARY KEY, source TEXT NOT NULL REFERENCES nodes(id),
                 target TEXT NOT NULL REFERENCES nodes(id), type TEXT NOT NULL, original_json TEXT NOT NULL);
              CREATE INDEX edges_source ON edges(source);
              CREATE INDEX edges_target ON edges(target);
              CREATE VIRTUAL TABLE node_fts USING fts5(id UNINDEXED, search_text, tokenize='trigram');
            ''')
            for node in graph['nodes']:
                p = node.get('props') or {}
                name, desc = str(p.get('name') or ''), str(p.get('description') or p.get('informal') or '')
                formula = str(p.get('latex') or p.get('formula') or '')
                con.execute('INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?)',
                    (node['id'], name, json.dumps(node.get('labels', []), ensure_ascii=False),
                     str(p.get('domain') or ''), str(p.get('source') or ''), desc, formula,
                     json.dumps(node, ensure_ascii=False, separators=(',', ':'))))
                text = ' '.join([node['id'], name, desc, formula, str(p.get('usage') or '')])
                con.execute('INSERT INTO node_fts VALUES (?,?)', (node['id'], text))
            for edge in graph['edges']:
                con.execute('INSERT INTO edges VALUES (?,?,?,?,?)',
                    (edge['id'], edge['source'], edge['target'], str(edge.get('type') or ''),
                     json.dumps(edge, ensure_ascii=False, separators=(',', ':'))))
            con.executemany('INSERT INTO meta VALUES (?,?)', [('source_sha256', digest), ('status', 'internal_unreviewed')])
            if con.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('关系存在悬空端点')
            con.commit()
        os.rename(tmp, DB)  # Fails rather than replacing an existing destination on Windows.
    except BaseException:
        if tmp.exists():
            tmp.unlink()
        raise
    return {'path': str(DB), 'nodes': len(graph['nodes']), 'edges': len(graph['edges']),
            'source_sha256': digest, 'bytes': DB.stat().st_size}


def search(question):
    term = question.strip().casefold()
    if not term or len(term) > 100:
        raise ValueError('请输入 1–100 个字符')
    if not DB.is_file():
        raise FileNotFoundError('索引尚未建立，请先运行 build')
    # The prototype returns only local, explicitly unreviewed search candidates.
    con = sqlite3.connect(f'{DB.as_uri()}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    try:
        results, seen, questions = [], set(), []
        def add(row, reason):
            if row and row['id'] not in seen:
                seen.add(row['id'])
                results.append({'id': row['id'], 'name': row['name'], 'domain': row['domain'],
                    'formula': None if row['id'] == 'BC:mo:atp' else row['formula'],
                    'source': row['source'], 'match': reason,
                    'status': ('已知结构与分子式矛盾，禁用展示并待修复' if row['id'] == 'BC:mo:atp'
                               else '原图候选：科学内容与许可待复核')})
        for triggers, targets, clarification in RULES:
            if all(word in term for word in triggers):
                questions.append(clarification)
                for item_id, reason in targets:
                    add(con.execute('SELECT * FROM nodes WHERE id=?', (item_id,)).fetchone(), reason)
        add(con.execute('SELECT * FROM nodes WHERE lower(id)=?', (term,)).fetchone(), 'ID 完全匹配')
        # Quoted FTS syntax and SQL parameters avoid interpreting user input as a query.
        if len(term) >= 3:
            fts_query = '"' + term.replace('"', '""') + '"'
            for row in con.execute('SELECT n.* FROM node_fts f JOIN nodes n ON n.id=f.id WHERE node_fts MATCH ? LIMIT 20', (fts_query,)):
                add(row, '原图名称、说明或公式中的连续文本匹配')
        else:
            for row in con.execute('SELECT * FROM nodes WHERE instr(lower(name), ?) > 0 OR instr(lower(formula), ?) > 0 LIMIT 20', (term, term)):
                add(row, '原图名称或公式中的短文本匹配')
        source_hash = con.execute("SELECT value FROM meta WHERE key='source_sha256'").fetchone()[0]
        return {'query': question, 'indexed_source_sha256': source_hash,
                'items': results[:10], 'clarifications': list(dict.fromkeys(questions)),
                'notice': '候选检索不证明科学正确或适用于问题；暂无结果不等于该知识不存在。'}
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser(description='STTP 原图库 SQLite 本机检索验证，不公开服务')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('build')
    find = sub.add_parser('search')
    find.add_argument('question')
    args = parser.parse_args()
    value = build() if args.command == 'build' else search(args.question)
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, sqlite3.Error, KeyError) as error:
        print(f'无法完成：{error}')
        raise SystemExit(1)
