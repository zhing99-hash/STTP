"""Local, bounded exploration of the imported graph. No writes to original data."""
from contextlib import closing
import json
import re
import sqlite3
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

from database_probe import DB

HERE = Path(__file__).resolve().parent
TERMS = json.loads((HERE / 'daily_terms.json').read_text(encoding='utf-8'))
PREFIX = 'raw:'
LABELS = {'Element':'元素', 'Molecule':'分子', 'Formula':'公式', 'Reaction':'反应',
          'Paper':'论文', 'PhysicalQuantity':'物理量', 'Symbol':'符号', 'Constant':'常数',
          'MathConcept':'数学概念', 'Definition':'定义', 'Unit':'单位'}
FILTERS = {'all':None, 'element':'Element', 'molecule':'Molecule', 'formula':'Formula'}


def connect():
    if not DB.is_file():
        raise FileNotFoundError('原图库数据库不存在，请先执行 database_probe.py build')
    con = sqlite3.connect(f'{DB.as_uri()}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    return con


def subject(domain):
    text = str(domain or '').lower()
    for prefix, result in [('phys','物理'),('chem','化学'),('math','数学'),('bio','生物'),('astro','天文'),('engineer','工程')]:
        if text.startswith(prefix): return result
    return '其他'


def info(row):
    raw = json.loads(row['original_json'])
    p = raw.get('props') or {}
    labels = raw.get('labels', [])
    kind = next((LABELS[x] for x in LABELS if x in labels), '知识')
    title = str(p.get('name') or row['id'])[:200]
    title_hints = {'MO:h2o':'水分子', 'MO:co2':'二氧化碳', 'TH:fo:ideal_gas':'理想气体状态方程',
                   'TH:fo:charles':'恒压下的温度与体积', 'TH:fo:boyle':'玻意耳定律',
                   'TH:pq:pressure':'压强', 'TH:fo:first_law':'热力学第一定律',
                   'EM:fo:ohm':'欧姆定律', 'CM:fo:gravity':'万有引力定律', 'CM:fo:momentum':'动量'}
    title_zh = title_hints.get(row['id'], title)
    element_names = {'H':'氢元素', 'O':'氧元素', 'C':'碳元素', 'N':'氮元素', 'Fe':'铁元素',
                     'Cu':'铜元素', 'Na':'钠元素', 'Cl':'氯元素', 'Ca':'钙元素'}
    if 'Element' in labels and p.get('symbol') in element_names:
        title_zh = element_names[p['symbol']]
    props_conditions = p.get('applicability')
    conditions = '；'.join(map(str, props_conditions[:8])) if isinstance(props_conditions, list) and props_conditions else '原图未提供完整适用条件。'
    description = str(p.get('description') or p.get('meaning') or p.get('informal') or '原图库暂无文字介绍。')[:1200]
    formula = str(p.get('latex') or p.get('formula') or p.get('symbol') or '')[:300]
    if row['id'] == 'BC:mo:atp':
        description = '此条原数据的结构与分子式存在矛盾，公式和结构暂不展示，请以权威来源复核。'
        formula = ''
    papers = []
    for ppr in (p.get('papers') or [])[:5]:
        if not isinstance(ppr, dict): continue
        doi = str(ppr.get('doi') or '')
        if doi.startswith('10.'): doi = 'https://doi.org/' + doi
        try: valid = urlparse(doi).scheme == 'https' and bool(urlparse(doi).hostname)
        except ValueError: valid = False
        if valid and ppr.get('title'):
            papers.append({'title':str(ppr['title'])[:300], 'authors':'原图未记录完整作者',
                           'year':ppr.get('year') or '年份待核对', 'journal':'原图论文线索', 'doi':doi,
                           'url':doi, 'relation':'原图记录的论文线索，主题关联及全文尚需核对。'})
    return {'id':PREFIX+row['id'], 'title':title_zh, 'english':title if title_zh != title else '', 'type':kind,
            'domain':subject(row['domain']), 'formula':formula, 'description':description,
            'conditions':conditions, 'source_info':str(row['source'] or '未记录')[:150],
            'source':None, 'papers':papers, 'scope':'原图库', 'original_id':row['id'],
            'notice':'原图库本地探索：数据和关系仍在完善，请核对来源及条件。'}


def normalized(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(text))).casefold()

def case_sensitive(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(text)))


