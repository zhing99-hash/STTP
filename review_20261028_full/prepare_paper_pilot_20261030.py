"""Prepare a non-publishing, reproducible paper-pilot review pack."""
import collections
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'review_20261028_full' / 'paper_pilot_20261030'
GRAPH = ROOT / '06_PoC' / 'etl' / 'normalized.json'
POOL = ROOT / '11_真实数据' / 'phase68_paper_mentions.json'
IDS = [
 'TH:fo:ideal_gas','TH:fo:charles','TH:fo:boyle','TH:fo:gay_lussac','TH:fo:first_law','TH:fo:carnot',
 'CM:fo:momentum','CM:fo:gravity','CM:fo:centripetal','CM:fo:work','CM:fo:power','FO:fma','FO:ke',
 'EM:fo:coulomb','EM:fo:faraday','EM:fo:gauss','EM:fo:lorentz','OP:fo:lens','OP:fo:snell',
 'CE:fo:arrhenius','CE:fo:nernst','CE:fo:henderson','CE:fo:mass_action','CE:fo:kw','CE:fo:ka',
 'CE:fo:ksp','CE:fo:le_chatelier','CE:fo:gibbs_standard','OM:fo:combustion',
 'MA:fo:pythagoras','MA:fo:quadratic','MA:fo:log_product','MA:fo:euler_identity','MA:fo:binomial',
 'MC:fo:chain_rule','MC:fo:ftc','MC:fo:taylor'
]


def clean_doi(value):
    text = unquote(str(value or '')).strip().lower()
    text = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', text)
    return text if re.match(r'^10\.\d{4,9}/\S+$',text) else None


