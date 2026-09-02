const taskDialog=document.querySelector('#taskDialog');
const detailTitle=document.querySelector('#detailTitle');
const taskDetail=document.querySelector('#taskDetail');
const correctionForm=document.querySelector('#correctionForm');
const settingsForm=document.querySelector('#settingsForm');
const settingSelect=(name,choices)=>{const input=settingsForm.elements[name];const select=document.createElement('select');select.name=name;select.setAttribute('aria-label',input.closest('label')?.firstChild?.textContent?.trim()||name);choices.forEach(([value,text])=>select.add(new Option(text,value)));input.replaceWith(select);return select};
settingSelect('language',[['auto','Automatic (project language)'],['English','English'],['Japanese','Japanese'],['Spanish','Spanish'],['French','French'],['German','German'],['Portuguese','Portuguese'],['Chinese','Chinese'],['Korean','Korean']]);
const browserTimeZone=Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC';
settingSelect('timeZone',[['local',`Computer local (${browserTimeZone})`],['UTC','UTC'],...(browserTimeZone==='UTC'?[]:[[browserTimeZone,browserTimeZone]])]);
settingSelect('retentionDays',[['30','30 days'],['90','90 days'],['180','180 days'],['365','1 year'],['730','2 years'],['1825','5 years'],['3650','10 years']]);
const notificationStatus=document.querySelector('#notificationStatus');
const notificationRows=document.querySelector('#notificationRows');
const notificationButton=document.querySelector('#notificationButton');
const notificationCount=document.querySelector('#notificationCount');
const notificationFilters=document.querySelector('#notificationFilters');
const notificationResultCount=document.querySelector('#notificationResultCount');
const inAppNotificationState=document.querySelector('#inAppNotificationState');
const osNotificationState=document.querySelector('#osNotificationState');
const settingsStatus=document.querySelector('#settingsStatus');
const gitLinkRows=document.querySelector('#gitLinkRows');
const correctionValue=document.querySelector('#correctionValue');
const correctionStatusValue=document.querySelector('#correctionStatusValue');
const correctionStatus=document.querySelector('#correctionStatus');

