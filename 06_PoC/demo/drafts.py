"""Editable, provenance-bound drafts and an offline, non-publishing bundle."""
import argparse
import copy
from datetime import date, datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlparse

import curation

HERE = Path(__file__).resolve().parent
WORK = HERE / 'draft_work'
DISCIPLINES = ['数学', '物理', '化学', '生物', '天文', '工程']


def valid_https(url):
    if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
        return parsed.scheme == 'https' and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def material(item, source_hash, node_proposals=None):
    result = {'source_sha256': source_hash, 'id': item['id'],
              'original_record_sha256': item['original_record_sha256'],
              'original_snapshot_sha256': item['snapshot_sha256'], 'proposal': item['proposal']}
    if node_proposals is not None:
        proposal = item['proposal']
        result['endpoint_proposals'] = {key: node_proposals.get(proposal.get(key)) if isinstance(proposal.get(key), str) else None for key in ('source', 'target')}
    return result


def fingerprint(item, source_hash, node_proposals=None):
    return curation.digest(material(item, source_hash, node_proposals))


def create(candidate, data, nodes, source_hash):
    if candidate.get('schema') != 'sttp-curation-candidate-v1' or candidate.get('source_sha256') != source_hash:
        raise ValueError('候选格式不符或原图版本已变化')
    edges = {e['id']: e for e in data['edges']}
    items = {}
    for kind, originals in (('nodes', nodes), ('edges', edges)):
        items[kind] = []
        seen = set()
        for item in candidate.get(kind, []):
            record = originals.get(item['id'])
            if item['id'] in seen or not record or curation.digest(record) != item.get('original_record_sha256'):
                raise ValueError('候选记录重复、缺失或已变化：' + item['id'])
            if curation.snapshot(record, 'node' if kind == 'nodes' else 'edge') != item.get('snapshot') or curation.digest(item['snapshot']) != item.get('snapshot_sha256'):
                raise ValueError('候选原始摘录不匹配：' + item['id'])
            seen.add(item['id'])
            if kind == 'nodes':
                proposal = {'title': '', 'english': '', 'type': '', 'domain': '',
                            'formula': '', 'description': '', 'conditions': '', 'source': {'title': '', 'url': ''}}
            else:
                proposal = {'source': record['source'], 'target': record['target'], 'discipline': '',
                            'relation': '', 'explanation': '', 'condition': '', 'reference': {'title': '', 'url': ''}}
            items[kind].append({'id': item['id'], 'original_record_sha256': item['original_record_sha256'],
                                'snapshot': copy.deepcopy(item['snapshot']), 'snapshot_sha256': item['snapshot_sha256'],
                                'proposal': proposal, 'review': {'scientific': None, 'license': None}})
    if not items['nodes']:
        raise ValueError('不能创建空草稿')
    ids = {item['id'] for item in items['nodes']}
    if any(e['proposal']['source'] not in ids or e['proposal']['target'] not in ids for e in items['edges']):
        raise ValueError('候选关系的端点不在候选节点中')
    return {'schema': 'sttp-editable-draft-v1', 'revision': 1, 'created_utc': datetime.now(timezone.utc).isoformat(),
            'source_sha256': source_hash, 'notice': '内部草稿；未完成独立科学与许可审核，不得公开。', **items}


def fill_from_demo(draft, demo):
    # The temperature mapping is a working interpretation of a source symbol;
    # it is intentionally not an identity assertion and remains pending review.
    mapping = {'MO:h2o': 'MO:h2o', 'TH:fo:ideal_gas': 'TH:fo:ideal_gas',
               'TH:fo:charles': 'TH:fo:charles', 'TH:sy:T': 'demo:temperature'}
    edge_map = {'has_symbol|TH:fo:ideal_gas|TH:sy:T': 'e5',
                'has_symbol|TH:fo:charles|TH:sy:T': 'e10'}
    demo_nodes = {n['id']: n for n in demo['nodes']}
    demo_edges = {e['id']: e for e in demo['edges']}
    for item in draft['nodes']:
        source = demo_nodes.get(mapping.get(item['id']))
        if source:
            item['proposal'] = {k: copy.deepcopy(source.get(k, '')) for k in
                                ('title', 'english', 'type', 'domain', 'formula', 'description', 'conditions')}
            item['proposal']['source'] = copy.deepcopy(demo['sources'][source['source']])
            item['proposal']['draft_note'] = ('由现有 Demo 内容提议映射至原图，语义等同性待审。' if item['id'] == 'TH:sy:T'
                                             else '由现有 Demo 内容提议，仍待独立审核。')
    for item in draft['edges']:
        source = demo_edges.get(edge_map.get(item['id']))
        if source:
            proposal = item['proposal']
            proposal.update({k: source[k] for k in ('discipline', 'relation', 'explanation', 'condition')})
            proposal['reference'] = copy.deepcopy(demo['sources'][source['source_ref']])
    return draft