def main():
    graph_raw, pool_raw = GRAPH.read_bytes(), POOL.read_bytes()
    graph, pool = json.loads(graph_raw), json.loads(pool_raw)
    nodes = {n['id']: n for n in graph['nodes']}
    candidates = {f['fid']: f for f in pool['formulas']}
    if len(IDS)!=len(set(IDS)) or any(i not in nodes or i not in candidates for i in IDS):
        raise ValueError('种子不存在或重复，停止生成')
    graph_dois = {clean_doi(n.get('props',{}).get('doi')) for n in graph['nodes'] if 'Paper' in n.get('labels',[])}
    seeds=[]
    for fid in IDS:
        node, batch = nodes[fid], candidates[fid]
        p = node.get('props',{})
        selected=[]
        for paper in (batch.get('candidates') or [])[:3]:
            value=clean_doi(paper.get('doi'))
            selected.append({'title':paper.get('title'), 'doi':value, 'openalex_id':paper.get('wid'),
                             'year':paper.get('year'), 'source_location':paper.get('where'),
                             'in_original_paper_nodes':bool(value and value in graph_dois),
                             'status':'候选线索，关系与许可待核对'})
        seeds.append({'id':fid, 'name':p.get('name'), 'domain':p.get('domain'), 'formula':p.get('latex') or p.get('formula'),
                      'candidate_count_in_existing_pool':len(batch.get('candidates') or []),
                      'selected_existing_candidates':selected,
                      'graph_paper_status':p.get('papers_status'), 'graph_paper_count':len(p.get('papers') or []),
                      'review':'待审，未确认适用条件、来源和关联'})
    # Metadata seen on publisher pages on 2026-10-30; only citations and short original notes are saved.
    added=[
      {'doi':'10.1021/ed075p1186','title':'Why the Arrhenius Equation Is Always in the Exponentially Increasing Region in Chemical Kinetic Studies',
       'authors':['Harvey F. Carroll'],'year':1998,'journal':'Journal of Chemical Education 75(9), 1186',
       'url':'https://pubs.acs.org/doi/10.1021/ed075p1186','connection':'讨论阿伦尼乌斯方程的温度变化与教学背景。'},
      {'doi':'10.1021/ed084p1776','title':'Visualizing the Transition State: A Hands-on Approach to the Arrhenius Equation',
       'authors':['Matthew S. Swanson','Deborah K. Sayers','Thomas S. Kuntzleman'],'year':2007,'journal':'Journal of Chemical Education 84(11), 1776',
       'url':'https://pubs.acs.org/doi/10.1021/ed084p1776','connection':'以课堂模拟介绍反应动力学与阿伦尼乌斯方程。'},
      {'doi':'10.1021/acs.jchemed.0c00081','title':'Establishing a Connection for Students between the Reacting System and the Particle Model with Games and Stochastic Simulations of the Arrhenius Equation',
       'authors':['Thomas Kraska'],'year':2020,'journal':'Journal of Chemical Education 97(7), 1951–1959',
       'url':'https://pubs.acs.org/doi/10.1021/acs.jchemed.0c00081','connection':'利用游戏和随机模拟讲解反应体系与粒子模型。'}
    ]
    for paper in added:
        paper.update({'target_id':'CE:fo:arrhenius', 'record_type':'教学或研究文章线索',
                      'publisher_page_checked':'2026-10-30；依据搜索结果中出版方页面题录和摘要片段；直接打开页面返回 403，未阅读全文',
                      'in_original_paper_nodes':clean_doi(paper['doi']) in graph_dois,
                      'review':'待独立核对具体关联、全文许可与使用范围'})
    data={'schema':'sttp-paper-pilot-review-v1','generated_for':'2026-10-30',
          'graph_sha256':hashlib.sha256(graph_raw).hexdigest(), 'candidate_pool_sha256':hashlib.sha256(pool_raw).hexdigest(),
          'notice':'本包是内部候选，不自动写入项目数据库，论文不等于公式原始发现或证明。',
          'seeds':seeds,'new_publisher_page_candidates':added}
    selected=[p for seed in seeds for p in seed['selected_existing_candidates']]
    unique={p['doi'] for p in selected if p['doi']}
    lines=['# 论文试点：首批种子及候选', '', '日期：2026-10-30。基于原图和 2026-10-22 的既有 OpenAlex 候选池；尚未写入公开图谱。', '',
           f'- 种子：{len(seeds)} 个；领域以原图域字段为准。',
           f'- 每个种子最多列出原候选池前 3 条：共 {len(selected)} 条线索，其中 {len(unique)} 个不同的可识别 DOI。',
           f'- 原候选池为空的种子：{sum(x["candidate_count_in_existing_pool"]==0 for x in seeds)} 个；这不表示没有相关论文。',
           '- 本次另按出版方页面补入 3 条阿伦尼乌斯教学论文候选，仅保存在此内部材料中。',
           '', '| 节点 ID | 名称 | 原图领域 | 原候选池数量 | 原图已有论文线索数 |', '|---|---|---|---:|---:|']
    for s in seeds:
        lines.append(f'| `{s["id"]}` | {str(s["name"]).replace("|", "&#124;")} | {s["domain"]} | {s["candidate_count_in_existing_pool"]} | {s["graph_paper_count"]} |')
    lines.extend(['','## 本次新增的出版方页面线索','','均不是原始发现论文；依据搜索结果中出版方页面题录和摘要片段，直接打开页面返回 403，全文及许可未审查。'])
    for paper in added:
        lines.append(f'- [{paper["title"]}]({paper["url"]})，{", ".join(paper["authors"])}，{paper["year"]}。{paper["connection"]} DOI 在当前原图 Paper 节点中未发现。')
    lines.extend(['','## 后续核对','','逐项核对论文版本、来源、实际关联和权限；学科专家检查公式条件及反例，独立试用者验证检索与理解。现有候选来自旧的标题/摘要检索，排序和相关性并非本次重新验证。',
                  '机器可读明细见 [paper_pilot.json](paper_pilot.json)。原始文件、图谱和用户展示数据均未被此脚本改动。'])
    OUT.mkdir(exist_ok=True)
    paths=[OUT/'paper_pilot.json', OUT/'首批论文种子与候选.md']
    if any(p.exists() for p in paths): raise FileExistsError('输出已存在，不覆盖既有内容')
    with paths[0].open('x',encoding='utf-8') as f: json.dump(data,f,ensure_ascii=False,indent=2);f.write('\n')
    with paths[1].open('x',encoding='utf-8') as f: f.write('\n'.join(lines)+'\n')
    print(json.dumps({'seeds':len(seeds),'selected_existing_refs':len(selected),'selected_existing_distinct_doi':len(unique),
                      'seeds_without_existing_candidates':sum(x['candidate_count_in_existing_pool']==0 for x in seeds),
                      'new_publisher_candidates':len(added),'new_already_in_graph':sum(p['in_original_paper_nodes'] for p in added),
                      'output':str(OUT)},ensure_ascii=False,indent=2))


if __name__ == '__main__': main()