const detailTime=value=>value?new Date(value).toLocaleString():'time not recorded';
const evidenceText=value=>(value||[]).map(item=>typeof item==='string'?item:JSON.stringify(item)).join(' · ')||'no structured evidence';
function detailList(title,items,format,total=items.length){return `<section class="detail-section"><h3>${escapeHtml(title)}</h3>${items.length?`<ul>${items.map(item=>`<li>${format(item)}</li>`).join('')}</ul>`:'<p class="task-meta">None recorded.</p>'}${total>items.length?`<p class="task-meta">Showing ${items.length} of ${total} bounded records.</p>`:''}</section>`}
function syncCorrectionValue(){const status=correctionForm.elements.field.value==='status';correctionValue.hidden=status;correctionValue.disabled=status;correctionStatusValue.hidden=!status;correctionStatusValue.disabled=!status;correctionForm.elements.reason.required=status;correctionForm.elements.reason.labels[0].firstChild.textContent=status?'Reason (required for status override)':'Reason (optional)';correctionStatus.textContent='';correctionStatus.className='form-status'}
async function openTask(taskId,{resetForm=true}={}){
  const task=await api(`/api/tasks/${encodeURIComponent(taskId)}`);
  const bounds=task.evidenceBounds||{};
  const timedEvents=(task.events||[]).map(item=>new Date(item.occurred_at).getTime()).filter(Number.isFinite);
  const fallbackSpan=timedEvents.length>1?(Math.max(...timedEvents)-Math.min(...timedEvents))/60000:0;
  const observed=task.observedEffort||{eventCount:bounds.events||timedEvents.length,evidenceSpanMinutes:Math.max(0,Math.round(fallbackSpan*10)/10),endedSessions:0,endedSessionMinutes:0,firstAt:timedEvents.length?new Date(Math.min(...timedEvents)).toISOString():null,lastAt:timedEvents.length?new Date(Math.max(...timedEvents)).toISOString():null,caution:'Evidence span is derived from bounded displayed events, not continuous human work.'};
  const reportedActual=task.actual_minutes==null?'Not reported':`${task.actual_minutes} min`;
  const estimated=task.estimated_minutes==null?'Not reported':`${task.estimated_minutes} min`;
  const evidenceSpan=observed.eventCount?`${observed.evidenceSpanMinutes||0} min across ${observed.eventCount} event(s)`:'No timed events';
  const endedSessions=observed.endedSessions?`${observed.endedSessionMinutes||0} min across ${observed.endedSessions} ended AI session(s)`:'No ended AI-session duration';
  detailTitle.textContent=task.title;
  if(resetForm){correctionForm.reset();syncCorrectionValue()}
  correctionForm.elements.taskId.value=task.id;
  taskDetail.innerHTML=`<div class="detail-grid"><div><span>Status</span><strong>${escapeHtml(label(task.status))}</strong></div><div><span>Theme</span><strong>${escapeHtml(task.theme||'—')}</strong></div><div><span>Reported effort</span><strong>Actual ${escapeHtml(reportedActual)} · Estimated ${escapeHtml(estimated)}</strong></div></div><section class="detail-section"><h3>Observed time evidence</h3><p><strong>${escapeHtml(evidenceSpan)}</strong><br>${escapeHtml(endedSessions)}</p><p class="task-meta">${escapeHtml(observed.caution||'Observed timestamps are evidence, not reported human effort.')}${observed.firstAt?` First ${escapeHtml(detailTime(observed.firstAt))} · Latest ${escapeHtml(detailTime(observed.lastAt))}`:''}</p></section><p>${escapeHtml(task.summary||'No structured summary.')}</p>${detailList('Blockers',task.blockers||[],item=>`${escapeHtml(item.summary)} · ${escapeHtml(item.status)} · ${escapeHtml(detailTime(item.created_at))}`,bounds.blockers)}${detailList('Validation',task.validations||[],item=>`${escapeHtml(item.category)} · ${escapeHtml(item.outcome)} · ${escapeHtml(item.command_summary||'no command')} · ${escapeHtml(detailTime(item.occurred_at))}${item.duration_ms==null?'':` · ${escapeHtml(item.duration_ms)} ms`}`,bounds.validations)}${detailList('Git evidence',task.commits||[],item=>`${escapeHtml(item.hash.slice(0,10))} · ${escapeHtml(item.status)} · confidence ${Math.round(Number(item.confidence)*100)}% · ${escapeHtml(detailTime(item.committed_at))}`,bounds.commits)}${detailList('Events',task.events||[],item=>`${escapeHtml(label(item.event_type))} · ${escapeHtml(item.summary)} · ${escapeHtml(detailTime(item.occurred_at))} · confidence ${Math.round(Number(item.confidence)*100)}%<small>${escapeHtml(evidenceText(item.evidence))}</small>`,bounds.events)}`;
  if(!taskDialog.open)taskDialog.showModal();
}

document.querySelector('#taskRows').addEventListener('click',event=>{const button=event.target.closest('[data-task-id]');if(button)openTask(button.dataset.taskId).catch(error=>{taskDetail.textContent=error.message;taskDialog.showModal()})});
correctionForm.elements.field.addEventListener('change',syncCorrectionValue);
correctionForm.addEventListener('submit',async event=>{event.preventDefault();const submit=correctionForm.querySelector('[type="submit"]');if(submit.disabled)return;submit.disabled=true;correctionStatus.textContent='Saving correction…';correctionStatus.className='form-status';const data=Object.fromEntries(new FormData(correctionForm));try{await api('/api/corrections',{method:'POST',body:JSON.stringify(data)});const [refreshed,insights]=await Promise.all([api('/api/overview?activity=0'),api(`/api/insights?period=${insightPeriod}&anchor=${insightAnchor}`)]);state=refreshed;insightData=insights;renderNow();renderTasks();renderReview();renderIdeas();await openTask(data.taskId,{resetForm:false});correctionStatus.textContent='Correction saved to local evidence.';correctionStatus.className='form-status success'}catch(error){correctionStatus.textContent=`Could not save correction: ${error.message}`;correctionStatus.className='form-status error'}finally{submit.disabled=false}});
document.querySelector('#ideaRows').addEventListener('click',async event=>{const button=event.target.closest('[data-idea-id]');if(!button)return;const updated=await api('/api/ideas/status',{method:'POST',body:JSON.stringify({ideaId:button.dataset.ideaId,status:button.dataset.ideaStatus})});state.ideas=(state.ideas||[]).map(item=>item.id===updated.id?updated:item);renderIdeas()});