def inspect(draft, data, nodes, source_hash):
    errors = []
    if not isinstance(draft, dict) or draft.get('schema') != 'sttp-editable-draft-v1':
        return ['草稿格式不受支持'], {}
    if draft.get('source_sha256') != source_hash:
        errors.append('原图哈希变化；需重新建立草稿并复核')
    raw_nodes, raw_edges = draft.get('nodes'), draft.get('edges')
    if not isinstance(raw_nodes, list) or not raw_nodes or not isinstance(raw_edges, list):
        return errors + ['节点或关系列表不合法'], {}
    originals = {'nodes': nodes, 'edges': {e['id']: e for e in data['edges']}}
    if any(not isinstance(x, dict) for x in raw_nodes + raw_edges):
        return errors + ['记录必须是对象'], {}
    proposals = {n.get('id'): n.get('proposal') for n in raw_nodes if isinstance(n.get('id'), str)}
    fingerprints = {}
    for kind, entries in (('nodes', raw_nodes), ('edges', raw_edges)):
        seen = set()
        for item in entries:
            item_id = item.get('id')
            label = f'{kind}:{item_id}'
            if not isinstance(item_id, str) or not item_id or item_id in seen:
                errors.append(f'{label} ID 无效或重复')
                continue
            seen.add(item_id)
            original = originals[kind].get(item_id)
            if not original or curation.digest(original) != item.get('original_record_sha256'):
                errors.append(f'{label} 原始记录缺失或改变')
            elif curation.snapshot(original, 'node' if kind == 'nodes' else 'edge') != item.get('snapshot'):
                errors.append(f'{label} 原始摘录不匹配')
            if curation.digest(item.get('snapshot')) != item.get('snapshot_sha256'):
                errors.append(f'{label} 原始摘录哈希不匹配')
            p = item.get('proposal')
            if not isinstance(p, dict):
                errors.append(f'{label} 草稿内容必须是对象')
                continue
            allowed = (('title', 'english', 'type', 'domain', 'formula', 'description', 'conditions', 'source', 'draft_note')
                       if kind == 'nodes' else ('source', 'target', 'discipline', 'relation', 'explanation', 'condition', 'reference'))
            if set(p) - set(allowed):
                errors.append(f'{label} 含有不支持的草稿字段')
            if kind == 'nodes' and any(not isinstance(p.get(k, ''), str) for k in ('english', 'formula', 'draft_note')):
                errors.append(f'{label} 可选文本字段格式无效')
            required = ('title', 'type', 'domain', 'description', 'conditions') if kind == 'nodes' else ('source', 'target', 'discipline', 'relation', 'explanation', 'condition')
            for key in required:
                if not isinstance(p.get(key), str) or not p[key].strip():
                    errors.append(f'{label} 缺少 {key}')
            if p.get('domain' if kind == 'nodes' else 'discipline') not in DISCIPLINES:
                errors.append(f'{label} 学科不在配置范围')
            reference = p.get('source' if kind == 'nodes' else 'reference')
            if not isinstance(reference, dict) or not str(reference.get('title', '')).strip() or not valid_https(reference.get('url')):
                errors.append(f'{label} 缺少有效的 HTTPS 来源标题或地址')
            if kind == 'edges':
                if original and (p.get('source') != original['source'] or p.get('target') != original['target']):
                    errors.append(f'{label} 端点或方向与原图不一致')
                if any(not isinstance(p.get(key), str) or p[key] not in proposals for key in ('source', 'target')):
                    errors.append(f'{label} 端点不在草稿中')
            review_hash = fingerprint(item, source_hash, proposals if kind == 'edges' else None)
            fingerprints[label] = review_hash
            review = item.get('review') or {}
            for role in ('scientific', 'license'):
                record = review.get(role) if isinstance(review, dict) else None
                if not isinstance(record, dict) or record.get('decision') != 'approved':
                    errors.append(f'{label} 缺少 {role} 审核通过记录')
                    continue
                for key in ('reviewer', 'date', 'evidence', 'scope'):
                    if not isinstance(record.get(key), str) or not record[key].strip():
                        errors.append(f'{label} {role} 缺少 {key}')
                try:
                    value = record.get('date', '')
                    if date.fromisoformat(value).isoformat() != value:
                        raise ValueError
                except (ValueError, TypeError):
                    errors.append(f'{label} {role} 日期格式无效')
                if record.get('content_sha256') != review_hash:
                    errors.append(f'{label} {role} 审核不匹配当前修订内容')
    return errors, fingerprints


