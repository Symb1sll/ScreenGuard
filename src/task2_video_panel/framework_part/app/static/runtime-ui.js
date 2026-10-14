/* Source setup, operational diagnostics and response workflow. */
export async function initRuntime({api,esc,toast,S,when,openRoi,selectEvent,navigate,leaveDraft}) {
  const $=id=>document.getElementById(id), admin=S.role==='admin';
  const states={pending:'待调度',waiting:'模型调用中',submitting:'正在提交',cloud_pending:'等待云端',completed:'建议已返回',error:'结果未知',skipped:'未调用',cancelled:'已取消',expired:'排队超时'};
  const outcomes={unknown:'仍需人工判断',risk:'建议关注风险',low:'建议风险较低'};
  const action=async(button,fn)=>{button.disabled=true;try{await fn();}catch(e){toast(e.message,true);}finally{button.disabled=false;}};
  const link=document.createElement('link');link.rel='stylesheet';link.href='/static/runtime.css';document.head.append(link);
  const banner=document.createElement('div');banner.id='staleBanner';banner.className='notice error';banner.hidden=true;banner.setAttribute('role','status');
  document.querySelector('main header').after(banner);
  let lastOverview=0;
  window.addEventListener('sg:overview',()=>{
    lastOverview=Date.now();banner.hidden=true;
    for(const source of S.sources){
      const card=$('camera-'+source.id);if(!card)continue;
      const m=source.metrics;
      card.querySelector('.camera-model').textContent='分类器 '+source.active_model+' · '+(source.inference_state==='unvalidated'?'尚未业务验证':'推理 '+source.inference_state)+' · 自动大模型 '+(source.automatic_enabled?'已启用':'未启用')+' · 配置 v'+source.settings_version+' / 每路 v'+source.profile_version+(source.configuration_pending?'（有待生效修改）':'');
      let metrics=card.querySelector('.runtime-metrics');
      if(!metrics){metrics=document.createElement('p');metrics.className='runtime-metrics muted';card.querySelector('.camera-model').after(metrics);}
      metrics.textContent='最后识别 '+when(m.last_inference_at)+' · 耗时 '+(m.inference_ms==null?'—':Math.round(m.inference_ms)+' ms')+' · 排队延迟 '+(m.queue_lag_seconds==null?'—':m.queue_lag_seconds.toFixed(1)+' s')+' · 丢弃旧采样 '+m.dropped_frames;
      if(admin&&!card.querySelector('[data-source-profile]')){
        const b=document.createElement('button');b.className='secondary';b.dataset.sourceProfile=source.id;b.textContent='设备与自动审核设置';
        b.onclick=()=>openProfile(source.id).catch(e=>toast(e.message,true));card.querySelector('.camera-body .row:last-of-type').append(b);
      }
    }
  });
  setInterval(()=>{
    if(lastOverview&&Date.now()-lastOverview>15000){
      banner.hidden=false;banner.textContent='工作台数据已停止更新。最后成功更新：'+new Date(lastOverview).toLocaleTimeString('zh-CN')+'；画面和状态可能已过时，正在尝试恢复连接。';
    }
  },3000);

  const health=document.createElement('article');health.className='panel';health.innerHTML='<div class="sectionhead"><h2>采集、识别与后台任务</h2><button id="runtimeRefresh" class="secondary">刷新状态</button></div><p id="runtimeSummary">正在读取状态…</p><div id="runtimeTasks" class="runtime-tasks"></div><p id="autoQueue"></p><details><summary>最近自动审核记录</summary><div id="autoRecent"></div></details>';
  $('page-monitor').append(health);
  let healthBusy=false;
  async function refreshHealth(){
    if(healthBusy)return;healthBusy=true;
    try{
      const data=await api('/runtime/status');
      const expected=data.sources.filter(x=>x.expected), bad=expected.filter(x=>!x.healthy);
      $('runtimeSummary').textContent='更新于 '+when(data.server_time)+' · 后台 '+(data.ready?'就绪':'需要检查')+' · 应运行 '+expected.length+' 路'+(bad.length?'，'+bad.length+' 路采集或识别异常':'')+'。';
      const taskNames={notifications:'通知投递',evidence_cleanup:'证据清理',source_recovery:'采集恢复',faults:'异常检查',storage_metrics:'存储统计',automatic_review:'自动审核调度'};
      $('runtimeTasks').innerHTML=data.maintenance.map(t=>'<p class="'+(t.healthy?'':'error')+'">'+esc(taskNames[t.name]||t.name)+'：'+(t.healthy?'运行正常':'需要检查')+' · 最后成功 '+when(t.last_success)+(t.last_error?' · '+esc(t.last_error):'')+'</p>').join('');
      $('autoQueue').textContent='自动审核：'+Object.entries(data.automatic_review.counts).map(([k,n])=>(states[k]||k)+' '+n).join(' / ')+'；排队上限 '+data.automatic_review.queue_limit+'。建议不替代人工结论。';
      $('autoRecent').innerHTML=data.automatic_review.recent.map(j=>'<p><button class="secondary" data-auto-event="'+j.event_id+'">事件 #'+j.event_id+'</button> '+esc(states[j.status]||j.status)+' · '+esc(j.reason)+'</p>').join('')||'<p>尚无自动审核记录。管理员可在每路设置中启用。</p>';
      $('autoRecent').querySelectorAll('button').forEach(b=>b.onclick=async()=>{await navigate('review');await selectEvent(Number(b.dataset.autoEvent));});
    }catch(e){$('runtimeSummary').textContent='运行状态暂时不可用：'+e.message;}finally{healthBusy=false;}
  }
  $('runtimeRefresh').onclick=e=>action(e.currentTarget,refreshHealth);
  async function healthTick(){if(!document.hidden&&S.page==='monitor')await refreshHealth();setTimeout(healthTick,6000);}healthTick();

  const dialog=document.createElement('dialog');dialog.id='sourceProfileDialog';
  dialog.innerHTML='<div class="sectionhead"><h2 id="profileTitle"></h2><button id="profileClose" class="secondary">关闭</button></div><p id="profileVersion"></p><div class="row"><button id="profileProbe" class="secondary">1. 测试连接与画面</button><button id="profileRoi" class="secondary">2. 标定屏幕区域</button></div><p id="probeMessage" role="status"></p><img id="probeImage" alt="设备连接测试画面" hidden><form id="sourceProfileForm"><h3>3. 本路运行策略</h3><p>空白项继承全局默认；采样和分类器修改在下次启动时生效。</p><div class="split"><label>采样间隔（秒，空白继承）<input name="sample_seconds" type="number" min=".1" max="60" step=".1"></label><label>分类器<select name="model_id"></select></label></div><label>自动告警<select name="auto_alerts"><option value="">继承全局</option><option value="true">开启（仍须业务验证）</option><option value="false">关闭</option></select></label><details><summary>本路五类识别策略</summary><label class="check"><input name="custom_policies" type="checkbox">覆盖全局类别与阈值</label><div id="profilePolicies"></div><p class="muted">模型或阈值变更仍需先完成对应候选的离线对照。</p></details><details><summary>计划运行时段</summary><label class="check"><input name="schedule_enabled" type="checkbox">启用计划时段（视频流 / 本机摄像头）</label><div class="split"><label>开始<input name="schedule_start" type="time" required></label><label>结束<input name="schedule_end" type="time" required></label></div><p class="muted">使用全局设置时区；开始与结束相同表示全天。跨午夜归属开始日。点击开始后才启用计划，手动停止会取消计划运行。</p><div id="profileDays" class="row"></div></details><h3>自动大模型辅助审核</h3><label class="check"><input name="ai_enabled" type="checkbox">对本路新疑似片段自动请求模型建议</label><label>本地图片模型<select name="local_connection"></select></label><label class="check"><input name="allow_cloud" type="checkbox">允许本路少量疑似画面发送至所选云端</label><label>本地结果不确定或调用失败时，补充云端建议<select name="cloud_connection"></select></label><div class="split"><label>本路每天自动调用上限<input name="daily_limit" type="number" min="1" max="1000" required></label><label>两次自动调用最小间隔（秒）<input name="min_interval_seconds" type="number" min="1" max="3600" required></label><label>相同证据去重时间（秒）<input name="dedupe_seconds" type="number" min="0" max="86400" required></label><label>任务排队有效期（秒）<input name="ttl_seconds" type="number" min="30" max="3600" required></label></div><label class="check"><input name="include_audits" type="checkbox">低风险抽检也请求模型建议</label><p class="muted">保存后自动审核许可立即生效，旧配置排队任务取消；已发出的请求不能撤回。调用额度同时受连接限额约束。未校准的大模型分数不会直接触发告警或自动完成审核。</p><button type="submit">保存本路设置</button><p id="profileMessage" role="status"></p></form><button id="profileStart">4. 开始监控</button>';
  document.body.append(dialog);
  let currentSource=null,profileVersion=0,profileDraft='',previewUrl=null;
  const form=$('sourceProfileForm'),f=form.elements;
  function draft(){return JSON.stringify([...new FormData(form).entries()]);}
  function canLeave(){return !profileDraft||draft()===profileDraft||confirm('本路设置尚未保存，确认放弃修改？');}
  $('profileClose').onclick=()=>{if(canLeave())dialog.close();};
  dialog.addEventListener('cancel',e=>{if(!canLeave())e.preventDefault();});
  window.addEventListener('beforeunload',e=>{if(dialog.open&&profileDraft&&draft()!==profileDraft){e.preventDefault();e.returnValue='';}});
  dialog.addEventListener('close',()=>{if(previewUrl)URL.revokeObjectURL(previewUrl);previewUrl=null;});
  async function openProfile(sid){
    const [data,connections,models]=await Promise.all([api('/sources/'+sid+'/profile'),api('/ai/connections'),api('/models')]);
    currentSource=sid;profileVersion=data.version;const p=data.profile;
    $('profileTitle').textContent='视频源 #'+sid+' · 接入与每路设置';
    $('profileVersion').textContent='已保存每路 v'+data.version+' / 运行每路 v'+data.running_profile_version+' · 当前分类器 '+(data.running.model_id||'尚未启动');
    f.model_id.innerHTML='<option value="">继承全局</option>'+models.map(m=>'<option value="'+esc(m.model_id)+'">'+esc(m.model_id)+'</option>').join('');
    const options=(rows,empty)=>'<option value="">'+empty+'</option>'+rows.map(c=>'<option value="'+esc(c.id)+'">'+esc(c.config.name)+' · '+esc(c.config.model)+(c.config.vision?'':'（尚未启用图片）')+'</option>').join('');
    f.local_connection.innerHTML=options(connections.filter(c=>c.config.provider==='local'),'请选择本地连接');
    f.cloud_connection.innerHTML=options(connections.filter(c=>c.config.provider!=='local'),'不调用云端');
    for(const k of ['sample_seconds','model_id','auto_alerts','schedule_start','schedule_end'])f[k].value=p[k]??'';
    f.schedule_enabled.checked=p.schedule_enabled;f.custom_policies.checked=p.policies!==null;
    const policies=p.policies||data.effective.settings.policies;
    $('profilePolicies').innerHTML=Object.entries(S.labels).map(([k,n])=>'<div class="filters"><label class="check"><input type="checkbox" data-label="'+k+'" data-value="enabled" '+(policies[k].enabled?'checked':'')+'>'+esc(n)+'</label><label>复核阈值<input type="number" min="0" max=".99999" step="any" data-label="'+k+'" data-value="review" value="'+policies[k].review+'"></label><label>告警阈值<input type="number" min=".00001" max="1" step="any" data-label="'+k+'" data-value="alert" value="'+policies[k].alert+'"></label></div>').join('');
    $('profilePolicies').querySelectorAll('[data-label]').forEach(x=>x.name='policy_'+x.dataset.label+'_'+x.dataset.value);
    $('profileDays').innerHTML=['一','二','三','四','五','六','日'].map((n,i)=>'<label class="check"><input name="day" type="checkbox" value="'+i+'" '+(p.schedule_days.includes(i)?'checked':'')+'>周'+n+'</label>').join('');
    for(const [k,v] of Object.entries(p.ai)){const input=f[k==='enabled'?'ai_enabled':k];if(input.type==='checkbox')input.checked=v;else input.value=v;}
    $('profileMessage').textContent='';$('probeMessage').textContent='';$('probeImage').hidden=true;
    profileDraft=draft();dialog.showModal();
  }
  $('profileProbe').onclick=e=>action(e.currentTarget,async()=>{
    $('probeMessage').textContent='正在测试连接…';
    try{
      const blob=await api('/sources/'+currentSource+'/probe',{method:'POST',blob:true});
      if(previewUrl)URL.revokeObjectURL(previewUrl);previewUrl=URL.createObjectURL(blob);
      $('probeImage').src=previewUrl;$('probeImage').hidden=false;$('probeMessage').textContent='已取得画面。请确认屏幕区域，再标定四角。';
    }catch(e){$('probeMessage').textContent=e.message;throw e;}
  });
  $('profileRoi').onclick=()=>{if(canLeave()){dialog.close();openRoi(currentSource).catch(e=>toast(e.message,true));}};
  $('profileStart').onclick=e=>action(e.currentTarget,async()=>{
    if(draft()!==profileDraft)throw new Error('请先保存设置，再开始监控');
    await api('/video/'+currentSource+'/start',{method:'POST'});toast('已提交开始请求；计划时段外将等待');dialog.close();
  });
  form.onsubmit=e=>{e.preventDefault();action(form.querySelector('[type=submit]'),async()=>{
    let policies=null;if(f.custom_policies.checked){policies={};$('profilePolicies').querySelectorAll('[data-label]').forEach(x=>{policies[x.dataset.label]??={};policies[x.dataset.label][x.dataset.value]=x.type==='checkbox'?x.checked:Number(x.value);});}
    const profile={sample_seconds:f.sample_seconds.value===''?null:Number(f.sample_seconds.value),model_id:f.model_id.value||null,auto_alerts:f.auto_alerts.value===''?null:f.auto_alerts.value==='true',policies,
      schedule_enabled:f.schedule_enabled.checked,schedule_start:f.schedule_start.value,schedule_end:f.schedule_end.value,schedule_days:[...form.querySelectorAll('[name=day]:checked')].map(x=>Number(x.value)),
      ai:{enabled:f.ai_enabled.checked,local_connection:f.local_connection.value,cloud_connection:f.cloud_connection.value,allow_cloud:f.allow_cloud.checked,include_audits:f.include_audits.checked}};
    for(const k of ['daily_limit','min_interval_seconds','dedupe_seconds','ttl_seconds'])profile.ai[k]=Number(f[k].value);
    const result=await api('/sources/'+currentSource+'/profile',{method:'PUT',body:{expected_version:profileVersion,profile}});
    profileVersion=result.version;profileDraft=draft();$('profileVersion').textContent='已保存每路 v'+result.version+'；采集策略下次启动生效';$('profileMessage').textContent='已保存 v'+result.version+'；'+result.effective;toast('本路设置已保存');
  });};

  if(admin){
    const webcam=document.createElement('form');webcam.id='webcamForm';webcam.innerHTML='<h3>本机 USB / 内置摄像头</h3><div class="filters"><label>名称<input name="name" maxlength="80" value="本机摄像头" required></label><label>设备编号<input name="device_index" type="number" min="0" max="15" value="0" required></label><button type="submit">添加并设置</button></div><p class="muted">编号通常从 0 开始。添加后点击测试画面；摄像头属于运行服务的主机。</p>';
    $('sourceForm').append(webcam);webcam.onsubmit=e=>{e.preventDefault();action(webcam.querySelector('button'),async()=>{const v=webcam.elements,result=await api('/video/webcam',{method:'POST',body:{name:v.name.value,device_index:Number(v.device_index.value)}});await openProfile(result.source_id);});};
  }

  const advice=document.createElement('div');advice.id='automaticAdvice';$('modelAdvice').after(advice);
  const related=document.createElement('div');related.id='relatedSegments';$('evidenceMeta').after(related);
  const response=document.createElement('section');response.id='casePanel';response.hidden=true;response.innerHTML='<h3>风险处置</h3><p id="caseHint"></p><form id="caseForm"><label>处置状态<select name="state"><option value="pending">待处置</option><option value="in_progress">处理中</option><option value="resolved">已解决</option></select></label><label>负责人<input name="owner" maxlength="80"></label><label>处置说明<textarea name="note" maxlength="1000" rows="3" required></textarea></label><button type="submit">保存处置记录</button></form><details><summary>处置历史</summary><div id="caseHistory"></div></details>';
  document.querySelector('.verdict').append(response);
  let selection='',caseData=null,caseBaseline='';
  function renderAdvice(job){
    advice.innerHTML='<h4>自动大模型建议</h4>'+(job?'<p>'+esc(states[job.status]||job.status)+' · '+esc(outcomes[job.outcome]||'')+' · '+esc(job.reason)+'</p>'+job.requests.map(r=>'<p>'+esc(r.model)+' · '+esc(r.status)+' · '+(r.result?esc((r.result.evidence||[]).join('；')):esc(r.error))+'</p>'+(r.result?'<p class="muted">'+Object.entries(r.result.scores||{}).map(([k,v])=>esc(S.labels[k]||k)+' '+Number(v).toFixed(2)).join(' · ')+'（辅助分数，未经业务校准）</p>':'')).join(''):'<p>此事件尚无自动请求记录。疑似片段固定后按每路设置调度。</p>');
  }
  async function adviceTick(){
    const e=S.selected;
    if(!document.hidden&&S.page==='review'&&e&&['pending','waiting','submitting','cloud_pending'].includes(e.automatic_review?.status)){
      try{
        const updated=await api('/events/'+e.id);
        // Refresh only advice; never replace an in-progress review or disposition draft.
        if(S.selected===e){e.automatic_review=updated.automatic_review;renderAdvice(e.automatic_review);}
      }catch{/* The shared stale-data banner reports connectivity failures. */}
    }
    setTimeout(adviceTick,4000);
  }
  adviceTick();
  window.addEventListener('sg:evidence-selected',()=>{
    const e=S.selected;if(!e)return;response.hidden=false;
    renderAdvice(e.automatic_review);
    const key=e.id+':'+e.review_version+':'+e.content_version+':'+(e.disposition?.current.version||0);
    if(selection===key)return;selection=key;
    related.innerHTML=e.related_segments.length?'<p>同一事件的其他片段：'+e.related_segments.map(x=>'<button class="secondary" data-segment="'+x.id+'">#'+x.id+' · '+x.media_start.toFixed(0)+'–'+x.media_end.toFixed(0)+'s</button>').join('')+'</p>':'';
    related.querySelectorAll('button').forEach(b=>b.onclick=()=>selectEvent(Number(b.dataset.segment)));
    caseData=e.disposition;const v=caseData.current,cf=$('caseForm');
    cf.hidden=!caseData.applicable;$('caseHint').textContent=caseData.applicable?'人工已确认风险，请记录实际处置结果。通知签收不会完成处置。':'人工确认风险后可登记处置；既有处置历史继续保留。';
    cf.elements.state.value=v.state;cf.elements.owner.value=v.owner;cf.elements.note.value='';caseBaseline=JSON.stringify([...new FormData(cf).entries()]);
    $('caseHistory').innerHTML=caseData.history.map(h=>'<p>'+when(h.created_at)+' · '+esc(h.actor)+' · '+esc({pending:'待处置',in_progress:'处理中',resolved:'已解决'}[h.state])+' · '+esc(h.owner)+'<br>'+esc(h.note)+'</p>').join('')||'<p>暂无处置记录</p>';
  });
  window.addEventListener('sg:before-leave',e=>{
    const cf=$('caseForm');
    if(!cf.hidden&&caseBaseline&&JSON.stringify([...new FormData(cf).entries()])!==caseBaseline&&!confirm('处置说明尚未保存，确认放弃？'))e.preventDefault();
  });
  window.addEventListener('beforeunload',e=>{if(caseBaseline&&!$('caseForm').hidden&&JSON.stringify([...new FormData($('caseForm')).entries()])!==caseBaseline){e.preventDefault();e.returnValue='';}});
  $('caseForm').onsubmit=e=>{e.preventDefault();const cf=e.currentTarget;action(cf.querySelector('button'),async()=>{
    if(!leaveDraft(true))return;
    const source=S.selected;
    await api('/events/'+source.id+'/disposition',{method:'POST',body:{state:cf.elements.state.value,owner:cf.elements.owner.value,note:cf.elements.note.value,expected_version:caseData.current.version,review_version:source.review_version}});
    caseBaseline=JSON.stringify([...new FormData(cf).entries()]);selection='';await selectEvent(source.id);toast('处置记录已保存');
  });};
  const caseList=document.createElement('article');caseList.className='panel';caseList.innerHTML='<div class="sectionhead"><h3>已确认风险的处置队列</h3><button id="refreshCases" class="secondary">刷新处置队列</button></div><label>范围<select id="caseFilter"><option value="open">未完成处置</option><option value="all">全部</option><option value="resolved">已解决</option></select></label><div id="caseQueue"></div><div class="row"><button id="casePrev" class="secondary">上一页</button><span id="casePage"></span><button id="caseNext" class="secondary">下一页</button></div>';
  $('page-events').append(caseList);let casePage=1;
  async function loadCases(){const result=await api('/dispositions?state='+$('caseFilter').value+'&page='+casePage);$('caseQueue').innerHTML=result.items.map(x=>'<p><button class="secondary" data-case="'+x.id+'">事件 #'+x.id+'</button> '+esc(x.source_name)+' · '+esc({pending:'待处置',in_progress:'处理中',resolved:'已解决'}[x.state])+' · '+esc(x.owner||'未填写负责人')+'</p>').join('')||'<p>此范围暂无事件</p>';$('casePage').textContent=casePage+' 页 / '+result.total+' 条';$('casePrev').disabled=casePage<=1;$('caseNext').disabled=casePage*30>=result.total;$('caseQueue').querySelectorAll('button').forEach(b=>b.onclick=async()=>{await navigate('review');await selectEvent(Number(b.dataset.case));});}
  $('refreshCases').onclick=e=>action(e.currentTarget,loadCases);$('caseFilter').onchange=()=>{casePage=1;loadCases();};$('casePrev').onclick=()=>{casePage--;loadCases();};$('caseNext').onclick=()=>{casePage++;loadCases();};
  window.addEventListener('sg:navigate',e=>{if(e.detail==='events')loadCases().catch(e=>toast(e.message,true));});
}