const preserveSettingChoice=(select,value)=>{if(![...select.options].some(option=>option.value===String(value)))select.add(new Option(`Previously saved: ${value}`,String(value)));select.value=String(value)};
async function loadSettings(){const settings=await api('/api/settings');preserveSettingChoice(settingsForm.elements.language,settings.language);preserveSettingChoice(settingsForm.elements.timeZone,settings.timeZone);preserveSettingChoice(settingsForm.elements.retentionDays,settings.retentionDays);settingsForm.elements.gitMetadata.checked=settings.gitMetadata;return settings}
settingsForm.addEventListener('submit',async event=>{event.preventDefault();const submit=settingsForm.querySelector('[type="submit"]');submit.disabled=true;settingsStatus.textContent='Saving locally…';try{const data={language:settingsForm.elements.language.value,timeZone:settingsForm.elements.timeZone.value,retentionDays:Number(settingsForm.elements.retentionDays.value),gitMetadata:settingsForm.elements.gitMetadata.checked};await api('/api/settings',{method:'POST',body:JSON.stringify(data)});settingsStatus.textContent='Saved locally. Notification delivery is managed in Notifications.';await loadGitLinks()}catch(error){settingsStatus.textContent=`Could not save settings: ${error.message}`}finally{submit.disabled=false}});
const notificationEvidence=item=>(item.evidence||[]).map(value=>`${label(value.name)}: ${value.value}`).join(' · ');
let notificationItems=[];
let notificationFilter='unread';
let notificationCounts={all:0,unread:0,read:0,dismissed:0};
function renderNotifications(){
  const visible=notificationFilter==='all'?notificationItems:notificationItems.filter(item=>item.state===notificationFilter);
  const displayed=visible.slice(0,50);
  const exact=notificationCounts[notificationFilter]||0;
  notificationResultCount.textContent=displayed.length<exact?`SHOWING ${displayed.length} OF ${exact}`:`${exact} RECORDS`;
  notificationRows.innerHTML=displayed.map(item=>`<article class="notification-row ${item.state==='unread'?'is-unread':''}"><div class="notification-heading"><strong>${escapeHtml(item.title)}</strong><span class="notification-state">${escapeHtml(label(item.state))}</span></div><p>${escapeHtml(item.body)}</p><small>${escapeHtml(notificationEvidence(item)||'Structured evidence not recorded')} · ${escapeHtml(detailTime(item.updatedAt))}</small><div class="notification-row-actions">${item.state==='dismissed'?`<button class="row-action" type="button" data-notification-id="${escapeHtml(item.id)}" data-notification-state="unread">Restore to unread</button>`:`<button class="row-action" type="button" data-notification-id="${escapeHtml(item.id)}" data-notification-state="${item.state==='unread'?'read':'unread'}">Mark ${item.state==='unread'?'read':'unread'}</button><button class="row-action" type="button" data-notification-id="${escapeHtml(item.id)}" data-notification-state="dismissed">Dismiss</button>`}</div></article>`).join('')||`<div class="empty-message"><strong>No ${escapeHtml(notificationFilter==='all'?'':notificationFilter)} notifications</strong><span>Notification history appears here after project evidence is analyzed.</span></div>`;
}
async function loadNotifications(){
  const [data,settings]=await Promise.all([api('/api/notifications?limit=200'),api('/api/settings')]);
  notificationItems=data.items;
  notificationCounts=data.counts||{all:data.items.length,unread:data.items.filter(item=>item.state==='unread').length,read:data.items.filter(item=>item.state==='read').length,dismissed:data.items.filter(item=>item.state==='dismissed').length};
  const unread=Number.isFinite(Number(data.unreadCount))?Number(data.unreadCount):data.items.filter(item=>item.state==='unread').length;
  notificationCount.textContent=String(unread);
  notificationButton.setAttribute('aria-label',`Notifications, ${unread} unread`);
  notificationButton.classList.toggle('has-unread',unread>0);
  const fullyConfigured=Boolean(settings.notificationsEnabled&&data.scheduler.installed);
  const lastDelivered=data.items.find(item=>item.deliveredAt)?.deliveredAt;
  inAppNotificationState.textContent=settings.notificationsEnabled?'ACTIVE':'PAUSED';
  osNotificationState.textContent=data.scheduler.installed?'SCHEDULED':'NOT SET UP';
  notificationStatus.textContent=fullyConfigured?`Both surfaces are active · daily at 6:00 PM local time${lastDelivered?` · last delivered ${detailTime(lastDelivered)}`:''}`:`Setup incomplete · ${data.scheduler.platform==='Darwin'?'use Set up both':'computer delivery is not supported on this OS yet'}`;
  document.querySelector('[data-notification-action="install"]').textContent=fullyConfigured?'Reconfigure both':'Set up both';
  renderNotifications();
}
document.querySelector('#view-notifications').addEventListener('click',async event=>{const button=event.target.closest('[data-notification-action]');if(!button)return;button.disabled=true;try{if(button.dataset.notificationAction==='run')await api('/api/notifications/run',{method:'POST',body:'{}'});else await api('/api/notifications/scheduler',{method:'POST',body:JSON.stringify({action:button.dataset.notificationAction})});await loadNotifications()}catch(error){notificationStatus.textContent=`Setup incomplete: ${error.message}`}finally{button.disabled=false}});
notificationFilters.addEventListener('click',event=>{const button=event.target.closest('[data-notification-filter]');if(!button)return;notificationFilter=button.dataset.notificationFilter;notificationFilters.querySelectorAll('button').forEach(item=>{const active=item===button;item.classList.toggle('active',active);item.setAttribute('aria-pressed',String(active))});renderNotifications()});
notificationRows.addEventListener('click',async event=>{const button=event.target.closest('[data-notification-id]');if(!button)return;button.disabled=true;try{await api('/api/notifications/state',{method:'POST',body:JSON.stringify({notificationId:button.dataset.notificationId,state:button.dataset.notificationState})});await loadNotifications()}catch(error){notificationStatus.textContent=`Could not update notification: ${error.message}`}finally{button.disabled=false}});
async function loadGitLinks(){const links=await api('/api/git-links');gitLinkRows.innerHTML=links.length?links.map(item=>`<div class="git-link"><strong>${escapeHtml(item.taskTitle)}</strong><span>${escapeHtml(item.commitHash.slice(0,10))} · ${escapeHtml(item.status)} · ${Math.round(item.confidence*100)}%</span><div><button class="row-action" data-git-task="${escapeHtml(item.taskId)}" data-git-commit="${escapeHtml(item.commitHash)}" data-git-status="confirmed">Confirm</button><button class="row-action" data-git-task="${escapeHtml(item.taskId)}" data-git-commit="${escapeHtml(item.commitHash)}" data-git-status="rejected">Reject</button></div></div>`).join(''):'<p class="task-meta">No Git candidates. Collection remains optional.</p>'}
gitLinkRows.addEventListener('click',async event=>{const button=event.target.closest('[data-git-task]');if(!button)return;await api('/api/git-links/status',{method:'POST',body:JSON.stringify({taskId:button.dataset.gitTask,commitHash:button.dataset.gitCommit,status:button.dataset.gitStatus})});await loadGitLinks()});
document.querySelector('#exportData').addEventListener('click',async()=>{const data=await api('/api/export');const href=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=href;link.download='project-tasks-export.json';link.click();URL.revokeObjectURL(href)});
loadSettings().catch(error=>{settingsStatus.textContent=error.message});loadGitLinks().catch(error=>{gitLinkRows.textContent=error.message});loadNotifications().catch(error=>{notificationStatus.textContent=error.message});

