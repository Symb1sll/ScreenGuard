export async function initOperations({api,esc,toast,session,navigate,selectEvent,getSelected,leaveDraft}){
  const $=id=>document.getElementById(id),admin=session.role==='admin';
  const when=t=>t?new Date(t*1000).toLocaleString('zh-CN'):'—',size=n=>(n/1024**3).toFixed(2)+' GiB';
  const nav=document.createElement('button');nav.dataset.page='operations';nav.textContent='▣　运行与通知';nav.onclick=()=>navigate('operations');document.querySelector('nav').append(nav);
  const section=document.createElement('section');section.id='page-operations';section.className='page';section.hidden=true;
  section.innerHTML=`
  <div class="sectionhead"><div><h2>运行与通知</h2><p>签收表示已看到通知，事件结论仍需在人工复核中提交。</p></div><button id="opsRefresh" class="secondary">刷新</button></div>
  <article class="panel"><h3>审核负荷与存储</h3><p id="opsWorkload"></p><p id="opsStorage"></p><p id="opsErrors" class="error"></p><a href="/api/operations/report.csv" download>下载事件与审核摘要 CSV（最近一万条）</a></article>
  <article class="panel"><div class="sectionhead"><h3>站内通知</h3><label class="check"><input id="onlyUnacked" type="checkbox">只看未签收</label></div><div id="notificationList"></div><div class="row"><button id="notifyPrev" class="secondary">上一页</button><span id="notifyPage"></span><button id="notifyNext" class="secondary">下一页</button></div></article>
  ${admin?`<div class="split"><form id="webhookForm" class="panel"><h3>通用 Webhook</h3><p>仅发送通知摘要和事件编号。每条通知最多自动尝试三次。</p><label class="check"><input name="enabled" type="checkbox">启用外部投递</label><label>HTTPS 地址（留空保留）<input name="url" type="password" autocomplete="off" maxlength="4096"></label><label>地址环境变量<input name="key_env" value="SCREEN_GUARD_WEBHOOK_URL" required></label><label class="check"><input name="clear" type="checkbox">清除已保存地址</label><p id="webhookHint"></p><button>保存渠道</button><p class="inline-status"></p></form>
  <article class="panel"><h3>备份与恢复</h3><p>包含配置、账号、事件、证据、上传视频和已导入模型。先固定数据库和文件引用，再打包；最多保留三份，下载后可移除旧备份。</p><p>恢复在服务器终端进行，校验后写入新目录。恢复后摄像头保持停止。</p><button id="createBackup">创建完整备份</button><p id="backupStatus" role="status"></p><div id="backupList"></div></article></div>
  <article class="panel"><h3>视频源管理</h3><p>停止采集后可修改名称、地点和流地址；归档保留历史事件。</p><div id="sourceAdmin"></div></article>
  <article class="panel"><h3>账号</h3><p>当前为 ${session.mode==='enterprise'?'企业账号模式':'本机模式；创建账号后需使用企业启动入口启用登录'}。</p><div id="userList"></div><form id="newUser"><div class="filters"><label>账号<input name="username" minlength="3" maxlength="64" required autocomplete="off"></label><label>密码（至少 12 字符）<input name="password" type="password" minlength="12" maxlength="256" required autocomplete="new-password"></label><label>角色<select name="role"><option value="reviewer">审核员</option><option value="admin">管理员</option></select></label><button>创建账号</button></div></form></article>
  <article class="panel"><h3>最近操作记录</h3><div id="auditList"></div></article>` : ''}
  `;
  document.querySelector('main').append(section);
  let page=1,webhookVersion=0,inflight=false;
  async function action(button,fn){button.disabled=true;try{await fn();}catch(e){toast(e.message,true);}finally{button.disabled=false;}}
  async function loadNotices(){
    const data=await api('/operations/notifications?page='+page+'&unacked='+$('onlyUnacked').checked);
    const states={disabled:'渠道未启用',pending:'待投递',sending:'发送中',delivered:'已送达',failed:'投递失败'};
    $('notificationList').innerHTML=data.items.map(n=>'<div class="notification-row"><div><strong>'+esc(n.title)+'</strong><p>'+when(n.created_at)+' · '+esc(states[n.delivery])+' · 尝试 '+n.attempts+' 次</p><p>'+esc(n.error)+(n.acked_at?' · '+esc(n.acked_by)+' 已签收 '+when(n.acked_at):'')+'</p></div><div class="row">'+(n.event_id?'<button data-open="'+n.event_id+'" class="secondary">查看事件</button>':'')+(!n.acked_at?'<button data-ack="'+n.id+'">签收</button>':'')+(admin&&['failed','disabled'].includes(n.delivery)?'<button data-retry="'+n.id+'" class="secondary">重新投递</button>':'')+'</div></div>').join('')||'<p>暂无通知</p>';
    $('notifyPage').textContent=page+' 页 · '+data.total+' 条';$('notifyPrev').disabled=page<=1;$('notifyNext').disabled=page*30>=data.total;
    $('notificationList').querySelectorAll('[data-ack],[data-retry]').forEach(b=>b.onclick=()=>action(b,async()=>{
      if(b.dataset.retry&&!confirm('将这条通知投递到当前配置的 Webhook 地址？'))return;
      const id=b.dataset.ack||b.dataset.retry;await api('/operations/notifications/'+id+'/'+(b.dataset.ack?'ack':'retry'),{method:'POST'});await loadNotices();
    }));
    $('notificationList').querySelectorAll('[data-open]').forEach(b=>b.onclick=async()=>{await navigate('review');await selectEvent(Number(b.dataset.open));});
  }
  async function loadBackups(){
    const rows=await api('/operations/backups');$('backupList').innerHTML=rows.map(r=>'<p><a href="/api/operations/backups/'+r.name+'" download>'+esc(r.name)+'</a> · '+size(r.bytes)+' <button class="secondary" data-remove="'+r.name+'">移除</button></p>').join('')||'<p>尚无运行备份</p>';
    $('backupList').querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>action(b,async()=>{if(!confirm('确认移除此备份？请先确认已下载保存。'))return;await api('/operations/backups/'+b.dataset.remove,{method:'DELETE'});await loadBackups();}));
  }
  async function loadSources(){
    const rows=await api('/operations/sources');
    $('sourceAdmin').innerHTML=rows.map(s=>'<form class="source-edit" data-id="'+s.id+'"><h4>#'+s.id+' '+esc(s.name)+(s.archived?'（已归档）':'')+'</h4><div class="filters"><label>名称<input name="name" maxlength="80" value="'+esc(s.name)+'" required></label><label>地点<input name="location" maxlength="200" value="'+esc(s.location)+'"></label>'+(s.source_type==='rtsp'?'<label>新地址（空白保留）<input name="url" type="password" autocomplete="off"></label>':'')+'<label class="check"><input name="archived" type="checkbox" '+(s.archived?'checked':'')+'>归档</label><button '+(['running','starting','stopping','reconnecting','scheduled'].includes(s.status)?'disabled':'')+'>保存</button></div></form>').join('');
    $('sourceAdmin').querySelectorAll('form').forEach(f=>f.onsubmit=e=>{e.preventDefault();action(f.querySelector('button'),async()=>{await api('/operations/sources/'+f.dataset.id,{method:'PUT',body:{name:f.elements.name.value,location:f.elements.location.value,archived:f.elements.archived.checked,url:f.elements.url?.value||null}});toast('视频源已保存');await loadSources();});});
  }
  async function loadUsers(){
    const rows=await api('/auth/users');
    $('userList').innerHTML=rows.map(u=>'<p>'+esc(u.username)+' · '+(u.role==='admin'?'管理员':'审核员')+' · '+(u.enabled?'启用':'停用')+' <button class="secondary" data-user="'+esc(u.username)+'" data-enable="'+(!u.enabled)+'">'+(u.enabled?'停用':'启用')+'</button></p>').join('')||'<p>尚未创建账号</p>';
    $('userList').querySelectorAll('button').forEach(b=>b.onclick=()=>action(b,async()=>{if(!confirm('确认'+b.textContent+'账号 '+b.dataset.user+'？停用会注销其会话。'))return;await api('/auth/users/'+encodeURIComponent(b.dataset.user),{method:'PUT',body:{enabled:b.dataset.enable==='true'}});await loadUsers();}));
  }
  async function load(){
    if(inflight)return;inflight=true;
    try{
      const s=await api('/operations/status');
      $('opsWorkload').textContent='待审 '+s.pending+' 条，预计 '+s.estimated_minutes+' 分钟 / 每日预算 '+s.budget_minutes+' 分钟'+(s.over_budget?'；已超过预算，请优先处理告警和临期片段。':'。')+' 未签收通知 '+s.unacknowledged+' 条。';
      $('opsStorage').textContent=Object.entries(s.storage.categories).map(([k,v])=>({evidence:'证据',uploads:'视频',models:'模型',backups:'备份',database:'数据库'}[k]||k)+' '+size(v)).join(' · ')+' · 磁盘可用 '+size(s.evidence.free_bytes)+(s.storage.stale?' · 容量统计待刷新':' · 统计于 '+when(s.storage.checked_at));
      $('opsErrors').textContent=s.evidence.error+(s.delivery_failures?'；失败投递 '+s.delivery_failures+' 条':'');
      nav.textContent='▣　运行与通知'+(s.unacknowledged?' ('+s.unacknowledged+')':'');
      await loadNotices();
      if(admin){await loadBackups();await loadSources();await loadUsers();const logs=await api('/operations/audit');$('auditList').innerHTML=logs.map(r=>'<p>'+when(r.created_at)+' · '+esc(r.actor)+' · '+esc(r.action)+' '+esc(r.target)+' · '+r.status+'</p>').join('');}
    }finally{inflight=false;}
  }
  $('opsRefresh').onclick=e=>action(e.currentTarget,load);
  $('onlyUnacked').onchange=()=>{page=1;loadNotices().catch(e=>toast(e.message,true));};
  $('notifyPrev').onclick=()=>{page--;loadNotices();};$('notifyNext').onclick=()=>{page++;loadNotices();};
  if(admin){
    const cfg=await api('/operations/webhook');webhookVersion=cfg.version;
    const f=$('webhookForm');f.elements.enabled.checked=cfg.enabled;f.elements.key_env.value=cfg.key_env;$('webhookHint').textContent=cfg.has_saved_url?'已保存加密地址':cfg.has_env_url?'已从环境变量读取地址':'尚未配置地址，当前只有站内通知';
    f.onsubmit=e=>{e.preventDefault();action(f.querySelector('button'),async()=>{const result=await api('/operations/webhook',{method:'PUT',body:{expected_version:webhookVersion,enabled:f.elements.enabled.checked,url:f.elements.url.value||null,key_env:f.elements.key_env.value,clear:f.elements.clear.checked}});webhookVersion=result.version;f.elements.url.value='';f.elements.clear.checked=false;$('webhookHint').textContent=result.enabled?'渠道已启用':'渠道已停用';toast('渠道已保存');});};
    $('createBackup').onclick=e=>action(e.currentTarget,async()=>{$('backupStatus').textContent='正在创建完整备份…';try{const r=await api('/operations/backups',{method:'POST'});$('backupStatus').textContent='备份完成：'+r.files+' 个文件';await loadBackups();}catch(e){$('backupStatus').textContent=e.message;throw e;}});
    $('newUser').onsubmit=e=>{e.preventDefault();const f=e.currentTarget;action(f.querySelector('button'),async()=>{await api('/auth/users',{method:'POST',body:Object.fromEntries(new FormData(f))});f.reset();await loadUsers();toast('账号已创建');});};
    const hold=document.createElement('button');hold.className='secondary';hold.textContent='证据保留 / 解除';hold.onclick=()=>action(hold,async()=>{const e=getSelected();if(!e)throw new Error('请先选择事件');const reason=prompt((e.locked?'解除':'设置')+'该事件的证据保留，请填写原因');if(!reason)return;await api('/events/'+e.id+'/hold',{method:'POST',body:{locked:!e.locked,reason}});await selectEvent(e.id);toast('证据保留状态已更新');});document.querySelector('.verdict').append(hold);
  }
  const foot=document.querySelector('.sidebar-foot');foot.querySelector('small').textContent=session.scope+' · '+session.operator;
  if(session.mode==='enterprise'){
    const logout=document.createElement('button');logout.className='secondary';logout.textContent='退出登录';logout.onclick=async()=>{if(!leaveDraft())return;await api('/auth/logout',{method:'POST'});location.replace('/login');};document.querySelector('.header-actions').append(logout);
  }
  window.addEventListener('sg:navigate',e=>{if(e.detail==='operations')load().catch(e=>toast(e.message,true));});
  // Badge updates never refresh or discard an unfinished form.
  async function refreshBadge(){
    try{if(!document.hidden){const s=await api('/operations/badge');nav.textContent='▣　运行与通知'+(s.unacknowledged?' ('+s.unacknowledged+')':'');}}catch{}
    setTimeout(refreshBadge,15000);
  }
  refreshBadge();
}
