const $ = (id) => document.getElementById(id);
const esc = (x) => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {data:null, nodes:new Map(), current:null, history:[], theme:null, cy:null, relations:[], offset:0, total:0, next:null, request:0};
const PAGE_SIZE = 8;
const searchRequests = new Map();
async function request(path, params={}) {
  const url = new URL(`./api/${path}`, window.location.href);
  Object.entries(params).forEach(([key,value]) => url.searchParams.set(key, value));
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function getRelations(id) {
  return state.relations.filter(e => e.source === id || e.target === id).map(e => ({...e, other: e.source === id ? e.target : e.source, reverse: e.target === id})).filter(e => state.nodes.has(e.other));
}
function sourceLink(key) {
  const source = state.data.sources[key];
  if (!source || !/^https:\/\//.test(source.url)) return '<span class="subject-empty">来源待补充</span>';
  return `<a class="source-link" href="${esc(source.url)}" target="_blank" rel="noopener noreferrer">${esc(source.title)} ↗</a>`;
}
function showPage(which) {
  $('home-page').hidden = which !== 'home'; $('explore-page').hidden = which !== 'explore';
  $('top-context').textContent = which === 'home' ? '发现' : '知识图谱';
  $('home-nav').classList.toggle('active', which === 'home'); $('explore-nav').classList.toggle('active', which === 'explore');
  window.scrollTo({top:0,behavior:'instant'});
  if (which === 'explore' && state.current) requestAnimationFrame(() => drawGraph());
}
function renderThemes() {
  $('theme-cards').innerHTML = state.data.themes.map(t => `<button type="button" class="theme-card" data-theme="${esc(t.id)}"><div class="theme-copy"><span class="theme-number">${esc(t.number)} / ${esc(t.eyebrow)}</span><h3>${esc(t.title)}</h3><p>${esc(t.subtitle)}</p><div class="theme-cta"><span>开始探索　↗</span><span>${esc(t.duration)}</span></div></div><div class="theme-visual" aria-hidden="true"><span>${t.id === 'water' ? 'H₂O' : t.id === 'light' ? 'A = εlc' : 'pV = nRT'}</span></div></button>`).join('');
  $('theme-count').textContent = String(state.data.themes.length).padStart(2,'0');
}
function startTheme(id) {
  const theme = state.data.themes.find(t => t.id === id); if (!theme) return;
  state.theme = theme; state.history = []; state.current = null; $('theme-description').textContent = theme.subtitle; showPage('explore'); navigate(theme.start);
}
async function navigate(id, push = true, offset = 0) {
  const requestId = ++state.request;
  $('api-status').textContent = '正在读取相关知识…';
  try {
    const isOriginal=id.startsWith('raw:');
    const result = await request(isOriginal ? 'raw/node' : 'node', {id:isOriginal ? id.slice(4) : id, offset, limit: PAGE_SIZE});
    if (requestId !== state.request) return;
    state.nodes.set(result.node.id, result.node);
    result.items.forEach(item => state.nodes.set(item.other.id, item.other));
    state.relations = result.items.map(item => item.edge);
    state.offset = result.offset; state.total = result.total; state.next = result.next_offset;
    if (push && state.current !== id) state.history.push(id);
    state.current = id; $('api-status').textContent = '';
    renderDetails(); renderPath(); renderList(); drawGraph();
  } catch (error) {
    if (requestId === state.request) $('api-status').textContent = `无法读取节点（${error.message}），请检查本地服务并重试。`;
  }
}
function renderPath() {
  const story = $('story-steps');
  story.hidden = !state.theme;
  story.innerHTML = state.theme ? `<span class="story-label">主题路线</span>${state.theme.steps.map((id,i)=>`<button type="button" class="story-step ${id===state.current?'current':''}" data-node="${esc(id)}"><span>${String(i+1).padStart(2,'0')}</span>${esc(state.theme.step_titles[id] || id)}</button>`).join('')}` : '';
  $('breadcrumbs').innerHTML = state.history.map((id,i) => `${i ? '<span>›</span>' : ''}<button type="button" data-history="${i}">${esc(state.nodes.get(id)?.title || id)}</button>`).join('');
  $('back-node').disabled = state.history.length < 2;
}
function renderDetails() {
  const node = state.nodes.get(state.current); if (!node) return;
  const relations = getRelations(node.id);
  const groups = [...state.data.disciplines,...(node.scope ? ['其他'] : [])].map(subject => {
    const edges = relations.filter(e => e.discipline === subject);
    return `<div class="subject-group"><div class="subject-name">${esc(subject)}</div>${edges.length ? edges.map(e => {
      const target = state.nodes.get(e.other);
      return `<div class="relation-item"><button type="button" class="relation-card" data-node="${esc(target.id)}"><strong>${esc(target.title)} <span class="rel-note"> · ${esc(e.relation)}</span>　↗</strong>${target.formula ? `<span class="related-formula">${esc(target.formula)}</span>` : ''}<small>${esc(e.explanation)}</small><small class="rel-note">条件：${esc(e.condition || '待补充')} · 原始方向：${esc(state.nodes.get(e.source).title)} → ${esc(state.nodes.get(e.target).title)}${e.reverse ? '（当前为反向浏览）' : ''}</small></button><div class="relation-source">${sourceLink(e.source_ref)}</div></div>`;
    }).join('') : `<div class="subject-empty">${state.total > PAGE_SIZE ? '此批暂无关联' : '暂无关联内容'}</div>`}</div>`;
  }).join('');
  const variables = (node.variables || []).map(v => `<li>${esc(v)}</li>`).join('');
  const papers = Array.isArray(node.papers) ? node.papers.filter(p => /^https:\/\//.test(p.url || '')) : [];
  const paperCards = papers.length ? papers.map(p => `<article class="paper-card"><span class="paper-label">相关论文 · ${esc(p.year)}</span><a href="${esc(p.url)}" target="_blank" rel="noopener noreferrer">${esc(p.title)} ↗</a><p>${esc(p.authors)} · ${esc(p.journal)}</p><p>${esc(p.relation)}</p><small>DOI：${esc(p.doi)}</small></article>`).join('') : '<p class="subject-empty">本节点暂无收录的相关论文。可先查看下方参考资料。</p>';
  const references = node.scope ? `<p>原图库记录来源：${esc(node.source_info)}<br>节点 ID：${esc(node.original_id)}</p>` : sourceLink(node.source);
  $('node-details').innerHTML = `<div class="detail-overline">NODE DETAIL <span>${esc(node.domain)} · ${esc(node.type)}</span></div><h2>${esc(node.title)}</h2><div class="detail-en">${esc(node.english)}</div><p class="detail-desc">${esc(node.description)}</p>${node.formula ? `<div class="formula-box">${esc(node.formula)}</div>` : ''}<div class="review-notice">${esc(node.notice || '开发预览：本节点及关联内容尚未经独立科学与许可复核。')}</div><div class="detail-section"><h3>${node.scope ? '论文线索（待核对）' : '相关论文'}</h3>${paperCards}</div>${variables ? `<div class="detail-section"><h3>符号与单位</h3><ul>${variables}</ul></div>` : ''}<div class="detail-section"><h3>适用条件</h3><p>${esc(node.conditions || '暂无适用条件说明。')}</p></div>${node.example ? `<div class="detail-section"><h3>一个例子</h3><p>${esc(node.example)}</p>${(node.example_sources||[]).map(sourceLink).join(' ')}</div>` : ''}<div class="detail-section"><h3>参考资料</h3>${references}</div><div class="detail-section"><div class="subjects-title"><h3>学科之间的联系</h3><span>按学科查看</span></div>${groups}</div>`;
}
function renderList() {
  const relations = getRelations(state.current);
  $('node-list').innerHTML = relations.length ? relations.map(e => {
    const n = state.nodes.get(e.other);
    return `<button type="button" class="node-row" data-node="${esc(n.id)}"><span class="node-badge">${esc(n.type === '公式' ? 'ƒ' : '·')}</span><span><strong>${esc(n.title)}</strong>${n.formula ? `<span class="row-formula">${esc(n.formula)}</span>` : ''}<small>${esc(e.discipline)} · ${esc(e.relation)}</small></span><span class="arrow">↗</span></button>`;
  }).join('') : '<p class="subject-empty">本版本暂无更多相关内容。</p>';
  $('graph-count').textContent = `本批 ${relations.length + 1} 个节点 · ${relations.length} 条联系，共 ${state.total} 条`;
  $('relation-pagination').hidden = state.total <= PAGE_SIZE;
  $('relations-prev').disabled = state.offset === 0;
  $('relations-next').disabled = state.next === null;
  $('relations-page').textContent = `${state.offset + 1}–${Math.min(state.offset + PAGE_SIZE, state.total)} / ${state.total}`;
}
function drawGraph() {
  const el = $('graph'); if (!el || !state.current || $('explore-page').hidden) return;
  if (!window.cytoscape) { el.textContent = '图谱组件不可用，请使用下方相关节点列表继续探索。'; return; }
  const edges = getRelations(state.current), ids = [state.current, ...new Set(edges.map(e=>e.other))];
  const elements = ids.map(id => {const n=state.nodes.get(id); const f=n.formula && n.formula.length>42 ? n.formula.slice(0,42)+'…' : n.formula; return {data:{id,label:f ? `${n.title}\n${f}` : n.title},classes:id===state.current?'current':''};});
  edges.forEach(e => elements.push({data:{id:e.id,source:e.source,target:e.target,label:e.relation}}));
  if (state.cy) state.cy.destroy();
  state.cy = window.cytoscape({container:el,elements,style:[
    {selector:'node',style:{'background-color':'#92b7a6','label':'data(label)','color':'#50677e','font-family':'Microsoft YaHei, sans-serif','font-size':13,'text-valign':'bottom','text-margin-y':9,'text-events':'yes','width':38,'height':38,'border-width':7,'border-color':'#e1eee9','text-wrap':'wrap','text-max-width':'110px'}},
    {selector:'node.current',style:{'background-color':'#355fc0','border-color':'#e0e9fa','border-width':9,'color':'#1d355a','font-weight':'bold','width':48,'height':48}},
    {selector:'edge',style:{'width':1.3,'line-color':'#cbd8e6','target-arrow-color':'#b5c6da','target-arrow-shape':'triangle','arrow-scale':.65,'curve-style':'bezier'}}
  ],layout:{name:'circle',padding:55,avoidOverlap:true},minZoom:.3,maxZoom:2.5,boxSelectionEnabled:false});
  state.cy.on('tap','node',evt => navigate(evt.target.id()));
  requestAnimationFrame(() => { if(state.cy){state.cy.resize();state.cy.fit(undefined,55);} });
}
async function search(value, target, offset=0, savedScope=null, savedCategory=null) {
  const query = value.trim(), box = $(target); box.hidden=false;
  const filterPrefix = target === 'search-results' ? 'home' : 'explore';
  const scope = savedScope || $(`${filterPrefix}-scope`).value;
  const category = savedCategory || $(`${filterPrefix}-category`).value;
  const requestId=(searchRequests.get(target)||0)+1;searchRequests.set(target,requestId);
  if(!query){box.innerHTML='<p>请输入关键词，例如「水」或「温度」。</p>'; return;}
  box.innerHTML='<p>正在搜索…</p>';
  try {
    const result = await request(scope === 'raw' ? 'raw/search' : 'search', {q:query, offset, limit:PAGE_SIZE, ...(scope === 'raw' ? {category} : {})});
    if(searchRequests.get(target)!==requestId)return;
    box.innerHTML = (scope === 'raw' ? `<p class="search-note">本地原图库 · 候选仅供探索${result.clarifications?.length ? `；${esc(result.clarifications.join(' '))}` : ''}</p>` : '') + (result.items.length ? result.items.map(n => `<button type="button" data-node="${esc(n.id)}"><strong>${esc(n.title)}</strong> <span class="subject-empty">${esc(n.english)} · ${esc(n.type)}</span>${n.formula ? `<span class="row-formula">${esc(n.formula)}</span>` : ''}${n.match ? `<small class="search-match">${esc(n.match)}</small>` : ''}　↗</button>`).join('') : '<p>没有找到匹配内容，请尝试元素符号、公式名称或更短关键词。</p>');
    if (result.total > PAGE_SIZE) box.innerHTML += `<div class="search-pagination">${offset ? `<button type="button" data-search-page="${Math.max(0,offset-PAGE_SIZE)}" data-query="${esc(query)}" data-target="${esc(target)}" data-scope="${esc(scope)}" data-category="${esc(category)}">上一批</button>` : ''}<span>${offset+1}–${Math.min(offset+PAGE_SIZE,result.total)} / ${result.total}</span>${result.next_offset !== null ? `<button type="button" data-search-page="${result.next_offset}" data-query="${esc(query)}" data-target="${esc(target)}" data-scope="${esc(scope)}" data-category="${esc(category)}">下一批</button>` : ''}</div>`;
  } catch(error) {if(searchRequests.get(target)===requestId)box.innerHTML=`<p>搜索暂不可用（${esc(error.message)}），请检查本地服务。</p>`;}
}
function bind() {
  for(const [form,prefix] of [['search-form','home'],['explore-search-form','explore']]) {
    const available=state.data.database_available;
    $(form).insertAdjacentHTML('afterend', `<div class="search-filters"><label for="${prefix}-scope">搜索范围</label><select id="${prefix}-scope"><option value="raw" ${available?'selected':'disabled'}>原图库${available?'':'（未建立）'}</option><option value="demo" ${available?'':'selected'}>精选内容</option></select><label for="${prefix}-category">类型</label><select id="${prefix}-category"><option value="all">全部</option><option value="element">元素</option><option value="molecule">分子</option><option value="formula">公式</option></select></div>`);
    const scope=$(`${prefix}-scope`), category=$(`${prefix}-category`);
    const update=()=>{category.disabled=scope.value==='demo';};scope.addEventListener('change',update);update();
  }
  document.addEventListener('click', e => {
    const page=e.target.closest('[data-search-page]');if(page){search(page.dataset.query,page.dataset.target,Number(page.dataset.searchPage),page.dataset.scope,page.dataset.category);return;}
    const theme = e.target.closest('[data-theme]'); if (theme) {startTheme(theme.dataset.theme);return;}
    const n = e.target.closest('[data-node]'); if (n) {if($('explore-page').hidden){state.history=[];state.current=null;state.theme=null;$('theme-description').textContent='从一个节点出发，沿着联系继续探索。';showPage('explore');} if(n.dataset.node.startsWith('raw:')){state.theme=null;$('theme-description').textContent='浏览原图库，沿关联继续探索。';} $('search-results').hidden=true;$('explore-search-results').hidden=true;navigate(n.dataset.node);return;}
    const h=e.target.closest('[data-history]'); if(h){const i=Number(h.dataset.history);state.history=state.history.slice(0,i+1);navigate(state.history[i],false);return;}
    const quick=e.target.closest('[data-search]');if(quick){$('search-input').value=quick.dataset.search;search(quick.dataset.search,'search-results');}
  });
  $('home-nav').onclick = $('back-home').onclick = $('mode-explore').onclick = () => showPage('home');
  document.querySelector('a[href="#about"]').addEventListener('click', () => showPage('home'));
  $('explore-nav').onclick = () => {if(!state.current) startTheme('water');else showPage('explore');};
  $('back-node').onclick = () => {if(state.history.length>1){state.history.pop();navigate(state.history.at(-1),false);}};
  $('relations-prev').onclick = () => navigate(state.current, false, Math.max(0,state.offset-PAGE_SIZE));
  $('relations-next').onclick = () => {if(state.next !== null) navigate(state.current, false, state.next);};
  $('fit-graph').onclick = () => state.cy?.fit(undefined,55);
  window.addEventListener('resize', () => {if(state.cy && !$('explore-page').hidden){state.cy.resize();state.cy.fit(undefined,55);}});
  $('search-form').onsubmit = e => {e.preventDefault();search($('search-input').value,'search-results');};
  $('explore-search-form').onsubmit = e => {e.preventDefault();search($('explore-search').value,'explore-search-results');};
  $('open-feedback').onclick = () => $('feedback-dialog').showModal();
  const feedbackText=()=>`日常说法：${$('feedback-term').value.trim()}\n补充说明：${$('feedback-text').value.trim()}\n`;
  $('copy-feedback').onclick = async () => {try{await navigator.clipboard.writeText(feedbackText());$('feedback-status').textContent='已复制到剪贴板。';}catch{$('feedback-status').textContent='无法复制，请手动选择文字或下载反馈。';}};
  $('download-feedback').onclick = () => {const u=URL.createObjectURL(new Blob([feedbackText()],{type:'text/plain;charset=utf-8'}));const a=document.createElement('a');a.href=u;a.download='sttp-feedback.txt';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);$('feedback-status').textContent='反馈文本已下载。';};
}
async function init() {
  try {
    const data=await request('config');if(!Array.isArray(data.themes)||!Array.isArray(data.disciplines))throw new Error('数据格式不正确');
    state.data=data;$('version-label').textContent=data.version;renderThemes();bind();
    const pageUrl = new URL(window.location.href);
    if(pageUrl.searchParams.get('feedback')==='1'){$('feedback-dialog').showModal();pageUrl.searchParams.delete('feedback');history.replaceState(null,'',pageUrl.pathname+pageUrl.search+pageUrl.hash);}
  } catch (error) {const box=$('search-results');box.hidden=false;box.innerHTML=`<p>无法加载 Demo 数据：${esc(error.message)}。请通过本地 HTTP 服务启动，参阅使用说明。</p>`;}
}
init();
