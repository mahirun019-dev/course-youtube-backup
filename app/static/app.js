let token='', refreshing=false, latest=[], noticeTimer, syncedToken='';
const visibilityPending=new Set();
const $=id=>document.getElementById(id);
function notice(message){
  $('notice').textContent=message;$('notice').hidden=false;
  clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>{$('notice').hidden=true},12000);
}
async function api(path,body){
  const r=await fetch(path,{method:body===undefined?'GET':'POST',
    headers:body===undefined?{}:{'Content-Type':'application/json','X-Local-Token':token},
    body:body===undefined?undefined:JSON.stringify(body)});
  const data=await r.json();
  if(!r.ok)throw Error(data.message||(data.detail?.map?data.detail.map(x=>x.msg).join('；'):'请求失败'));
  return data;
}
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n}
function action(row,label,handler,cls){
  const b=el('button',label,cls);b.type='button';
  b.onclick=async()=>{b.disabled=true;try{await handler()}catch(e){notice(e.message)}finally{await refresh();b.disabled=false}};
  row.append(b);return b;
}
function link(row,label,url){
  const a=el('a',label,'button');a.href=url;
  if(url.startsWith('https://')){a.target='_blank';a.rel='noopener noreferrer'}else a.download='';row.append(a);
}
async function copyText(text){
  try{await navigator.clipboard.writeText(text)}catch(e){
    const area=el('textarea');area.value=text;document.body.append(area);area.select();
    const ok=document.execCommand('copy');area.remove();if(!ok)throw Error('无法写入剪贴板，请手动复制。');
  }
}
function confirmDialog({title,message,options,titleConfirm}){
  return new Promise(resolve=>{
    const dialog=$('confirm-dialog');let result=null;
    $('dialog-title').textContent=title;$('dialog-message').textContent=message;
    $('dialog-input').replaceChildren();$('dialog-buttons').replaceChildren();
    let input;
    if(titleConfirm!==undefined){
      const label=el('label','输入完整视频标题以确认');input=el('input');input.autocomplete='off';
      input.setAttribute('aria-label','输入完整视频标题以确认');label.append(input);$('dialog-input').append(label);
    }
    const cancel=el('button','取消','secondary');cancel.type='button';cancel.onclick=()=>dialog.close();$('dialog-buttons').append(cancel);
    for(const option of options){
      const b=el('button',option.label,option.danger?'danger':'');b.type='button';
      if(input){b.disabled=true;input.addEventListener('input',()=>{b.disabled=input.value!==titleConfirm})}
      b.onclick=()=>{result=option.value;dialog.close()};$('dialog-buttons').append(b);
    }
    dialog.addEventListener('close',()=>resolve(result),{once:true});dialog.showModal();
    if(input)input.focus();else cancel.focus();
  });
}
function visibilityText(j){
  if(visibilityPending.has(j.id))return '状态核对中';
  if(j.remote_deleted)return 'YouTube 已删除';
  if(j.remote_missing)return '远端未找到';
  if(!j.video_id)return '本地记录';
  if(j.visibility_uncertain||!j.visibility_checked_at)return j.visibility==='public'?'状态待确认 · 上次公开':'可见性待确认';
  return {private:'🔒 非公開',public:'公开',unlisted:'限定公開',unavailable:'远端未找到'}[j.visibility]||'可见性待确认';
}
async function changePrivacy(j,target){
  if(target==='public'){
    const confirmed=await confirmDialog({title:'临时设为公开？',
      message:`视频「${j.title}」将变为 Public，任何人都可能访问、传播或下载。它会保持公开，直到你主动恢复为非公开。请确认你有公开视频的许可。`,
      options:[{label:'确认设为公开',value:'public',danger:true}]});
    if(!confirmed)return;
  }
  visibilityPending.add(j.id);render(latest);
  try{notice((await api('/api/jobs/'+j.id+'/privacy',{target,confirmed:true})).message)}
  finally{
    try{latest=await api('/api/jobs');visibilityPending.delete(j.id);render(latest)}
    catch(e){notice('无法读取当前可见性，请核对 YouTube Studio；页面不会假定它是非公开。');throw e}
  }
}
async function deleteJob(j){
  const remote=!!j.can_delete_remote;
  const choice=await confirmDialog({title:remote?'删除这条备份？':'删除这条本地记录？',
    message:remote?'仅删除本地记录：删除历史和本地字幕/临时数据，YouTube 视频保持不变。\n\n删除 YouTube 视频和本地记录：将永久删除该视频，需要再次确认。':'这只会删除本机保存的历史记录和相关本地字幕/临时数据。',
    options:remote?[{label:'仅删除本地记录',value:'local'},{label:'删除 YouTube 视频和本地记录',value:'remote',danger:true}]:[{label:'删除记录',value:'local'}]});
  if(!choice)return;
  if(choice==='remote'){
    const confirmed=await confirmDialog({title:'永久删除 YouTube 视频？',
      message:`视频标题：${j.title}\n此操作不可恢复。只有 YouTube API 确认删除成功后，才会删除本地记录和文件。`,
      titleConfirm:j.title,options:[{label:'永久删除视频和记录',value:'confirmed',danger:true}]});
    if(!confirmed)return;
  }
  notice((await api('/api/jobs/'+j.id+'/delete',{mode:choice,confirmed:true,confirmation_title:choice==='remote'?j.title:''})).message);
}
function render(jobs){
  $('count').textContent=jobs.length+' 个视频';
  if(!jobs.length){const empty=el('div',undefined,'empty');empty.append(el('div','还没有备份记录。'),el('span','从你的第一节课程开始。'));$('jobs').replaceChildren(empty);return}
  const fragment=document.createDocumentFragment();
  for(const j of jobs){
    const box=el('article',undefined,'job '+j.state);box.dataset.jobId=j.id;
    const active=['queued','downloading','uploading'].includes(j.state);
    const head=el('div',undefined,'job-head');
    head.append(el('h3',j.title),el('span',visibilityText(j),'badge '+(j.visibility==='public'?'public-badge':'')),el('span',new Date(j.created_at).toLocaleString('zh-CN'),'date'));
    const del=action(head,'删除',()=>deleteJob(j),'text-button delete-button');del.disabled=active;
    box.append(head);
    if(j.source_title)box.append(el('p','原视频标题：'+j.source_title,'source'));
    box.append(el('p',j.message||'等待处理……','message'));
    if(active){const row=el('div',undefined,'progress-row'),p=el('progress');p.max=100;p.value=j.progress;row.append(p,el('span',Math.floor(j.progress)+'%'));box.append(row)}
    const row=el('div',undefined,'actions');
    if(j.video_id&&j.state==='uploaded'&&!j.remote_deleted){
      box.append(el('p','✓ YouTube 上传完成','caption'));
      link(row,'打开 YouTube','https://www.youtube.com/watch?v='+encodeURIComponent(j.video_id));
      link(row,'YouTube Studio','https://studio.youtube.com/video/'+encodeURIComponent(j.video_id)+'/translations');
      if(['ready','saved'].includes(j.caption_state))box.append(el('p','✓ 自动字幕已生成 · '+(j.language.trim().toLowerCase().split('-')[0]==='ja'?'日本語':(j.language||'语言未提供')),'caption'));
      action(row,'检查字幕',async()=>notice((await api('/api/jobs/'+j.id+'/captions/check',{})).message));
      const confirmedPublic=j.visibility==='public'&&!j.visibility_uncertain&&!!j.visibility_checked_at&&!visibilityPending.has(j.id);
      if(j.can_use_gemini||j.visibility==='public'){
        const gemini=el('section',undefined,'gemini '+(j.visibility==='public'?'is-public':''));gemini.append(el('h4','Gemini 使用'));
        gemini.append(el('p','当前：'+visibilityText(j),'visibility-status'));
        if(j.visibility_checked_at)gemini.append(el('p','API 确认时间：'+new Date(j.visibility_checked_at).toLocaleString('zh-CN'),'hint'));
        const actions=el('div',undefined,'actions');
        if(confirmedPublic){
          gemini.append(el('p','公开视频可被任何人访问。使用结束后请主动恢复为非公开。','public-warning'));
          action(actions,'复制 Gemini 用链接',async()=>{await copyText('https://youtu.be/'+j.video_id);notice('Gemini 用链接已复制')},'primary-action');
          action(actions,'恢复为非公开',()=>changePrivacy(j,'private'));
        }else if(j.visibility_uncertain||!j.visibility_checked_at||visibilityPending.has(j.id)){
          action(actions,'核对当前可见性',async()=>notice((await api('/api/visibility/sync',{})).message));
          action(actions,'恢复为非公开',()=>changePrivacy(j,'private'));
        }else if(j.can_use_gemini){action(actions,'临时设为公开',()=>changePrivacy(j,'public'),'primary-action')}
        gemini.append(actions);box.append(gemini);
      }
      if(j.has_temp)action(row,'清理临时文件',()=>api('/api/jobs/'+j.id+'/cleanup',{}));
    }else if(j.state==='failed'&&!j.remote_deleted){
      action(row,j.session_expired?'会话过期 · 重新上传':'重试备份 / 上传',async()=>{
        if(j.session_expired&&!confirm('上传会话已过期。请先确认 YouTube Studio 没有重复视频。重新上传将从头开始，是否继续？'))return;
        await api('/api/jobs/'+j.id+'/retry',{restart_expired:!!j.session_expired});
      });
    }
    box.append(row);
    if(j.has_subtitles||j.caption_state==='ready'){
      const details=el('details',undefined,'subtitle-tools');details.append(el('summary','字幕文件（辅助功能）'));
      const subs=el('div',undefined,'actions');
      if(j.caption_state==='ready')action(subs,'获取字幕',async()=>notice((await api('/api/jobs/'+j.id+'/captions/fetch',{})).message));
      if(j.has_subtitles){
        action(subs,'复制字幕',async()=>{const r=await fetch('/api/jobs/'+j.id+'/subtitle/txt');if(!r.ok)throw Error('字幕文件读取失败');await copyText(await r.text());notice('字幕已复制到剪贴板')});
        link(subs,'下载 TXT','/api/jobs/'+j.id+'/subtitle/txt');link(subs,'下载 SRT','/api/jobs/'+j.id+'/subtitle/srt');
      }
      details.append(subs);box.append(details);
    }
    fragment.append(box);
  }
  $('jobs').replaceChildren(fragment);
}
function renderAuth(auth){
  const controls=$('oauth-controls'),link=$('oauth-open');let url;try{url=new URL(auth.authorization_url)}catch(e){}
  const ready=auth.busy&&url&&url.protocol==='https:'&&url.host==='accounts.google.com';controls.hidden=!ready;
  if(ready)link.href=url.href;else link.removeAttribute('href');
}
$('oauth-copy').onclick=async()=>{try{await copyText($('oauth-open').href);notice('授权链接已复制，请在浏览器中打开')}catch(e){notice('无法复制链接。请右键「打开 Google 授权页面」复制链接地址。')}};
async function refresh(){
  if(refreshing)return;refreshing=true;
  try{
    const s=await api('/api/status');token=s.token;renderAuth(s.auth);
    $('connection').textContent=s.auth.busy?'正在授权……':s.connected?'YouTube 已连接（本机保存授权）':'首次使用 · 连接自己的 YouTube';
    let text=s.auth.message||(!s.credential_present?'先将 Desktop App OAuth 文件放入 data/config/client_secret.json。':'OAuth 和视频处理均在这台 Mac 上运行。');
    if(!s.dependencies.ffmpeg||!s.dependencies.ffprobe)text+=' 需要安装 ffmpeg。';
    if(!s.dependencies.deno&&!s.dependencies.node)text+=' 需要安装 deno。';if(s.database_error)text=s.database_error;
    $('setup-text').textContent=text;$('connect').disabled=s.auth.busy||s.busy;$('connect').textContent=s.connected?'重新授权':'连接 YouTube';$('start').disabled=s.busy||s.auth.busy||!!s.database_error;
    latest=await api('/api/jobs');
    if(s.connected&&!s.auth.busy&&syncedToken!==token&&latest.some(j=>j.video_id)){
      syncedToken=token;
      try{await api('/api/visibility/sync',{});latest=await api('/api/jobs')}catch(e){notice(e.message)}
    }
    const key=JSON.stringify(latest)+'|'+[...visibilityPending].join(',');
    if(key!==$('jobs').dataset.key){render(latest);$('jobs').dataset.key=key}
  }catch(e){notice(e.message)}finally{refreshing=false}
}
$('sync-visibility').onclick=async()=>{const b=$('sync-visibility');b.disabled=true;try{notice((await api('/api/visibility/sync',{})).message)}catch(e){notice(e.message)}finally{await refresh();b.disabled=false}};
$('connect').onclick=async()=>{try{notice((await api('/api/auth',{})).message);await refresh()}catch(e){notice(e.message)}};
$('backup-form').onsubmit=async e=>{e.preventDefault();$('start').disabled=true;try{await api('/api/jobs',{url:$('url').value,title:$('title').value});notice('任务已开始');await refresh()}catch(e){notice(e.message);$('start').disabled=false}};
refresh();setInterval(refresh,2000);
