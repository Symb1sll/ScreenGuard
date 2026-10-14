/* Manual API connections: keys never enter browser storage or rendered markup. */
export async function initModelInterfaces({api,esc,toast,getSelected,getEvidence,labels}) {
  const $=id=>document.getElementById(id);
  const style=document.createElement('link');style.rel='stylesheet';style.href='/static/model-interfaces.css';document.head.append(style);
  const panel=document.createElement('article');panel.className='panel ai-panel';
  panel.innerHTML=[
    '<div class="sectionhead"><div><h2>本地与云端模型接口</h2><p>保存连接后，在“素材与模型”测试。每路自动辅助审核在监控卡片设置，发送云端需单独允许。</p></div><button id="aiNew" class="secondary">新增连接</button></div>',
    '<div id="aiConnectionList" class="ai-cards"></div><form id="aiConnectionForm">',
    '<div class="ai-grid"><label>连接名称<input name="name" required maxlength="80"></label><label>提供商<select name="provider" id="aiProvider"></select></label>',
    '<label>接口协议<select name="protocol"><option value="responses">OpenAI Responses</option><option value="anthropic">Anthropic Messages</option><option value="chat">Chat Completions</option></select></label>',
    '<label>API 基础地址<input name="base_url" type="url" required placeholder="http://127.0.0.1:8088/v1" autocomplete="off"></label>',
    '<label>模型 ID<input name="model" id="aiModelId" list="aiModelOptions" maxlength="200" placeholder="填写账号实际可用的模型 ID"><datalist id="aiModelOptions"></datalist></label>',
    '<label>环境变量名（可选）<input name="key_env" maxlength="100" placeholder="OPENAI_API_KEY" autocomplete="off"></label>',
    '<label>API 密钥（留空保留已保存密钥）<input name="api_key" type="password" maxlength="4096" autocomplete="new-password" placeholder="仅传给本机后端保存"></label>',
    '<label>每日调用上限<input name="daily_limit" type="number" min="1" max="10000" required></label>',
    '<label>调用超时（秒）<input name="timeout_seconds" type="number" min="10" max="180" required></label>',
    '<label>最大输出 token<input name="max_output_tokens" type="number" min="128" max="8192" required></label></div>',
    '<div class="ai-checks"><label class="check"><input name="enabled" type="checkbox">启用连接</label><label class="check"><input name="vision" type="checkbox">此模型支持图片输入</label>',
    '<label class="check"><input name="json_mode" type="checkbox">服务支持强制 JSON 输出</label><label class="check"><input name="clear_key" type="checkbox">删除已保存密钥</label></div>',
    '<p id="aiKeyState" class="muted"></p><p class="muted">图片能力需与你填写的具体模型相符。本地 Qwen 请使用图片模式启动脚本，并完成图片测试。限额按 UTC 零点重置，失败调用也计入。</p>',
    '<div class="row"><button type="submit">保存连接</button><button id="aiReload" type="button" class="secondary">重载连接</button><button id="aiModels" type="button" class="secondary">读取已保存连接的模型列表</button></div>',
    '<p id="aiConnectionStatus" role="status"></p></form><details><summary>本地模型与密钥存储</summary><p id="aiLocalInfo"></p><p>图片模式：项目根目录 start_local_model_vision.bat；纯文本模式：start_local_model.bat。切换模式前请先运行 stop_local_model.bat。模型约 19 GiB，首次加载可能较慢；接口配置不会自动加载模型。Windows 密钥使用当前用户的 DPAPI 加密，迁移 Linux 后请重新配置环境变量。</p></details>'
  ].join('');
  $('page-settings').prepend(panel);
  const test=document.createElement('article');test.className='panel ai-panel';
  test.innerHTML=[
    '<h2>API 模型测试</h2><p>测试结果只作为审核建议；调用失败显示未知，不会修改人工结论或自动触发告警。</p>',
    '<label>已保存的连接<select id="aiTestConnection"></select></label><p id="aiTestTarget" class="muted"></p>',
    '<label>待审核文本<textarea id="aiTestText" rows="3" maxlength="12000" placeholder="输入需要审核的文本；连通测试无需填写"></textarea></label>',
    '<label>测试图片（JPEG / PNG / WebP，最大 8 MiB）<input id="aiTestImage" type="file" accept="image/jpeg,image/png,image/webp"></label>',
    '<label class="check"><input id="aiTestCloud" type="checkbox">允许将本次测试内容发送至上述云端地址</label>',
    '<div class="row"><button id="aiProbe">测试文本连通性</button><button id="aiText" class="secondary">审核文本</button><button id="aiImage" class="secondary">审核所选图片</button></div>',
    '<p id="aiTestStatus" role="status"></p><div id="aiTestResult"></div>',
    '<details><summary>最近调用记录</summary><button id="aiRefreshLog" class="secondary">刷新记录</button><div id="aiLog"></div></details>'
  ].join('');
  $('page-models').prepend(test);
  const assist=document.createElement('details');assist.id='aiAssist';
  assist.innerHTML='<summary>AI 辅助复核当前画面</summary><p class="muted">只发送当前选中的这一帧。建议与人工结论分别记录。</p><label>连接<select id="aiReviewConnection"></select></label><p id="aiReviewTarget" class="muted"></p><label class="check"><input id="aiReviewCloud" type="checkbox">允许将当前画面发送至所选云端</label><button id="aiReviewSend" class="secondary">请求当前画面的审核建议</button><button id="aiReviewHistory" class="secondary">查看此事件的调用记录</button><p id="aiReviewStatus" role="status"></p><div id="aiReviewResult"></div>';
  $('evidenceBody').append(assist);
  const form=$('aiConnectionForm');
  let catalog=[],connections=[],editing=null,local=null;
  const defaults={enabled:true,vision:false,json_mode:false,timeout_seconds:90,max_output_tokens:2048,daily_limit:20};
  const statusNames={queued:'等待中',running:'调用中',completed:'完成',error:'失败 / 未知'};
  function target(selectId,targetId) {
    const c=connections.find(c=>c.id===$(selectId).value);
    $(targetId).textContent=c?c.config.name+' · '+c.config.model+' · '+c.config.base_url+' · '+(c.config.provider==='local'?'本地':'云端'):'请先在设置中保存连接';
  }
  function populate() {
    $('aiConnectionList').innerHTML=connections.map(c=>'<button type="button" class="secondary ai-card" data-id="'+esc(c.id)+'"><strong>'+esc(c.config.name)+'</strong><small>'+esc(c.config.model||'未填写模型 ID')+' · v'+c.version+'</small><small>今日 '+c.used_today+' / '+c.config.daily_limit+' · '+(c.config.enabled?'启用':'停用')+' · '+(c.config.vision?'声明支持图片':'文本')+'</small></button>').join('');
    $('aiConnectionList').querySelectorAll('[data-id]').forEach(b=>b.onclick=()=>fill(connections.find(c=>c.id===b.dataset.id)));
    for(const id of ['aiTestConnection','aiReviewConnection']){
      const value=$(id).value;
      $(id).innerHTML=connections.filter(c=>c.config.enabled).map(c=>'<option value="'+esc(c.id)+'">'+esc(c.config.name)+' · '+esc(c.config.model||'未配置模型')+'</option>').join('');
      if([...$(id).options].some(o=>o.value===value))$(id).value=value;
    }
    target('aiTestConnection','aiTestTarget');target('aiReviewConnection','aiReviewTarget');
  }
  function fill(c) {
    editing=c;
    const preset=catalog[0],cfg=c?c.config:{...defaults,...preset,name:'本地 Qwen',provider:preset.id,daily_limit:200};
    for(const [key,value] of Object.entries(cfg)){
      const input=form.elements.namedItem(key);if(!input)continue;
      if(input.type==='checkbox')input.checked=value;else input.value=value;
    }
    form.elements.api_key.value='';form.elements.clear_key.checked=false;
    $('aiModelOptions').replaceChildren();
    $('aiKeyState').textContent=c?'版本 v'+c.version+' · '+(c.has_saved_key?'已保存本机加密密钥':c.has_env_key?'环境变量密钥可用':'未配置密钥（本地通常不需要）'):'新连接；尚未保存';
    $('aiConnectionStatus').textContent='';
  }
  async function reload(){connections=await api('/ai/connections');populate();}
  async function act(button,status,action,isCurrent=()=>true) {
    button.disabled=true;$(status).textContent='正在处理…';
    try{await action();}catch(e){if(isCurrent())$(status).textContent=e.message;toast(e.message,true);}
    finally{button.disabled=false;}
  }
  function config() {
    const cfg={};
    for(const key of ['name','provider','protocol','base_url','model','key_env'])cfg[key]=form.elements[key].value.trim();
    for(const key of ['enabled','vision','json_mode'])cfg[key]=form.elements[key].checked;
    for(const key of ['daily_limit','timeout_seconds','max_output_tokens'])cfg[key]=Number(form.elements[key].value);
    return cfg;
  }
  $('aiProvider').onchange=()=>{
    const p=catalog.find(p=>p.id===form.elements.provider.value);
    for(const key of ['protocol','base_url','model','key_env'])form.elements[key].value=p[key];
    form.elements.name.value=p.name;form.elements.vision.checked=false;form.elements.json_mode.checked=false;
    form.elements.daily_limit.value=p.id==='local'?200:20;
    form.elements.api_key.value='';
    $('aiModelOptions').replaceChildren();
    $('aiConnectionStatus').textContent='提供商预设已载入草稿，请填写模型 ID 并保存。';
  };
  form.onsubmit=e=>{e.preventDefault();act(e.submitter||form.querySelector('button[type="submit"]'),'aiConnectionStatus',async()=>{
    const id=editing?.id||('conn-'+crypto.randomUUID());
    const body={expected_version:editing?.version||0,config:config(),clear_key:form.elements.clear_key.checked};
    if(form.elements.api_key.value)body.api_key=form.elements.api_key.value;
    let saved;
    try{saved=await api('/ai/connections/'+id,{method:'PUT',body});}
    finally{form.elements.api_key.value='';delete body.api_key;}
    await reload();fill(saved);$('aiConnectionStatus').textContent='已保存 v'+saved.version+'；可前往“素材与模型”测试。';
  });};
  $('aiNew').onclick=()=>fill(null);
  $('aiReload').onclick=e=>act(e.currentTarget,'aiConnectionStatus',async()=>{const id=editing?.id;await reload();fill(connections.find(c=>c.id===id)||connections[0]);$('aiConnectionStatus').textContent='已载入保存的配置';});
  $('aiModels').onclick=e=>act(e.currentTarget,'aiConnectionStatus',async()=>{
    if(!editing)throw new Error('先保存连接，再读取模型列表');
    const data=await api('/ai/connections/'+editing.id+'/models?expected_version='+editing.version,{method:'POST'});
    $('aiModelOptions').innerHTML=data.models.map(id=>'<option value="'+esc(id)+'"></option>').join('');
    $('aiConnectionStatus').textContent='读取到 '+data.models.length+' 个模型；点击模型 ID 输入框选择并保存。列表不代表支持图片。';
  });
  for(const pair of [['aiTestConnection','aiTestTarget','aiTestCloud'],['aiReviewConnection','aiReviewTarget','aiReviewCloud']]){
    $(pair[0]).onchange=()=>{target(pair[0],pair[1]);$(pair[2]).checked=false;};
  }
  function render(job) {
    let html='<div class="model-card"><strong>'+esc(statusNames[job.status]||job.status)+'</strong><p>'+esc(job.model)+' · 连接 v'+job.connection_version+' · '+esc(job.kind)+(job.event_id?' · 事件 #'+job.event_id:'')+'</p>';
    if(job.error)html+='<p class="error">'+esc(job.error)+'</p>';
    const r=job.result;
    if(r?.ready)html+='<p>文本接口测试成功。'+esc(r.scope)+'</p>';
    if(r?.scores) {
      html+='<p>AI 建议 · 未经业务校准 · '+(r.needs_review?'模型建议补充人工复核':'请由人工确认')+'</p>';
      html+='<ul>'+Object.entries(r.scores).map(([k,v])=>'<li>'+esc(labels()[k]||k)+'：'+v.toFixed(3)+'</li>').join('')+'</ul>';
      html+='<p>'+r.evidence.map(esc).join('；')+'</p>';
    }
    if(r?.usage)html+='<small>Token 用量：'+esc(JSON.stringify(r.usage))+'</small>';
    return html+'</div>';
  }
  async function log(){const rows=await api('/ai/requests');$('aiLog').innerHTML=rows.map(render).join('')||'<p>尚无调用记录</p>';}
  $('aiRefreshLog').onclick=e=>act(e.currentTarget,'aiTestStatus',async()=>{await log();$('aiTestStatus').textContent='记录已更新';});
  let reviewSequence=0;
  window.addEventListener('sg:evidence-selected',()=>{
    reviewSequence++;$('aiReviewResult').replaceChildren();$('aiReviewStatus').textContent='';$('aiReviewCloud').checked=false;
  });
  async function run(button,kind,review=false) {
    const status=review?'aiReviewStatus':'aiTestStatus',resultId=review?'aiReviewResult':'aiTestResult';
    const sequence=reviewSequence;
    const canRender=()=>!review||sequence===reviewSequence;
    await act(button,status,async()=>{
      const c=connections.find(c=>c.id===$(review?'aiReviewConnection':'aiTestConnection').value);
      if(!c)throw new Error('先在设置中保存并启用连接');
      const allow=$(review?'aiReviewCloud':'aiTestCloud').checked;
      if(c.config.provider!=='local'&&!allow)throw new Error('请先勾选允许将本次内容发送至云端');
      if(kind==='evidence'&&!c.config.vision)throw new Error('连接未启用图片能力；请先启动视觉服务，再在设置中启用图片输入。');
      const data={connection_id:c.id,expected_version:c.version,request_id:crypto.randomUUID(),kind,allow_cloud:allow,text:review?'':$('aiTestText').value};
      let job;
      if(review){
        const asset=getEvidence();if(!asset)throw new Error('当前没有可用证据画面');
        data.evidence_id=asset.id;
        job=await api('/ai/requests',{method:'POST',body:data});
      }else if(kind==='evidence'){
        const file=$('aiTestImage').files[0];if(!file)throw new Error('请选择图片');
        if(file.size>8*1024*1024)throw new Error('图片不能超过 8 MiB');
        const body=new FormData();body.append('metadata',JSON.stringify(data));body.append('file',file);
        job=await api('/ai/requests/image',{method:'POST',body});
      }else{job=await api('/ai/requests',{method:'POST',body:data});}
      const deadline=Date.now()+c.config.timeout_seconds*3000+30000;
      while(['queued','running'].includes(job.status)){
        if(canRender()){$(status).textContent=statusNames[job.status]+' · '+job.id.slice(0,8);$(resultId).innerHTML=render(job);}
        if(Date.now()>deadline)throw new Error('等待时间较长，请在最近调用记录查看结果；不要重复提交');
        await new Promise(resolve=>setTimeout(resolve,1500));
        job=await api('/ai/requests/'+job.id);
      }
      if(canRender()){$(resultId).innerHTML=render(job);$(status).textContent='调用'+statusNames[job.status]+'；记录已保存';}
      await reload();await log();
    },canRender);
  }
  $('aiProbe').onclick=e=>run(e.currentTarget,'probe');
  $('aiText').onclick=e=>run(e.currentTarget,'text');
  $('aiImage').onclick=e=>run(e.currentTarget,'evidence');
  $('aiReviewSend').onclick=e=>run(e.currentTarget,'evidence',true);
  $('aiReviewHistory').onclick=e=>act(e.currentTarget,'aiReviewStatus',async()=>{
    const event=getSelected();if(!event)throw new Error('先选择事件');
    const seq=reviewSequence,rows=await api('/ai/requests?event_id='+event.id);
    if(seq===reviewSequence){$('aiReviewResult').innerHTML=rows.map(render).join('')||'<p>此事件尚无 AI 调用</p>';$('aiReviewStatus').textContent='事件 #'+event.id+' 的调用记录';}
  });
  const info=await api('/ai/providers');catalog=info.providers;local=info.local;
  $('aiProvider').innerHTML=catalog.map(p=>'<option value="'+esc(p.id)+'">'+esc(p.name)+'</option>').join('');
  $('aiLocalInfo').textContent='模型：'+local.model_path+' · '+(local.model_exists?(local.model_bytes/1024**3).toFixed(2)+' GiB':'当前系统未找到')+'；服务程序：'+(local.server_exists?'已找到':'未找到')+'；'+local.message+' 密钥保存方式：'+local.secret_storage;
  await reload();fill(connections[0]||null);await log();
}