const guideToggle=document.querySelector('#guideToggle');
const guideContent=document.querySelector('#guideContent');
const shortcutDialog=document.querySelector('#shortcutDialog');
const shortcutHelp=document.querySelector('#shortcutHelp');
const characterShortcuts=document.querySelector('#characterShortcuts');
const dashboardTabs=[...document.querySelectorAll('.tab')];
const guideStorageKey='project-tasks:guide-collapsed';
const shortcutStorageKey='project-tasks:character-shortcuts';

function setGuideCollapsed(collapsed,{focusToggle=false}={}){
  guideContent.hidden=collapsed;
  guideToggle.setAttribute('aria-expanded',String(!collapsed));
  guideToggle.querySelector('span').textContent=collapsed?'Show guide':'Hide guide';
  try{localStorage.setItem(guideStorageKey,String(collapsed))}catch(_error){}
  if(focusToggle)guideToggle.focus();
}

function activateDashboardTab(index,{focus=true}={}){
  const tab=dashboardTabs[index];
  if(!tab)return;
  tab.click();
  if(focus)tab.focus();
}

notificationButton.addEventListener('click',()=>activateDashboardTab(5));

function editableTarget(target){
  return target instanceof Element&&Boolean(target.closest('input,textarea,select')||target.isContentEditable);
}

