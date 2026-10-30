"""Idempotently expand the local demo; keeps a 0.2 backup, never alters the original graph."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
path = HERE / 'data.json'
backup = HERE / 'data-v0.2.backup.json'
data = json.loads(path.read_text(encoding='utf-8'))
if data['version'] == 'sttp-explore-demo-0.2' and not backup.exists():
    with backup.open('x', encoding='utf-8') as output:
        json.dump(data, output, ensure_ascii=False, indent=2)
        output.write('\n')
elif data['version'] not in ('sttp-explore-demo-0.2', 'sttp-explore-demo-0.3'):
    raise SystemExit('版本不匹配，拒绝修改。')

data['sources'].update({
    'solutions': {'title': 'OpenStax · 溶液浓度与稀释', 'url': 'https://openstax.org/books/chemistry-2e/pages/3-3-molarity'},
    'mixgas': {'title': 'OpenStax · 气体混合与分压', 'url': 'https://openstax.org/books/chemistry-2e/pages/9-3-stoichiometry-of-gaseous-substances-mixtures-and-reactions'},
    'spectroscopy': {'title': 'Delgado · 吸光度、透射率与校准', 'url': 'https://pmc.ncbi.nlm.nih.gov/articles/PMC8808104/'},
})
papers = {
    'charles': {'title': "Charles’s law revisited", 'authors': 'Arnold George; Clem Zidick', 'year': 1991,
                'journal': 'Journal of Chemical Education, 68(12), 1042', 'doi': '10.1021/ed068p1042',
                'url': 'https://pubs.acs.org/doi/10.1021/ed068p1042',
                'relation': '介绍不使用水银气压计的查理定律实验建议；是相关教学论文，并非原始定律发现论文。'},
    'beer': {'title': 'Misuse of Beer–Lambert Law and other calibration curves', 'authors': 'Rosario Delgado', 'year': 2022,
             'journal': 'Royal Society Open Science, 9(2), 211103', 'doi': '10.1098/rsos.211103',
             'url': 'https://pmc.ncbi.nlm.nih.gov/articles/PMC8808104/',
             'relation': '讨论吸光度与浓度的关系及实验校准中容易出现的统计误用。'}
}
nodes = [
 {'id':'TH:fo:boyle','legacy_id':'TH:fo:boyle','title':'玻意耳定律','english':"Boyle's law",'aliases':['玻意耳','等温','boyle'],'type':'公式','domain':'物理','formula':'p₁V₁ = p₂V₂','description':'同一份理想气体在温度不变时，压强和体积的乘积保持不变。','variables':['p₁、p₂：两种状态的压强','V₁、V₂：两种状态的体积'],'conditions':'固定物质的量、温度恒定；适用理想气体模型。','source':'gas'},
 {'id':'TH:fo:gay_lussac','legacy_id':'TH:fo:gay_lussac','title':'恒容压强温度关系','english':"Amontons's law",'aliases':['盖吕萨克','阿蒙顿','恒容','amontons'],'type':'公式','domain':'物理','formula':'p₁ / T₁ = p₂ / T₂','description':'固定量的理想气体在体积不变时，压强与热力学温度成正比。','variables':['p：压强','T：热力学温度（K）'],'conditions':'固定物质的量与体积；两状态的温度用 K，适用理想气体模型。','source':'gas'},
 {'id':'demo:partial_pressure','title':'道尔顿分压定律','english':"Dalton's law of partial pressures",'aliases':['分压','道尔顿','partial pressure'],'type':'公式','domain':'化学','formula':'p总 = Σpᵢ','description':'理想气体混合物的总压强等于各组分气体分压之和。','variables':['p总：混合气体总压强','pᵢ：第 i 个组分的分压'],'conditions':'采用理想气体混合模型；各分压定义在同一温度和总体积下。','source':'mixgas'},
 {'id':'demo:gas_density','title':'理想气体密度','english':'Ideal gas density','aliases':['气体密度','density'],'type':'公式','domain':'化学','formula':'ρ = pM / (RT)','description':'理想气体密度与压强、摩尔质量、温度之间的关系，可由状态方程和摩尔质量定义推得。','variables':['ρ：质量密度','p：压强','M：摩尔质量','R：气体常数','T：热力学温度'],'conditions':'气体满足理想气体模型；温度使用 K，单位一致；T 和 R 非零。','source':'mixgas'},
 {'id':'demo:molarity','title':'物质的量浓度','english':'Molar concentration','aliases':['浓度','摩尔浓度','molarity'],'type':'公式','domain':'化学','formula':'c = n / V','description':'物质的量浓度是指定溶质的物质的量除以溶液体积。','variables':['c：溶质物质的量浓度','n：溶质物质的量','V：溶液体积'],'conditions':'V 指溶液体积且大于零；例如 n 使用 mol、V 使用 L 时，c 的单位为 mol/L。','source':'solutions'},
 {'id':'demo:dilution','title':'溶液稀释关系','english':'Dilution equation','aliases':['稀释','稀释方程','dilution'],'type':'公式','domain':'化学','formula':'c₁V₁ = c₂V₂','description':'单纯稀释前后，所讨论溶质的物质的量保持不变。','variables':['c₁、c₂：稀释前后浓度','V₁、V₂：对应溶液体积'],'conditions':'溶质没有损失、反应或额外加入；浓度与体积单位一致。','source':'solutions'},
 {'id':'demo:beer_lambert','title':'比尔—朗伯定律','english':'Beer–Lambert law','aliases':['比尔','朗伯','吸光度','beer lambert','光吸收'],'type':'公式','domain':'化学','formula':'A = εlc','description':'在适用条件下，溶液吸光度与吸收物质浓度和光程长度成正比。','variables':['A：吸光度，无量纲','ε：指定波长的摩尔吸光系数','l：光程长度','c：吸收物质的物质的量浓度'],'conditions':'近似单色光、均匀且适当稀的样品；ε 与波长、物种和测量条件相关；散射等影响需评估。','source':'spectroscopy','papers':[papers['beer']]},
 {'id':'demo:absorbance','title':'吸光度与透射率','english':'Absorbance and transmittance','aliases':['吸光度','透射率','absorbance','log'],'type':'公式','domain':'物理','formula':'A = −log₁₀(T)','description':'吸光度由透射率的负常用对数定义。','variables':['A：吸光度','T：透射率，为出射光强与入射光强之比'],'conditions':'T 为正的无量纲比值；测量结果可能受散射和仪器条件影响。','source':'spectroscopy','papers':[papers['beer']]},
 {'id':'demo:transmittance','title':'透射率','english':'Transmittance','aliases':['透射率','光强','transmittance'],'type':'公式','domain':'物理','formula':'T = I / I₀','description':'透射率是透过样品的光强与入射光强之比。','variables':['I：透过样品的光强','I₀：入射或参考光强'],'conditions':'I₀ 大于零；比较同一波长和一致的测量条件。','source':'spectroscopy','papers':[papers['beer']]},
 {'id':'demo:inverse_proportion','title':'反比例关系','english':'Inverse proportion','aliases':['反比例','inverse proportion'],'type':'公式','domain':'数学','formula':'y = k / x','description':'当 k 固定且 x 非零时，x 与 y 的乘积保持为 k。','variables':['x、y：变量','k：固定的比例系数'],'conditions':'x 不为零；应用到科学现象时还需满足对应模型的条件。','source':'gas'},
]
edges = [
 ('n1','TH:fo:ideal_gas','TH:fo:boyle','数学','有条件变形','固定气体物质的量与温度，状态方程给出压强与体积乘积不变。','理想气体、温度和物质的量恒定。','gas'),
 ('n2','TH:fo:ideal_gas','TH:fo:gay_lussac','数学','有条件变形','恒容且气体物质的量固定时，状态方程给出压强与热力学温度的比例关系。','理想气体、体积与物质的量恒定，温度用 K。','gas'),
 ('n3','TH:fo:ideal_gas','demo:partial_pressure','化学','混合气体','状态方程可用于理想混合物中各组分的分压计算。','理想气体混合模型；各组分使用共同的温度和总体积。','mixgas'),
 ('n4','TH:fo:ideal_gas','demo:gas_density','化学','公式推导','结合 n=m/M 和 ρ=m/V，可推导出气体密度表达式。','理想气体，R、T 非零，单位一致。','mixgas'),
 ('n5','TH:fo:boyle','demo:inverse_proportion','数学','数学形式','恒温时 pV 不变，因而压强与体积在模型中呈反比例。','固定物质的量和温度；体积大于零。','gas'),
 ('n6','TH:fo:gay_lussac','demo:proportion','数学','数学形式','恒容时，气体压强与热力学温度呈正比例。','固定物质的量和体积，温度用 K。','gas'),
 ('n7','demo:gas_density','demo:molar_mass','化学','物质量关联','气体密度关系中的 M 是气体的摩尔质量。','单位一致并使用理想气体模型。','mixgas'),
 ('n8','demo:molarity','demo:amount','化学','定义','物质的量浓度由溶质物质的量和溶液体积共同决定。','体积为溶液总体积且大于零。','solutions'),
 ('n9','demo:molarity','demo:dilution','数学','守恒推导','由 n=cV 和稀释前后溶质物质的量相等得到稀释关系。','没有溶质损失或反应，单位一致。','solutions'),
 ('n10','demo:molarity','demo:beer_lambert','化学','测量应用','比尔—朗伯定律使用吸收物质的浓度来联系吸光度。','适用模型及一致浓度单位。','spectroscopy'),
 ('n11','demo:beer_lambert','demo:absorbance','物理','测量定义','吸光度可以由光的透射率通过常用对数计算。','透射率大于零，测量条件一致。','spectroscopy'),
 ('n12','demo:absorbance','demo:transmittance','数学','对数关系','透射率是光强比值，吸光度是该比值的负常用对数。','入射光强与透射率均大于零。','spectroscopy'),
 ('n13','demo:beer_lambert','demo:proportion','数学','数学形式','光程和吸光系数固定时，吸光度与浓度成正比。','适用比尔—朗伯模型，光程和吸光系数固定。','spectroscopy'),
]
existing = {n['id'] for n in data['nodes']}
data['nodes'].extend(n for n in nodes if n['id'] not in existing)
existing_edges = {e['id'] for e in data['edges']}
data['edges'].extend({'id':i,'source':s,'target':t,'discipline':d,'relation':r,'explanation':x,'condition':c,'source_ref':ref}
                     for i,s,t,d,r,x,c,ref in edges if i not in existing_edges)
for node in data['nodes']:
    if node['id'] == 'TH:fo:charles': node['papers'] = [papers['charles']]
if not any(t['id']=='light' for t in data['themes']):
    data['themes'].append({'id':'light','number':'03','eyebrow':'从颜色走进实验','title':'一束光，如何测出溶液浓度？',
                           'subtitle':'沿着光强、吸光度与溶液浓度探索。','start':'demo:beer_lambert',
                           'steps':['demo:beer_lambert','demo:absorbance','demo:transmittance','demo:molarity','demo:dilution'],
                           'duration':'约 5 分钟'})
data['version'] = 'sttp-explore-demo-0.3'
path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(f"{data['version']}: {len(data['nodes'])} nodes, {len(data['edges'])} edges, {len(data['themes'])} themes")