def bundle(draft, data, nodes, source_hash):
    errors, hashes = inspect(draft, data, nodes, source_hash)
    if errors:
        raise ValueError('草稿仍有 %d 项缺陷，不能生成内部候选包' % len(errors))
    manifest = []
    for kind in ('nodes', 'edges'):
        for item in draft[kind]:
            manifest.append({'kind': kind, 'id': item['id'], 'original_record_sha256': item['original_record_sha256'],
                             'snapshot_sha256': item['snapshot_sha256'], 'content_sha256': hashes[f'{kind}:{item["id"]}'],
                             'review': copy.deepcopy(item['review'])})
    return {'schema': 'sttp-internal-candidate-v1',
            'notice': '仅为内部候选包：字段通过不证明身份、授权或科学正确；不得自动公开。',
            'source_sha256': source_hash, 'manifest': manifest,
            'nodes': [{'id': n['id'], **copy.deepcopy(n['proposal'])} for n in draft['nodes']],
            'edges': [{'id': e['id'], **copy.deepcopy(e['proposal'])} for e in draft['edges']]}


def path_in(work, name):
    if not isinstance(name, str) or Path(name).name != name or not name.lower().endswith('.json'):
        raise ValueError('只允许目录内的 JSON 文件名')
    path = work / name
    if path.is_symlink():
        raise ValueError('不允许符号链接')
    return path


def revise_draft(draft):
    result = copy.deepcopy(draft)
    if result.get('schema') != 'sttp-editable-draft-v1':
        raise ValueError('草稿格式不受支持')
    result['parent_draft_sha256'] = curation.digest(draft)
    result['revision'] = int(draft.get('revision', 1)) + 1
    result['created_utc'] = datetime.now(timezone.utc).isoformat()
    for item in result['nodes'] + result['edges']:
        item['review'] = {'scientific': None, 'license': None}
    return result


def main():
    parser = argparse.ArgumentParser(description='STTP 内部知识草稿，不自动发布')
    sub = parser.add_subparsers(dest='command', required=True)
    new = sub.add_parser('new')
    new.add_argument('--candidate', required=True)
    new.add_argument('--name', required=True)
    new.add_argument('--seed-demo', action='store_true', help='以当前 Demo 内容预填示例（仍为待审）')
    check = sub.add_parser('check')
    check.add_argument('name')
    revise = sub.add_parser('revise', help='复制为新版本，清除审核记录')
    revise.add_argument('name')
    revise.add_argument('--output', required=True)
    build = sub.add_parser('bundle')
    build.add_argument('name')
    build.add_argument('--output', required=True)
    args = parser.parse_args()
    data, nodes, source_hash = curation.load_graph(curation.GRAPH)
    WORK.mkdir(exist_ok=True)
    if args.command == 'new':
        candidate = json.loads(path_in(curation.WORK, args.candidate).read_text(encoding='utf-8'))
        result = create(candidate, data, nodes, source_hash)
        if args.seed_demo:
            fill_from_demo(result, json.loads((HERE / 'data.json').read_text(encoding='utf-8')))
        destination = path_in(WORK, args.name)
    else:
        draft = json.loads(path_in(WORK, args.name).read_text(encoding='utf-8'))
        if args.command == 'revise':
            result = revise_draft(draft)
            destination = path_in(WORK, args.output)
        elif args.command == 'check':
            errors, hashes = inspect(draft, data, nodes, source_hash)
            print(json.dumps({'blocker_count': len(errors), 'blockers': errors, 'content_sha256': hashes,
                              'notice': '仅检查结构和哈希；不验证身份、证据真伪或授权。'}, ensure_ascii=False, indent=2))
            return 3 if errors else 0
        else:
            result = bundle(draft, data, nodes, source_hash)
            destination = path_in(WORK, args.output)
    with destination.open('x', encoding='utf-8') as out:
        json.dump(result, out, ensure_ascii=False, indent=2)
        out.write('\n')
    print(f'已写入 {destination}；仅限内部使用，不会更新 Demo 或公开。')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
        print(f'处理失败：{error}')
        raise SystemExit(2)