def search(question, category='all', offset=0, limit=8):
    query = question.strip()
    if not query or len(query) > 100 or category not in FILTERS or offset < 0 or not 1 <= limit <= 20:
        raise ValueError('查询或筛选条件无效')
    target_label = FILTERS[category]
    term = normalized(query)
    results, seen, clarifications = [], set(), []
    with closing(connect()) as con:
        def add(row, reason):
            if not row or row['id'] in seen: return
            if target_label and target_label not in json.loads(row['labels']): return
            seen.add(row['id'])
            item = info(row)
            item['match'] = reason
            results.append(item)
        def by_id(item_id, reason):
            add(con.execute('SELECT * FROM nodes WHERE id=?', (item_id,)).fetchone(), reason)
        # Exact identifiers and English names are matched before broader hints.
        for row in con.execute('SELECT * FROM nodes WHERE lower(id)=? OR lower(name)=? LIMIT 200', (query.casefold(), query.casefold())):
            add(row, 'ID 或名称匹配')
        # Chemical symbols and formulas are strings; matching does not prove chemical equivalence.
        for row in con.execute("SELECT * FROM nodes WHERE formula<>'' OR labels LIKE '%Element%'"):
            props = json.loads(row['original_json']).get('props') or {}
            labels = json.loads(row['labels'])
            symbol = props.get('symbol') if 'Element' in labels else None
            same_formula = case_sensitive(row['formula']) == case_sensitive(query)
            if term and (row['formula'] and same_formula or symbol and case_sensitive(symbol) == case_sensitive(query)):
                add(row, '符号或公式文本匹配')
        entries = sorted(TERMS['entries'], key=lambda e: max((len(normalized(t)) for t in e['terms'] if normalized(t) in term), default=0), reverse=True)
        for entry in entries:
            matching = [t for t in entry['terms'] if normalized(t) in term]
            if matching:
                for target in entry['targets']: by_id(target, '日常词典：' + max(matching, key=len))
                if entry.get('question'): clarifications.append(entry['question'])
        if len(query) >= 3:
            fts = '"' + query.replace('"', '""') + '"'
            for row in con.execute('SELECT n.* FROM node_fts f JOIN nodes n ON n.id=f.id WHERE node_fts MATCH ? LIMIT 200', (fts,)):
                add(row, '原图文本匹配')
        else:
            for row in con.execute('SELECT * FROM nodes WHERE instr(lower(name),?)>0 OR instr(lower(formula),?)>0 LIMIT 200', (query.casefold(),query.casefold())):
                add(row, '原图文本匹配')
        total = len(results)
        return {'items':results[offset:offset+limit], 'total':total, 'next_offset':offset+limit if offset+limit<total else None,
                'clarifications':list(dict.fromkeys(clarifications))[:3],
                'notice':'本地原图库候选，最多汇集 200 条文本匹配；词典为检索线索，不能替代适用条件判断。'}


def node(node_id, offset=0, limit=8):
    if not isinstance(node_id,str) or len(node_id)>200 or offset<0 or not 1<=limit<=20:
        raise ValueError('节点或分页参数无效')
    with closing(connect()) as con:
        current = con.execute('SELECT * FROM nodes WHERE id=?',(node_id,)).fetchone()
        if not current: return None
        params = (node_id,node_id,node_id)
        sql = 'SELECT * FROM edges WHERE source=? UNION ALL SELECT * FROM edges WHERE target=? AND source<>?'
        total = con.execute('SELECT count(*) FROM ('+sql+')',params).fetchone()[0]
        rows = con.execute('SELECT * FROM ('+sql+') ORDER BY id LIMIT ? OFFSET ?',(*params,limit,offset)).fetchall()
        items=[]
        for row in rows:
            other_id=row['target'] if row['source']==node_id else row['source']
            other=con.execute('SELECT * FROM nodes WHERE id=?',(other_id,)).fetchone()
            if not other: continue
            raw=json.loads(row['original_json'])
            props=raw.get('props') or {}
            other_info=info(other)
            edge={'id':PREFIX+row['id'],'source':PREFIX+row['source'],'target':PREFIX+row['target'],
                  'discipline':subject(other['domain']), 'relation':row['type'] or '原始关系',
                  'explanation':str(props.get('claim') or '原图记录了这条关系，具体含义请结合来源核查。')[:500],
                  'condition':'原图关系，具体适用条件待核对。','source_ref':None}
            items.append({'edge':edge, 'other':other_info})
        return {'node':info(current),'items':items,'offset':offset,'total':total,
                'next_offset':offset+limit if offset+limit<total else None}
