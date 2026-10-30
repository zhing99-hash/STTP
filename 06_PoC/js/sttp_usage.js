/* Optional aggregate usage counts, designed only for the localhost demo. */
(() => {
  const version = document.currentScript.dataset.version;
  const isExplore = version === 'explore';
  const storage = isExplore ? localStorage : sessionStorage;
  const key = 'sttp-local-usage-opt-in';
  const endpoint = (isExplore ? location.origin : 'http://127.0.0.1:8773') + '/demo/api/metrics/event';
  const params = new URLSearchParams(location.search);
  if (!isExplore && params.get('sttp_metrics') === '1') {
    storage.setItem(key,'yes'); params.delete('sttp_metrics');
    history.replaceState(null,'',location.pathname + (params.size ? '?' + params.toString() : '') + location.hash);
  }
  if (!isExplore && params.get('sttp_metrics') === '0') {
    storage.removeItem(key); params.delete('sttp_metrics');
    history.replaceState(null,'',location.pathname + (params.size ? '?' + params.toString() : '') + location.hash);
  }
  let enabled = storage.getItem(key) === 'yes';
  let started = enabled && !document.hidden ? performance.now() : null;
  function send(event, seconds=0) {
    if(!enabled)return Promise.resolve();
    const body=JSON.stringify({version,event,seconds});
    return fetch(endpoint,{method:'POST',headers:{'Content-Type':'text/plain;charset=UTF-8'},body,keepalive:true}).catch(()=>{});
  }
  function flush() {
    if(started===null || !enabled) return;
    const now=performance.now(), seconds=Math.min(30,Math.floor((now-started)/1000));
    let sent=Promise.resolve();
    if(seconds>0){sent=send('visible_seconds',seconds);started += seconds*1000;}
    if(now-started>30000)started=now;
    return sent;
  }
  function update() {
    const toggle=document.getElementById('metrics-toggle');
    if(toggle){toggle.textContent=enabled?'试用统计：开':'试用统计：关';toggle.setAttribute('aria-pressed',String(enabled));}
  }
  function setup() {
    update();
    const toggle=document.getElementById('metrics-toggle');if(toggle)toggle.disabled=false;
    const statsOpen=document.getElementById('metrics-open');if(statsOpen)statsOpen.disabled=false;
    if(enabled)send('enter');
    document.getElementById('metrics-toggle')?.addEventListener('click',()=>{
      if(enabled){flush();enabled=false;started=null;storage.removeItem(key);}
      else{enabled=true;storage.setItem(key,'yes');started=document.hidden?null:performance.now();send('enter');}
      update();
    });
    document.querySelectorAll('[data-engineering-link]').forEach(link=>link.addEventListener('click',event=>{
      flush(); started=null; send('switch_to_engineering');
      const url=new URL(event.currentTarget.href);
      url.searchParams.set('sttp_metrics',enabled?'1':'0');
      event.currentTarget.href=url.href;
    }));
    document.getElementById('return-explore')?.addEventListener('click',()=>{flush();started=null;send('switch_to_explore');});
    document.getElementById('engineering-feedback')?.addEventListener('click',()=>{flush();started=null;send('feedback_open');send('switch_to_explore');});
    document.addEventListener('click',event=>{
      if(event.target.closest('button,a,canvas'))send('interface_click');
      if(event.target.closest('[data-explore-choice]'))send('select_explore');
      if(isExplore && event.target.closest('[data-node]'))send('node_open');
      if(event.target.closest('#open-feedback'))send('feedback_open');
      if(!isExplore && event.target.closest('#task-search'))send('search');
      if(!isExplore && event.target.closest('#task-run'))send('graph_generate');
      if(!isExplore && event.target.closest('#btn-export-json,#btn-export-svg,#btn-export-png'))send('export');
    });
    document.addEventListener('submit',()=>send('search'));
    document.addEventListener('visibilitychange',()=>{
      if(document.hidden){flush();started=null;}else if(enabled)started=performance.now();
    });
    window.addEventListener('pagehide',()=>{flush();started=null;});
    setInterval(flush,15000);
    if(isExplore){
      const dialog=document.getElementById('metrics-dialog');
      document.getElementById('metrics-open')?.addEventListener('click',async()=>{
        await flush();
        dialog.showModal();
        const body=document.getElementById('metrics-results');body.textContent='正在读取本机汇总…';
        try{
          const response=await fetch('./api/metrics');if(!response.ok)throw new Error('暂时不可用');
          const data=await response.json();
          const totals={explore:{},engineering:{}};
          for(const row of data.items){const v=totals[row.version];if(v)v[row.event]=(v[row.event]||0)+(row.event==='visible_seconds'?row.seconds:row.count);}
          const values=v=>`进入 ${v.enter||0} 次 · 点击 ${v.interface_click||0} 次 · 搜索 ${v.search||0} 次 · 页面可见 ${Math.round((v.visible_seconds||0)/60*10)/10} 分钟`;
          body.textContent=`探索版：${values(totals.explore)}\n　点击探索版入口 ${totals.explore.select_explore||0} 次 · 点击文字节点 ${totals.explore.node_open||0} 次 · 打开反馈 ${totals.explore.feedback_open||0} 次\n工程版：${values(totals.engineering)}\n　点击生成 ${totals.engineering.graph_generate||0} 次 · 点击导出 ${totals.engineering.export||0} 次 · 反馈 ${totals.engineering.feedback_open||0} 次\n版本切换：探索 → 工程 ${totals.explore.switch_to_engineering||0} 次 · 工程 → 探索 ${totals.engineering.switch_to_explore||0} 次`;
        }catch{body.textContent='无法读取本机统计，请检查服务。';}
      });
    }
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',setup,{once:true});else setup();
})();
