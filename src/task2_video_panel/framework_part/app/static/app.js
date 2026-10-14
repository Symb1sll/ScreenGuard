const $=id=>document.getElementById(id);
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const S={page:'monitor',labels:{},csrf:'',sources:[],models:[],config:null,history:[],eventPage:1,queuePage:1,queue:[],selected:null,selectedAt:0,view:'roi',frameRole:'first',latestId:0,compareId:null,compareStatus:'',candidate:null,overview:null};
const names={monitor:'监控总览',events:'事件中心',review:'人工复核',models:'素材与模型',settings:'设置',operations:'运行与通知'};
const statusNames={scheduled:'等待计划时段',created:'待开始',starting:'正在启动',running:'采集中',finished:'文件已完成',stopped:'已停止',stopping:'正在停止',error:'异常',reconnecting:'正在重连',pending:'待复核',confirmed:'人工确认风险',normal:'正常 / 误报',uncertain:'仍不确定',expired_unreviewed:'过期未审',review:'疑似风险',alert:'告警事件',unknown:'无法判断',audit:'低风险抽检',ready:'推理就绪',unvalidated:'尚未业务验证',loading:'正在加载',waiting:'等待画面',idle:'未启动',demo:'演示规则',candidate:'候选模型',validated:'已验证',invalid:'无效模型'};
const pill=(value,label)=>'<span class="pill '+esc(value)+'">'+esc(label||statusNames[value]||value)+'</span>';
const when=t=>t?new Date(Number(t)*1000).toLocaleString('zh-CN',{hour12:false,timeZone:S.config?.settings.timezone||'Asia/Shanghai'}):'—';
const size=n=>(n/1024**3).toFixed(2)+' GiB';
function toast(message,error=false){$('toast').textContent=message;$('toast').className=error?'error':'';$('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').hidden=true,error?12000:5000);}
async function fetchWithDeadline(url,request={}){
  const controller=new AbortController();
  const longOperation=url.includes('/models/import')||url.includes('/video/local')||url==='/api/operations/backups';
  const timer=setTimeout(()=>controller.abort(),longOperation?180000:20000);
  try{
    const response=await fetch(url,{...request,signal:controller.signal});
    const body=await response.arrayBuffer();
    return new Response(body,{status:response.status,statusText:response.statusText,headers:response.headers});
  }catch(e){if(e.name==='AbortError')throw new Error('请求超时；操作可能已完成，请刷新状态确认');throw e;}
  finally{clearTimeout(timer);}
}
let sessionRenewal=null;
async function renewSession(){
  if(!sessionRenewal)sessionRenewal=(async()=>{
    const r=await fetchWithDeadline('/api/bootstrap',{cache:'no-store'});
    if(r.status===401){location.replace('/login');throw new Error('会话已过期，请重新登录');}
    if(!r.ok)throw new Error('无法恢复会话，请刷新页面');
    const data=await r.json();S.csrf=data.csrf;
  })().finally(()=>{sessionRenewal=null;});
  return sessionRenewal;
}
async function api(path,opts={}){
  const headers={...(opts.headers||{})};
  if(Date.now()-(S.interactionAt||0)<15000)headers['X-SG-Interaction']='1';
  if(opts.method&&opts.method!=='GET')headers['X-SG-CSRF']=S.csrf;
  const request={...opts,headers,cache:'no-store'};
  if(opts.body && !(opts.body instanceof FormData)){headers['Content-Type']='application/json';request.body=JSON.stringify(opts.body);}
  let r=await fetchWithDeadline('/api'+path,request);
  if(r.status===401&&path==='/bootstrap'){location.replace('/login');throw new Error('请登录');}
  if(r.status===401&&path!=='/bootstrap'){
    await renewSession();
    if(opts.method&&opts.method!=='GET')headers['X-SG-CSRF']=S.csrf;
    r=await fetchWithDeadline('/api'+path,request);
  }
  if(!r.ok){let data;try{data=await r.json();}catch{data={detail:'请求失败：'+r.status};}
    const detail=Array.isArray(data.detail)?data.detail.map(x=>x.msg).join('；'):data.detail;
    const error=new Error(detail||'操作失败');error.status=r.status;throw error;}
  return opts.blob?r.blob():r.json();
}
async function busy(button,action,status){
  const old=button.textContent;button.disabled=true;button.textContent='处理中…';
  if(status){status.textContent='正在处理…';status.classList.remove('error');}
  try{await action();}catch(e){if(status){status.textContent=e.message;status.classList.add('error');}toast(e.message,true);}
  finally{
    button.disabled=false;button.textContent=old;
    if(button.id==='undoReview')button.disabled=!S.selected||S.selected.expired||S.selected.reviews[0]?.operation!=='review';
    if(button.dataset.verdict)button.disabled=!S.selected||S.selected.expired||S.selected.lifecycle!=='closed';
    if(button.dataset.action&&button.dataset.action!=='roi')drawSources();
  }
}
function populateSelect(el,items,value,empty=false){
  const html=(empty?'<option value="">全部</option>':'')+items.map(x=>'<option value="'+esc(x.value)+'">'+esc(x.label)+'</option>').join('');
  if(el.dataset.options!==html){el.innerHTML=html;el.dataset.options=html;}
  if([...el.options].some(o=>o.value===value))el.value=value;
}
function sourceSelects(){
  for(const id of ['eventSource','reviewSource'])populateSelect($(id),S.sources.map(x=>({value:String(x.id),label:x.name})),$(id).value,true);
}
function modelSelects(){
  const items=S.models.map(m=>({value:m.model_id,label:m.model_id+' · '+(statusNames[m.validation_status]||m.validation_status)}));
  for(const id of ['activeModel','candidateModel'])populateSelect($(id),items,$(id).value||S.config?.settings.model_id);
}
function policiesHTML(p,prefix){
  return '<table class="policy-table"><thead><tr><th>类别</th><th>启用</th><th>复核阈值</th><th>告警阈值</th></tr></thead><tbody>'+Object.entries(S.labels).map(([k,label])=>
    '<tr><td>'+esc(label)+'</td><td><input type="checkbox" aria-label="'+esc(label)+'启用" data-policy="'+k+'" data-field="enabled" '+(p[k].enabled?'checked':'')+'></td><td><input aria-label="'+esc(label)+'复核阈值" type="number" min="0" max=".999999" step="any" required data-policy="'+k+'" data-field="review" value="'+p[k].review+'"></td><td><input aria-label="'+esc(label)+'告警阈值" type="number" min="0" max="1" step="any" required data-policy="'+k+'" data-field="alert" value="'+p[k].alert+'"></td></tr>').join('')+'</tbody></table>';
}
function readPolicies(container){
  const p={};
  container.querySelectorAll('[data-policy]').forEach(input=>{const k=input.dataset.policy;p[k]??={};p[k][input.dataset.field]=input.type==='checkbox'?input.checked:Number(input.value);});
  return p;
}
async function navigate(page){
  if(S.role==='reviewer'&&['models','settings'].includes(page))page='review';
  if(S.page==='review'&&page!=='review'&&!leaveDraft())return;
  S.page=page;document.querySelectorAll('.page').forEach(el=>el.hidden=el.id!=='page-'+page);
  document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.page===page));
  $('pageTitle').textContent=names[page];location.hash=page;
  window.dispatchEvent(new CustomEvent('sg:navigate',{detail:page}));
  try{
    if(page==='events')await loadEvents();
    if(page==='review')await loadQueue();
    if(page==='models'){await loadModels();await loadComparisons();}
    if(page==='settings'&&!S.config)await loadSettings();
  }catch(e){toast(e.message,true);}
}
document.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>navigate(b.dataset.page));
$('refreshBtn').onclick=e=>busy(e.currentTarget,async()=>{await updateOverview();if(S.page==='events')await loadEvents();if(S.page==='review')await loadQueue();if(S.page==='models')await loadComparisons();toast('数据已更新');});
$('showSource').onclick=()=>{$('sourceForm').hidden=!$('sourceForm').hidden;};
function drawSources(){
  const ids=new Set(S.sources.map(x=>String(x.id)));
  $('cameras').querySelectorAll('.camera').forEach(el=>{if(!ids.has(el.dataset.id))el.remove();});
  if(!S.sources.length){$('cameras').innerHTML='<div class="panel empty" id="noSources">还没有视频源。添加本地视频即可验证审核流程。</div>';return;}
  $('noSources')?.remove();
  for(const src of S.sources){
    let card=$('camera-'+src.id);
    if(!card){
      card=document.createElement('article');card.className='camera';card.id='camera-'+src.id;card.dataset.id=src.id;
      card.innerHTML='<div class="camera-image"><span class="placeholder">等待采集画面</span><img alt="最新采样截图" hidden></div><div class="camera-body"><div class="row"><h3></h3><span class="camera-state"></span></div><p class="camera-time"></p><p class="camera-message"></p><p class="camera-model muted"></p><div class="row"><button data-action="start">开始</button><button data-action="stop" class="secondary">停止</button><button data-action="roi" class="secondary">标定屏幕</button></div><p class="inline-status"></p></div>';
      $('cameras').append(card);
      card.querySelectorAll('[data-action]').forEach(b=>b.onclick=()=>busy(b,async()=>{
        if(b.dataset.action==='roi'){await openRoi(src.id);return;}
        await api('/video/'+src.id+'/'+b.dataset.action,{method:'POST'});
        card.querySelector('.inline-status').textContent=b.dataset.action==='start'?'启动请求已提交':'停止请求已提交';await updateOverview();
      },card.querySelector('.inline-status')));
      const image=card.querySelector('img');image.onload=()=>{image.hidden=false;card.querySelector('.placeholder').hidden=true;};
      image.onerror=()=>{image.hidden=true;card.querySelector('.placeholder').hidden=false;delete card.dataset.frame;};
    }
    card.querySelector('h3').textContent=src.name;card.querySelector('.camera-state').innerHTML=pill(src.status);
    const age=src.last_frame_at?Math.max(0,Math.floor(Date.now()/1000-src.last_frame_at)):null;
    card.querySelector('.camera-time').textContent=age===null?'尚无采样画面':'采集于 '+when(src.last_frame_at)+' · '+age+' 秒前';
    card.querySelector('.camera-message').textContent=src.last_message||'';
    card.querySelector('.camera-model').textContent='推理：'+(statusNames[src.inference_state]||src.inference_state)+' · 已处理 '+src.processed+' 帧 · 配置 v'+src.settings_version+' · 屏幕 '+src.regions.regions.length+' 个';
    const active=['starting','running','reconnecting','stopping','scheduled'].includes(src.status);
    card.querySelector('[data-action=start]').disabled=active;
    card.querySelector('[data-action=stop]').disabled=!active||src.status==='stopping';
    if(S.role==='reviewer')card.querySelectorAll('[data-action]').forEach(b=>b.hidden=true);
    if(src.last_frame_at&&card.dataset.frame!==String(src.last_frame_at)){
      card.dataset.frame=src.last_frame_at;card.querySelector('img').src=src.frame_url+'?t='+src.last_frame_at;
    }
  }
}
let overviewInFlight=false;
async function updateOverview(){
  if(overviewInFlight)return;overviewInFlight=true;
  try{
    const data=await api('/overview');S.overview=data;S.sources=data.sources;
    $('connection').textContent='服务已连接';$('connection').className='';
    const fresh=data.sources.filter(s=>s.status==='running'&&data.server_time-(s.last_frame_at||0)<Math.max(10,s.sampling_seconds*2+2)).length;
    $('m-online').textContent=fresh+' / '+data.sources.length;
    $('m-pending').textContent=data.counts.pending;$('m-uncertain').textContent='另有 '+data.counts.uncertain+' 条仍不确定';
    $('m-alerts').textContent=data.counts.alerts;$('m-confirmed').textContent=data.counts.confirmed;
    $('currentModel').textContent=data.model_id;
    $('notice').textContent=data.model_id==='demo-rules'?'演示识别模式：颜色规则仅验证流程。高分结果也进入人工复核，不作为真实内容告警。':'模型建议与人工结论分开记录。候选模型或未经验证的阈值不会触发自动告警。';
    $('batchInfo').textContent='本批次截止 '+when(data.batch.cutoff)+'；下一次 '+when(data.batch.next_at)+'（'+data.batch.timezone+'）';
    $('expiryInfo').textContent='临期未审 '+data.counts.expiring+' 条 · 过期未审 '+data.counts.expired_unreviewed+' 条 · 平均审核 '+(data.review_seconds_mean==null?'暂无数据':data.review_seconds_mean.toFixed(1)+' 秒');
    $('storageBar').style.width=Math.min(100,100*data.storage.used_bytes/data.storage.quota_bytes)+'%';
    $('storageInfo').textContent='证据 '+size(data.storage.used_bytes)+' / '+size(data.storage.quota_bytes)+' · 磁盘可用 '+size(data.storage.free_bytes);
    $('storageError').textContent=data.storage.error;
    drawSources();sourceSelects();window.dispatchEvent(new CustomEvent('sg:overview'));
    if(S.page==='review'){const info=await api('/events?state=queue&limit=1');$('newEvents').textContent=info.latest_id>S.latestId?'有新事件，点击“更新队列”载入；当前审核保持不变':'';}
  }catch(e){$('connection').textContent='服务连接中断';$('connection').className='error';throw e;}
  finally{overviewInFlight=false;}
}
for(const id of ['uploadForm','rtspForm']){
  $(id).onsubmit=e=>{e.preventDefault();const form=e.currentTarget;const button=form.querySelector('button[type=submit]');
    busy(button,async()=>{
      const fd=new FormData(form);let result;
      if(id==='uploadForm'){fd.set('auto_start',String(form.elements.auto_start.checked));result=await api('/video/local',{method:'POST',body:fd});form.elements.file.value='';}
      else{result=await api('/video/rtsp',{method:'POST',body:{name:fd.get('name'),url:fd.get('url'),auto_start:form.elements.auto_start.checked}});form.elements.url.value='';}
      form.querySelector('.inline-status').textContent=result.started?'已添加并开始检测':'已添加，等待手动开始（最多四路同时运行）';await updateOverview();
    },form.querySelector('.inline-status'));
  };
}
function eventParams(queue=false){
  const q=new URLSearchParams({state:queue?'queue':$('eventState').value,page:String(queue?S.queuePage:S.eventPage),limit:'20'});
  const source=$(queue?'reviewSource':'eventSource').value,label=$(queue?'reviewLabel':'eventLabel').value;
  if(source)q.set('source_id',source);if(label)q.set('label',label);
  if(queue&&$('reviewScope').value==='batch')q.set('batch_only','true');
  if(queue&&$('reviewScope').value==='alert')q.set('kind','alert');
  if(queue&&$('reviewScope').value==='expiring')q.set('expiring','true');
  return q.toString();
}
function eventName(e){return e.labels.length?e.labels.map(k=>S.labels[k]).join(' · '):(e.kind==='unknown'?'无法判断':e.kind==='audit'?'低风险抽检':'待复核');}
let eventsSeq=0,queueSeq=0;
async function loadEvents(){
  const seq=++eventsSeq;
  const data=await api('/events?'+eventParams());if(seq!==eventsSeq)return;S.eventTotal=data.total;
  $('eventTable').innerHTML=data.items.map(e=>'<div class="event-row">'+(e.thumbnail_id?'<img loading="lazy" src="/api/evidence/'+e.thumbnail_id+'" alt="事件缩略图">':'<span class="muted">无证据</span>')+'<div><h3>#'+e.id+' '+esc(eventName(e))+' '+pill(e.review_state)+'</h3><p>'+esc(e.source_name)+' / '+esc(e.roi_id)+' · '+when(e.started_at)+'</p><p>'+pill(e.kind)+' · '+e.samples+' 个采样 · '+esc(e.reason)+'</p></div><button data-event="'+e.id+'">查看 / 审核</button></div>').join('')||'<div class="empty">没有符合条件的事件</div>';
  $('eventTable').querySelectorAll('[data-event]').forEach(b=>b.onclick=async()=>{await navigate('review');await selectEvent(Number(b.dataset.event));});
  $('eventPage').textContent='第 '+S.eventPage+' 页 · '+data.total+' 条';
  $('eventPrev').disabled=S.eventPage<=1;$('eventNext').disabled=S.eventPage*20>=data.total;
}
$('eventFilter').onclick=()=>{S.eventPage=1;loadEvents().catch(e=>toast(e.message,true));};
$('eventPrev').onclick=()=>{if(S.eventPage>1){S.eventPage--;loadEvents();}};
$('eventNext').onclick=()=>{S.eventPage++;loadEvents();};
async function loadQueue(){
  const seq=++queueSeq,data=await api('/events?'+eventParams(true));if(seq!==queueSeq)return;S.queue=data.items;S.latestId=data.latest_id;
  $('queueCount').textContent=data.total+' 条';$('newEvents').textContent='';
  $('queue').innerHTML=data.items.map(e=>'<button class="queue-item '+(S.selected?.id===e.id?'active':'')+'" data-event="'+e.id+'"><strong>#'+e.id+' '+esc(eventName(e))+'</strong><small>'+esc(e.source_name)+' · '+esc(e.roi_id)+'</small><small>'+when(e.started_at)+'</small>'+pill(e.review_state)+' '+pill(e.kind)+'</button>').join('')||'<p class="empty">此范围暂无待复核事件</p>';
  $('queue').querySelectorAll('[data-event]').forEach(b=>b.onclick=()=>selectEvent(Number(b.dataset.event)).catch(e=>toast(e.message,true)));
  $('queuePage').textContent=S.queuePage+' 页';$('queuePrev').disabled=S.queuePage<=1;$('queueNext').disabled=S.queuePage*20>=data.total;
}
$('reviewFilter').onclick=()=>{S.queuePage=1;document.querySelector('.review-layout').classList.remove('viewing-detail');loadQueue().catch(e=>toast(e.message,true));};
$('queuePrev').onclick=()=>{if(S.queuePage>1){S.queuePage--;loadQueue();}};
$('queueNext').onclick=()=>{S.queuePage++;loadQueue();};
let selectionSeq=0;
function reviewDraft(){return JSON.stringify({note:$('reviewNote').value,labels:[...$('reviewLabels').querySelectorAll('input:checked')].map(x=>x.value)});}
function hasDraft(){return S.selected&&S.draftBaseline!==undefined&&reviewDraft()!==S.draftBaseline;}
function leaveDraft(skipExternal=false){if(!skipExternal&&!window.dispatchEvent(new Event('sg:before-leave',{cancelable:true})))return false;if(!hasDraft())return true;if(!confirm('当前审核备注或类别尚未保存，确认放弃这些修改？'))return false;S.draftBaseline=reviewDraft();return true;}
window.addEventListener('beforeunload',e=>{if(hasDraft()){e.preventDefault();e.returnValue='';}});
for(const event of ['pointerdown','keydown'])document.addEventListener(event,()=>{S.interactionAt=Date.now();},{passive:true});
async function selectEvent(id){
  if(!leaveDraft())return;
  const seq=++selectionSeq,e=await api('/events/'+id);if(seq!==selectionSeq)return;
  S.selected=e;S.selectedAt=performance.now();S.frameRole='first';$('reviewEmpty').hidden=true;$('evidenceBody').hidden=false;
  $('evidenceTitle').textContent='事件 #'+e.id+' · '+e.source_name;
  $('evidenceMeta').textContent=when(e.started_at)+' · '+e.roi_id+' · '+e.media_start.toFixed(1)+'–'+e.media_end.toFixed(1)+' 秒 · 内容 v'+e.content_version+' · '+(e.lifecycle==='open'?'画面仍在更新，请先固定片段':'证据范围已固定')+(e.evidence_error?' · '+e.evidence_error:'');
  $('freezeEvent').hidden=e.expired||e.lifecycle!=='open';
  $('reviewNote').value=e.review_note||'';$('reasonPreset').value='';
  $('reviewLabels').querySelectorAll('input').forEach(input=>input.checked=e.review_labels.includes(input.value));
  S.draftBaseline=reviewDraft();
  $('reviewStatus').textContent=statusNames[e.review_state]||e.review_state;$('reviewStatus').className='';
  $('imageZoom').value=100;$('evidenceImage').style.width='100%';
  $('queue').querySelectorAll('[data-event]').forEach(b=>b.classList.toggle('active',Number(b.dataset.event)===id));
  $('modelAdvice').innerHTML='<p>'+esc(e.reason)+'</p><p>模型 '+esc(e.model_id)+' · 配置 v'+e.settings_version+' · 区域 v'+e.roi_version+'</p>'+Object.entries(e.scores).map(([k,v])=>'<p>'+esc(S.labels[k]||k)+'：'+Number(v).toFixed(3)+'</p>').join('')+'<small>分数不是正确概率；分类结果不提供精确风险位置。</small>';
  $('reviewHistory').innerHTML=e.reviews.map(r=>'<div class="history-entry">'+when(r.created_at)+' · '+esc(r.actor)+' · '+esc(r.operation==='undo'?'撤销':statusNames[r.result.state])+'<br>'+esc(r.result.note||'')+'</div>').join('')||'<p>暂无审核记录</p>';
  $('undoReview').disabled=e.expired||!e.reviews.length||e.reviews[0].operation!=='review';
  document.querySelectorAll('[data-verdict]').forEach(b=>b.disabled=!!e.expired||e.lifecycle!=='closed');
  drawEvidence();
  document.querySelector('.review-layout').classList.add('viewing-detail');
  if(matchMedia('(max-width:800px)').matches)$('evidenceBody').scrollIntoView({block:'start'});
}
$('backToQueue').onclick=()=>{document.querySelector('.review-layout').classList.remove('viewing-detail');$('queue').scrollIntoView({block:'start'});};
function drawEvidence(){
  const e=S.selected;if(!e)return;
  const roleNames={before:'前一帧',first:'首帧',peak:'峰值帧',last:'结束帧',after:'后一帧'};
  const assets=e.evidence.filter(a=>a.role.endsWith('_'+S.view));
  if(!assets.some(a=>a.role===S.frameRole+'_'+S.view))S.frameRole=(assets[0]?.role||'first_roi').split('_')[0];
  $('evidenceFrames').innerHTML=assets.map(a=>{const role=a.role.split('_')[0];return '<button class="'+(role===S.frameRole?'active':'')+'" data-role="'+role+'">'+'<img loading="lazy" src="/api/evidence/'+a.id+'" alt="过程证据">'+(roleNames[role]||(role.startsWith('context')?'过程帧 '+Number(role.slice(7)):role))+' · '+a.media_time.toFixed(1)+'s</button>';}).join('');
  $('evidenceFrames').querySelectorAll('[data-role]').forEach(b=>b.onclick=()=>{S.frameRole=b.dataset.role;drawEvidence();});
  const chosen=assets.find(a=>a.role===S.frameRole+'_'+S.view);
  window.dispatchEvent(new CustomEvent('sg:evidence-selected'));
  $('evidenceImage').hidden=!chosen;
  if(chosen){$('evidenceImage').src='/api/evidence/'+chosen.id;$('evidenceStatus').textContent='证据 #'+chosen.id+' · 媒体时间 '+chosen.media_time.toFixed(2)+' 秒 · '+chosen.sha256.slice(0,16)+'…';}
  else{$('evidenceImage').removeAttribute('src');$('evidenceStatus').textContent=e.expired?'证据已到期清理，事件与审核摘要保留':'暂时没有此类证据；事件结束后补齐峰值及结束帧';}
}
$('viewCrop').onclick=()=>{S.view='roi';drawEvidence();};$('viewOriginal').onclick=()=>{S.view='original';drawEvidence();};
$('imageZoom').oninput=e=>$('evidenceImage').style.width=e.target.value+'%';
$('freezeEvent').onclick=ev=>busy(ev.currentTarget,async()=>{if(!S.selected)return;const id=S.selected.id;const current=await api('/events/'+id);await api('/events/'+id+'/freeze',{method:'POST',body:{content_version:current.content_version}});await selectEvent(id);toast('片段已固定，后续画面会单独待审');});
$('reloadEvent').onclick=()=>{if(S.selected)selectEvent(S.selected.id).catch(e=>toast(e.message,true));};
$('reasonPreset').onchange=e=>{if(e.target.value)$('reviewNote').value=e.target.value;};
document.querySelectorAll('[data-verdict]').forEach(button=>button.onclick=()=>busy(button,async()=>{
  const e=S.selected;if(!e)throw new Error('请先选择事件');
  const labels=[...$('reviewLabels').querySelectorAll('input:checked')].map(x=>x.value);
  const result=await api('/events/'+e.id+'/review',{method:'POST',body:{state:button.dataset.verdict,labels,note:$('reviewNote').value,expected_version:e.review_version,content_version:e.content_version,duration_ms:Math.min(86400000,Math.round(performance.now()-S.selectedAt))}});
  S.draftBaseline=reviewDraft();
  await selectEvent(e.id);$('reviewStatus').textContent='已保存 · '+statusNames[button.dataset.verdict];toast('审核已保存，可撤销');if($('autoNextReview').checked){await loadQueue();const next=S.queue.find(x=>x.id!==e.id);if(next)await selectEvent(next.id);}
  await updateOverview();
},$('reviewStatus')));
$('undoReview').onclick=e=>busy(e.currentTarget,async()=>{
  const item=S.selected;if(!item?.reviews.length)return;
  await api('/events/'+item.id+'/undo',{method:'POST',body:{review_id:item.reviews[0].id,expected_version:item.review_version}});
  await selectEvent(item.id);toast('已撤销最近审核，历史记录仍保留');await updateOverview();
},$('reviewStatus'));
$('nextReview').onclick=async()=>{
  const i=S.queue.findIndex(e=>e.id===S.selected?.id),next=S.queue[i+1];
  if(next)await selectEvent(next.id);else{await loadQueue();if(S.queue.length)await selectEvent(S.queue[0].id);else toast('当前队列已完成');}
};
document.addEventListener('keydown',e=>{
  if(S.page!=='review'||!S.selected||e.ctrlKey||e.altKey||e.metaKey||['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName)||$('roiDialog').open)return;
  const states={'1':'normal','2':'confirmed','3':'uncertain'};
  if(states[e.key]){e.preventDefault();document.querySelector('[data-verdict="'+states[e.key]+'"]').click();}
  if(e.key.toLowerCase()==='j')$('nextReview').click();
});
function fillSettings(cfg){
  const form=$('settingsForm');
  for(const [key,value] of Object.entries(cfg)){
    const input=form.elements.namedItem(key);if(!input)continue;
    if(input.type==='checkbox')input.checked=value;else input.value=value;
  }
  $('settingsPolicies').innerHTML=policiesHTML(cfg.policies,'settings');
  $('settingsVersion').textContent='当前保存版本 v'+S.config.version+' · '+when(S.config.created_at);
  showDiff();
}
function readSettings(){
  const config=structuredClone(S.config.settings),form=$('settingsForm');
  for(const [key,value] of Object.entries(config)){
    const input=form.elements.namedItem(key);if(!input)continue;
    config[key]=input.type==='checkbox'?input.checked:typeof value==='number'?Number(input.value):input.value;
  }
  config.policies=readPolicies($('settingsPolicies'));return config;
}
function showDiff(){
  const draft=readSettings();const changed=Object.keys(draft).filter(k=>JSON.stringify(draft[k])!==JSON.stringify(S.config.settings[k]));
  const activeSources=Math.min(4,S.sources.filter(s=>!s.archived).length||1);const samples=draft.audit_daily_per_source*activeSources;
  $('auditEstimate').textContent=activeSources+' 路每天最多 '+samples+' 条低风险抽检，预计 '+Math.ceil(samples*draft.review_seconds_per_item/60)+' 分钟；总审核预算 '+draft.review_minutes+' 分钟。疑似风险另行留存，静态重复画面可能少于上限。';
  const labels={audit_daily_per_source:'每路每日抽检上限',review_minutes:'每日审核预算',review_seconds_per_item:'每条审核秒数',segment_seconds:'片段最长秒数',evidence_interval_seconds:'证据保存间隔',review_time:'每日复核时间',timezone:'时区',retention_days:'疑似证据保留天数',quota_gib:'证据容量 GiB',min_free_gib:'最低剩余空间 GiB',sample_seconds:'采样间隔',merge_gap_seconds:'事件合并间隔',confirm_samples:'连续确认帧数',confirm_seconds:'连续确认秒数',cooldown_seconds:'告警冷却',audit_sample_rate:'抽检比例',auto_alerts:'自动告警',model_id:'运行模型'};
  const rows=[];
  for(const k of changed){
    if(k==='policies'){for(const label of Object.keys(S.labels)){const before=S.config.settings.policies[label],after=draft.policies[label];if(JSON.stringify(before)!==JSON.stringify(after))rows.push(S.labels[label]+'：复核 '+before.review+' → '+after.review+'；告警 '+before.alert+' → '+after.alert+'；启用 '+before.enabled+' → '+after.enabled);}}
    else rows.push((labels[k]||k)+'：'+S.config.settings[k]+' → '+draft[k]);
  }
  $('settingsDiff').textContent=rows.length?'适用于全部视频源。'+rows.join('；'):'尚未修改';
}
async function loadSettings(){
  S.config=await api('/settings');S.history=await api('/settings/history');modelSelects();fillSettings(S.config.settings);
  populateSelect($('configHistory'),S.history.map(x=>({value:String(x.version),label:'v'+x.version+' · '+when(x.created_at)})),'');
  if(!$('candidatePolicies').children.length){$('candidatePolicies').innerHTML=policiesHTML(S.config.settings.policies,'candidate');}
}
$('settingsForm').oninput=showDiff;
$('settingsForm').onsubmit=e=>{e.preventDefault();const b=e.submitter||$('settingsForm').querySelector('button[type=submit]');busy(b,async()=>{
  const result=await api('/settings',{method:'POST',body:{expected_version:S.config.version,settings:readSettings()}});
  await loadSettings();$('settingsStatus').textContent='已保存 v'+result.version+'。'+result.effective;toast('设置已保存');await updateOverview();
},$('settingsStatus'));};
$('resetSettings').onclick=()=>loadSettings().catch(e=>toast(e.message,true));
$('restoreSettings').onclick=()=>{const item=S.history.find(x=>String(x.version)===$('configHistory').value);if(item){fillSettings(item.settings);$('settingsStatus').textContent='历史配置已载入草稿，尚未保存。模型或阈值变化仍需对应离线对照。';}};
async function loadModels(){
  S.models=await api('/models');modelSelects();
  $('modelList').innerHTML=S.models.map(m=>'<div class="model-card"><strong>'+esc(m.model_id)+'</strong> '+pill(m.validation_status)+'<p>'+esc(m.description||'')+'</p><code>'+esc(m.preprocessing||'')+'</code></div>').join('');
}
$('modelImport').onsubmit=e=>{e.preventDefault();const form=e.currentTarget;busy(form.querySelector('button'),async()=>{
  const result=await api('/models/import',{method:'POST',body:new FormData(form)});
  form.querySelector('.inline-status').textContent=result.message;await loadModels();
},form.querySelector('.inline-status'));};
$('exportReviews').onclick=e=>busy(e.currentTarget,async()=>{const data=await api('/review-export');$('exportResult').hidden=false;$('exportResult').textContent=JSON.stringify(data,null,2);});
$('compareForm').onsubmit=e=>{e.preventDefault();const form=e.currentTarget;busy(form.querySelector('button[type=submit]'),async()=>{
  const candidate=structuredClone(S.config.settings);candidate.model_id=$('candidateModel').value;candidate.policies=readPolicies($('candidatePolicies'));
  S.candidate=candidate;
  const result=await api('/comparisons',{method:'POST',body:{candidate_model:candidate.model_id,candidate_settings:candidate}});
  S.compareId=result.id;S.compareStatus='';form.querySelector('.inline-status').textContent='对照已提交；运行配置没有改变';await loadComparisons();
},form.querySelector('.inline-status'));};
$('adoptCandidate').onclick=async()=>{
  if(!S.candidate){toast('先执行一次候选对照，再带入设置草稿',true);return;}
  await loadSettings();const draft=structuredClone(S.config.settings);
  draft.model_id=S.candidate.model_id;draft.policies=structuredClone(S.candidate.policies);
  fillSettings(draft);await navigate('settings');toast('候选已带入草稿，请检查并保存');
};
async function loadComparisons(){
  const list=await api('/comparisons');
  $('comparisonList').innerHTML=list.map(r=>'<div class="comparison-entry"><button class="secondary" data-job="'+esc(r.id)+'">'+when(r.created_at)+'</button><span>'+esc(({queued:'等待中',running:'运行中',completed:'已完成',error:'失败'})[r.status])+'</span><small>'+esc(r.error||'')+'</small></div>').join('');
  $('comparisonList').querySelectorAll('[data-job]').forEach(b=>b.onclick=()=>{S.compareId=b.dataset.job;S.compareStatus='';loadComparisonReport().catch(e=>toast(e.message,true));});
  if(S.compareId)await loadComparisonReport();
}
async function loadComparisonReport(){
  const run=await api('/comparisons/'+S.compareId);
  if(S.compareStatus===run.id+run.status)return;S.compareStatus=run.id+run.status;
  S.candidate=run.spec.candidate_settings;S.candidate.model_id=run.spec.candidate_model;
  if(run.status!=='completed'){$('comparisonReport').textContent=run.status==='error'?run.error:'对照正在运行…';return;}
  const r=run.report;
  let html='<h3>对照结果</h3><p class="muted">'+esc(r.scope)+'</p><p>冻结样本 '+r.total+' · 两侧共同成功 '+r.paired_success+' · 用时 '+r.elapsed_seconds+' 秒</p><table><tr><th></th><th>基线</th><th>候选</th></tr>';
  for(const kind of ['normal','review','alert','unknown'])html+='<tr><td>'+esc(({normal:'低于复核阈值',alert:'达到高阈值（模拟）'})[kind]||statusNames[kind]||kind)+'</td><td>'+r.baseline.counts[kind]+'</td><td>'+r.candidate.counts[kind]+'</td></tr>';
  html+='</table><p class="muted">以上是候选阈值模拟数量，未应用业务准入和连续确认，不会发出实际告警。</p><table>';
  html+='<tr><td>失败（不计作正常）</td><td>'+r.baseline.errors+'</td><td>'+r.candidate.errors+'</td></tr></table>';
  const n=r.candidate.metrics_on_common_labeled.labeled;
  html+='<p>共同成功且有人工作答：'+n+' 条。仅代表事件首帧候选判断。</p>';
  if(n){html+='<table><tr><th>类别</th><th>基线 P / R</th><th>候选 P / R</th></tr>';
    const fmt=x=>x===null?'无分母':(x*100).toFixed(1)+'%';
    for(const k of Object.keys(S.labels)){const a=r.baseline.metrics_on_common_labeled.per_label[k],b=r.candidate.metrics_on_common_labeled.per_label[k];html+='<tr><td>'+esc(S.labels[k])+'</td><td>'+fmt(a.precision)+' / '+fmt(a.recall)+'</td><td>'+fmt(b.precision)+' / '+fmt(b.recall)+'</td></tr>';}html+='</table>';}
  html+='<details><summary>逐事件结果及固定样本标识</summary><pre>'+esc(JSON.stringify({sample_set_hash:r.sample_set_hash,rows:r.rows},null,2))+'</pre></details>';
  $('comparisonReport').innerHTML=html;
}
let R={source:null,version:0,regions:[],index:0,points:[],image:null,history:[],previewURL:null,start:null};
async function openRoi(id){
  const data=await api('/sources/'+id+'/roi');R={...R,source:id,version:data.version,regions:structuredClone(data.regions),index:0,history:data.history,start:null,image:null};
  $('roiZoom').value=100;$('roiCanvas').style.width='100%';
  $('roiTitle').textContent=(S.sources.find(x=>x.id===id)?.name||'视频源')+' · 当前区域版本 v'+R.version;
  $('roiStatus').textContent='';$('roiDialog').showModal();
  populateSelect($('roiHistory'),R.history.map(x=>({value:String(x.id),label:'v'+x.id+' · '+when(x.created_at)})),'');
  const blob=await api('/sources/'+id+'/frame',{blob:true});const url=URL.createObjectURL(blob),img=new Image();
  img.onload=()=>{R.image=img;const canvas=$('roiCanvas');canvas.width=Math.min(1000,img.naturalWidth);canvas.height=Math.round(img.naturalHeight*canvas.width/img.naturalWidth);selectRegion(0);URL.revokeObjectURL(url);};
  img.onerror=()=>{URL.revokeObjectURL(url);$('roiStatus').textContent='画面加载失败';};img.src=url;
}
function stashRegion(){if(R.regions[R.index]){R.regions[R.index].points=structuredClone(R.points);R.regions[R.index].name=$('regionName').value;}}
function selectRegion(index){
  R.index=index;R.points=structuredClone(R.regions[index].points);$('regionName').value=R.regions[index].name;
  populateSelect($('regionSelect'),R.regions.map((r,i)=>({value:String(i),label:r.name})),String(index));drawRoi();
}
function drawRoi(){
  if(!R.image)return;
  const c=$('roiCanvas'),ctx=c.getContext('2d');ctx.clearRect(0,0,c.width,c.height);ctx.drawImage(R.image,0,0,c.width,c.height);
  ctx.strokeStyle='#34e7bf';ctx.fillStyle='#17bd9630';ctx.lineWidth=2;
  if(R.points.length){ctx.beginPath();R.points.forEach((p,i)=>{if(i)ctx.lineTo(p[0]*c.width,p[1]*c.height);else ctx.moveTo(p[0]*c.width,p[1]*c.height);});if(R.points.length===4){ctx.closePath();ctx.fill();}ctx.stroke();}
  R.points.forEach((p,i)=>{ctx.beginPath();ctx.fillStyle='#16b88f';ctx.arc(p[0]*c.width,p[1]*c.height,7,0,Math.PI*2);ctx.fill();ctx.fillStyle='white';ctx.font='14px sans-serif';ctx.fillText(String(i+1),p[0]*c.width+9,p[1]*c.height+14);});
  $('roiStatus').textContent='已选择 '+R.points.length+' / 4 点；修改尚未保存';
}
function canvasPoint(e){const b=$('roiCanvas').getBoundingClientRect();return [Math.min(1,Math.max(0,(e.clientX-b.left)/b.width)),Math.min(1,Math.max(0,(e.clientY-b.top)/b.height))];}
$('roiCanvas').onpointerdown=e=>{
  if(!R.image)return;
  const p=canvasPoint(e);
  if($('roiMode').value==='rect'){R.start=p;e.target.setPointerCapture(e.pointerId);}
  else{if(R.points.length===4)R.points=[];R.points.push(p);drawRoi();}
};
$('roiCanvas').onpointerup=e=>{
  if(!R.start)return;
  const p=canvasPoint(e),a=R.start;R.start=null;
  const left=Math.min(p[0],a[0]),right=Math.max(p[0],a[0]),top=Math.min(p[1],a[1]),bottom=Math.max(p[1],a[1]);
  R.points=[[left,top],[right,top],[right,bottom],[left,bottom]];drawRoi();
};
$('regionSelect').onchange=e=>{stashRegion();selectRegion(Number(e.target.value));};
$('resetPoints').onclick=()=>{R.points=[];drawRoi();};
$('roiZoom').oninput=e=>$('roiCanvas').style.width=e.target.value+'%';
$('addRegion').onclick=()=>{if(R.regions.length>=4){toast('每路最多四个屏幕',true);return;}stashRegion();R.regions.push({id:'screen-'+Date.now().toString(36),name:'屏幕 '+(R.regions.length+1),points:[]});selectRegion(R.regions.length-1);};
$('removeRegion').onclick=()=>{if(R.regions.length<=1){toast('至少保留一个屏幕区域',true);return;}R.regions.splice(R.index,1);selectRegion(0);};
$('previewRoi').onclick=e=>busy(e.currentTarget,async()=>{
  const blob=await api('/sources/'+R.source+'/roi/preview',{method:'POST',body:{points:R.points},blob:true});
  if(R.previewURL)URL.revokeObjectURL(R.previewURL);R.previewURL=URL.createObjectURL(blob);$('roiPreview').src=R.previewURL;$('roiStatus').textContent='校正预览已更新；尚未保存';
},$('roiStatus'));
$('saveRoi').onclick=e=>busy(e.currentTarget,async()=>{
  stashRegion();const result=await api('/sources/'+R.source+'/roi',{method:'POST',body:{expected_version:R.version,regions:R.regions}});
  R.version=result.version;$('roiStatus').textContent='已保存 v'+result.version+'；'+result.effective;await updateOverview();toast('屏幕区域已保存');
},$('roiStatus'));
$('restoreRoi').onclick=()=>{const item=R.history.find(x=>String(x.id)===$('roiHistory').value);if(item){R.regions=JSON.parse(item.body);selectRegion(0);$('roiStatus').textContent='历史区域已载入草稿，点击保存后生效';}};
$('closeRoi').onclick=()=>{$('roiDialog').close();if(R.previewURL){URL.revokeObjectURL(R.previewURL);R.previewURL=null;}$('roiPreview').removeAttribute('src');};
window.addEventListener('unhandledrejection',e=>{toast(e.reason?.message||'操作失败',true);});
async function tick(){
  try{await updateOverview();if(S.page==='models'&&S.compareId)await loadComparisonReport();}
  catch{}
  setTimeout(tick,document.hidden?10000:3000);
}
async function init(){
  const data=await api('/bootstrap');S.csrf=data.csrf;S.labels=data.labels;
  S.role=data.role;S.mode=data.mode;
  for(const id of ['eventLabel','reviewLabel'])populateSelect($(id),Object.entries(S.labels).map(([k,v])=>({value:k,label:v})),'',true);
  $('reviewLabels').innerHTML='<legend>风险类别（可多选）</legend>'+Object.entries(S.labels).map(([k,v])=>'<label><input type="checkbox" value="'+k+'">'+esc(v)+'</label>').join('');
  await loadModels();await loadSettings();
  const {initModelInterfaces}=await import('./model-interfaces.js?v=20261011-1');
  await initModelInterfaces({api,esc,toast,getSelected:()=>S.selected,getEvidence:()=>S.selected?.evidence.find(a=>a.role===S.frameRole+'_'+S.view),labels:()=>S.labels});
  const {initOperations}=await import('./operations.js');
  await initOperations({api,esc,toast,session:data,navigate,selectEvent,getSelected:()=>S.selected,leaveDraft});
  const {initRuntime}=await import('./runtime-ui.js');
  await initRuntime({api,esc,toast,S,when,openRoi,selectEvent,navigate,leaveDraft});
  if(S.role==='reviewer'){document.querySelectorAll('[data-page="models"],[data-page="settings"]').forEach(b=>b.hidden=true);$('showSource').hidden=true;$('sourceForm').hidden=true;}
  await navigate(names[location.hash.slice(1)]?location.hash.slice(1):'monitor');await tick();
}
init().catch(e=>toast('初始化失败：'+e.message,true));