function openShortcutHelp(){
  if(!shortcutDialog.open)shortcutDialog.showModal();
}

function syncCharacterShortcutContract(enabled){
  document.querySelectorAll('[aria-keyshortcuts],[data-keyshortcuts]').forEach(element=>{
    const value=element.getAttribute('aria-keyshortcuts')||element.dataset.keyshortcuts;
    if(!value)return;
    element.dataset.keyshortcuts=value;
    if(enabled)element.setAttribute('aria-keyshortcuts',value);else element.removeAttribute('aria-keyshortcuts');
  });
  shortcutDialog.toggleAttribute('data-shortcuts-disabled',!enabled);
  document.querySelector('#shortcutNote').textContent=enabled?'Shortcuts pause while you type and never override Ctrl, Command, Alt, or browser shortcuts.':'Single-key shortcuts are off. Tab and tab-list arrow navigation remain available.';
}

try{setGuideCollapsed(localStorage.getItem(guideStorageKey)==='true')}catch(_error){setGuideCollapsed(false)}
try{characterShortcuts.checked=localStorage.getItem(shortcutStorageKey)!=='false'}catch(_error){}
syncCharacterShortcutContract(characterShortcuts.checked);
guideToggle.addEventListener('click',()=>setGuideCollapsed(guideToggle.getAttribute('aria-expanded')==='true'));
shortcutHelp.addEventListener('click',openShortcutHelp);
characterShortcuts.addEventListener('change',()=>{syncCharacterShortcutContract(characterShortcuts.checked);try{localStorage.setItem(shortcutStorageKey,String(characterShortcuts.checked))}catch(_error){}});

document.querySelector('.tabs').addEventListener('keydown',event=>{
  if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
  event.preventDefault();
  const current=dashboardTabs.indexOf(document.activeElement);
  const index=event.key==='Home'?0:event.key==='End'?dashboardTabs.length-1:
    (current+(event.key==='ArrowRight'?1:-1)+dashboardTabs.length)%dashboardTabs.length;
  activateDashboardTab(index);
});

document.addEventListener('keydown',event=>{
  if(event.key==='Escape'){
    if(shortcutDialog.open){shortcutDialog.close();return}
    if(taskDialog.open){taskDialog.close();return}
    if(!editableTarget(event.target)&&guideToggle.getAttribute('aria-expanded')==='true'&&document.querySelector('.project-guide').contains(document.activeElement)){
      setGuideCollapsed(true,{focusToggle:true});
    }
    return;
  }
  if(shortcutDialog.open||taskDialog.open)return;
  if(event.defaultPrevented||event.repeat||event.isComposing||event.ctrlKey||event.metaKey||event.altKey||editableTarget(event.target))return;
  if(!characterShortcuts.checked)return;
  const key=event.key.toLowerCase();
  if(/^[1-7]$/.test(key)){event.preventDefault();activateDashboardTab(Number(key)-1);return}
  if(key==='?'){event.preventDefault();openShortcutHelp();return}
  if(key==='g'){event.preventDefault();if(document.querySelector('.project-guide').hidden)activateDashboardTab(0,{focus:false});setGuideCollapsed(guideToggle.getAttribute('aria-expanded')==='true',{focusToggle:true});return}
  if(key==='q'){event.preventDefault();if(document.querySelector('.project-guide').hidden)activateDashboardTab(0,{focus:false});setGuideCollapsed(false);document.querySelector('#guideQuestion').focus();return}
  if(key==='/'){event.preventDefault();activateDashboardTab(1,{focus:false});document.querySelector('#taskFilter').focus();return}
  if(['d','w','m','y'].includes(key)){event.preventDefault();if(document.querySelector('.period-bar').hidden)activateDashboardTab(0,{focus:false});document.querySelector(`[data-period="${({d:'day',w:'week',m:'month',y:'year'})[key]}"]`).click();return}
  if(key==='['){event.preventDefault();document.querySelector('#previousRange').click();return}
  if(key===']'){event.preventDefault();document.querySelector('#nextRange').click()}
});
