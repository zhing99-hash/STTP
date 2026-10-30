"""Local, non-publishing candidate staging for the original STTP graph."""
import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
GRAPH = HERE.parent / 'etl' / 'normalized.json'
WORK = HERE / 'curation_work'
FIELDS = ('name', 'description', 'informal', 'latex', 'formula', 'domain',
          'source', 'source_ref', 'meta_source', 'applicability')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def load_graph(path):
    raw = path.read_bytes()
    data = json.loads(raw)
    if not isinstance(data.get('nodes'), list) or not isinstance(data.get('edges'), list):
        raise ValueError('图数据缺少 nodes 或 edges 列表')
    nodes = {n['id']: n for n in data['nodes']}
    if len(nodes) != len(data['nodes']):
        raise ValueError('原图存在重复节点 ID，停止处理')
    if len({e['id'] for e in data['edges']}) != len(data['edges']):
        raise ValueError('原图存在重复关系 ID，停止处理')
    return data, nodes, hashlib.sha256(raw).hexdigest()


def snapshot(record, kind):
    props = record.get('props') or {}
    if kind == 'node':
        result = {'id': record['id'], 'labels': record.get('labels', []),
                  'fields': {key: props[key] for key in FIELDS if key in props}}
    else:
        result = {'id': record['id'], 'source': record['source'], 'target': record['target'],
                  'type': record.get('type'), 'fields': {key: props[key] for key in
                  ('source', 'evidence', 'claim', 'verification_level', 'verified') if key in props}}
    return result


def new_item(record, kind):
    extracted = snapshot(record, kind)
    return {'id': record['id'], 'original_record_sha256': digest(record),
            'snapshot': extracted, 'snapshot_sha256': digest(extracted),
            'review': {'scientific': None, 'license': None}}


def stage(data, nodes, source_hash, ids):
    if not ids or len(ids) > 30 or len(ids) != len(set(ids)):
        raise ValueError('请选择 1 至 30 个不重复的节点 ID')
    missing = [node_id for node_id in ids if node_id not in nodes]
    if missing:
        raise ValueError('未找到节点：' + ', '.join(missing))
    selected = set(ids)
    edges = [e for e in data['edges'] if e['source'] in selected and e['target'] in selected]
    return {'schema': 'sttp-curation-candidate-v1',
            'created_utc': datetime.now(timezone.utc).isoformat(),
            'source_file': '06_PoC/etl/normalized.json', 'source_sha256': source_hash,
            'notice': '内部候选，默认未经科学审稿和许可核对；不得自动发布。',
            'nodes': [new_item(nodes[node_id], 'node') for node_id in ids],
            'edges': [new_item(e, 'edge') for e in edges]}


def inspect(candidate, data, nodes, source_hash):
    blockers = []
    if candidate.get('schema') != 'sttp-curation-candidate-v1':
        return ['候选格式不受支持']
    if candidate.get('source_sha256') != source_hash:
        blockers.append('原图文件哈希变化，需重新生成候选并复核')
    original_edges = {e['id']: e for e in data['edges']}
    raw_nodes = candidate.get('nodes')
    selected_ids = [item.get('id') for item in raw_nodes if isinstance(item, dict)] if isinstance(raw_nodes, list) else []
    if len(set(selected_ids)) != len(selected_ids):
        blockers.append('候选节点 ID 重复')
    selected = set(selected_ids)
    for kind, original in (('nodes', nodes), ('edges', original_edges)):
        entries = candidate.get(kind)
        if not isinstance(entries, list):
            blockers.append(kind + ' 必须是列表')
            continue
        entry_ids = [item.get('id') for item in entries if isinstance(item, dict)]
        if len(entry_ids) != len(set(entry_ids)):
            blockers.append(kind + ' ID 重复')
        for item in entries:
            if not isinstance(item, dict):
                blockers.append(kind + ' 含有非对象记录')
                continue
            item_id = item.get('id', '')
            source = original.get(item_id)
            if kind == 'edges' and source and (source['source'] not in selected or source['target'] not in selected):
                blockers.append(f'edges:{item_id} 端点不在候选中')
            if not source or digest(source) != item.get('original_record_sha256'):
                blockers.append(f'{kind}:{item_id} 原始记录缺失或已变化')
            elif snapshot(source, 'node' if kind == 'nodes' else 'edge') != item.get('snapshot'):
                blockers.append(f'{kind}:{item_id} 原始摘录已更改')
            if digest(item.get('snapshot')) != item.get('snapshot_sha256'):
                blockers.append(f'{kind}:{item_id} 摘录哈希不匹配')
            review = item.get('review') or {}
            for field in ('scientific', 'license'):
                record = review.get(field)
                if not isinstance(record, dict) or record.get('decision') != 'approved':
                    blockers.append(f'{kind}:{item_id} 缺少 {field} 审查通过记录')
                    continue
                for required in ('reviewer', 'date', 'evidence', 'scope'):
                    if not isinstance(record.get(required), str) or not record[required].strip():
                        blockers.append(f'{kind}:{item_id} {field} 缺少 {required}')
                if record.get('snapshot_sha256') != item.get('snapshot_sha256'):
                    blockers.append(f'{kind}:{item_id} {field} 不是针对当前摘录的审核')
    if not candidate.get('nodes'):
        blockers.append('候选节点为空')
    return blockers


def main():
    parser = argparse.ArgumentParser(description='STTP 原图库内部候选整理工具（不发布）')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('inventory', help='统计原图库')
    find = sub.add_parser('find', help='按 ID、名称或公式搜索原图节点')
    find.add_argument('query')
    find.add_argument('--limit', type=int, default=10, choices=range(1, 31))
    create = sub.add_parser('stage', help='创建隔离候选，不覆盖现有文件')
    create.add_argument('--id', action='append', required=True, dest='ids')
    create.add_argument('--name', required=True, help='候选文件名，例如 pilot.json')
    check = sub.add_parser('check', help='列出候选的审查缺项，不发布')
    check.add_argument('name', help='curation_work 下的候选文件名')
    args = parser.parse_args()
    data, nodes, source_hash = load_graph(GRAPH)
    if args.command == 'inventory':
        labels = Counter(label for node in data['nodes'] for label in node.get('labels', []))
        print(json.dumps({'nodes': len(nodes), 'edges': len(data['edges']),
                          'labels': labels, 'source_sha256': source_hash}, ensure_ascii=False, indent=2))
    elif args.command == 'find':
        term = args.query.strip().casefold()
        if not term:
            raise ValueError('搜索词不能为空')
        results = []
        for node in data['nodes']:
            props = node.get('props') or {}
            if any(term in str(value or '').casefold() for value in
                   (node['id'], props.get('name'), props.get('description'), props.get('latex'))):
                results.append({'id': node['id'], 'labels': node.get('labels', []),
                                'name': props.get('name'), 'source': props.get('source')})
        print(json.dumps({'total': len(results), 'items': results[:args.limit]}, ensure_ascii=False, indent=2))
    else:
        name = args.name
        if Path(name).name != name or not name.lower().endswith('.json'):
            raise ValueError('只能使用工作目录内的 JSON 文件名')
        WORK.mkdir(exist_ok=True)
        dest = WORK / name
        if dest.is_symlink():
            raise ValueError('不允许使用符号链接')
        if args.command == 'stage':
            candidate = stage(data, nodes, source_hash, args.ids)
            with dest.open('x', encoding='utf-8') as out:
                json.dump(candidate, out, ensure_ascii=False, indent=2)
                out.write('\n')
            print(f'已创建 {dest}；{len(candidate["nodes"])} 个节点、{len(candidate["edges"])} 条内部关系；全部待审。')
        else:
            candidate = json.loads(dest.read_text(encoding='utf-8'))
            blockers = inspect(candidate, data, nodes, source_hash)
            print(json.dumps({'blocker_count': len(blockers), 'blockers': blockers,
                              'notice': '程序只检查记录完整性，不验证身份或授权真伪，也不会发布。'}, ensure_ascii=False, indent=2))
            return 3 if blockers else 0
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
        print(f'处理失败：{error}')
        raise SystemExit(2)
