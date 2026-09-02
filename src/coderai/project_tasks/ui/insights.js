let insightPeriod = 'week';
let insightData = null;
const localDate = date => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
let insightAnchor = '';
let insightRequest = 0;
let graphMode = 'project';
let graphSelection = '';
let mapData = null;
let mapCompatibilityError = '';
const graphOptions = new URLSearchParams(location.search);
let graphLayout = ['story', 'flow', 'timeline', 'focus'].includes(graphOptions.get('layout')) ? graphOptions.get('layout') : 'story';
let graphDensity = graphOptions.get('density') === 'compact' ? 'compact' : 'comfortable';
let graphZoom = 1;
const taskBatchSize = 8;
let taskRenderLimit = taskBatchSize;
const userTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
const timeZoneQuery = `&timeZone=${encodeURIComponent(userTimeZone)}`;

const periodCopy = {
  day: ['What happened today?', 'Today', 'Daily signal'],
  week: ['What changed this week?', 'This week', 'Weekly signal'],
  month: ['Where did momentum change this month?', 'This month', 'Monthly pattern'],
  year: ['How did the project evolve this year?', 'This year', 'Yearly direction'],
};
const activityNames = {
  task_started: 'Started', task_continued: 'Continued', task_paused: 'Paused', task_blocked: 'Blocked',
  validation_passed: 'Proof passed', validation_failed: 'Proof failed', task_completed: 'Completed',
  task_corrected: 'Corrected', session_ended: 'Session ended',
};
const eventName = type => activityNames[type] || label(type);
const periodCountKeys = ['started', 'completed', 'blocked', 'passed', 'failed', 'other'];
const normalizePeriodInsights = data => {
  const buckets = (data?.buckets || []).map(bucket => {
    const counts = Object.fromEntries(periodCountKeys.map(key => [key, finiteNumber(bucket?.[key])]));
    const totalEvents = finiteNumber(bucket?.totalEvents ?? periodCountKeys.reduce((sum, key) => sum + counts[key], 0));
    return {...bucket, ...counts, totalEvents, eventIds: Array.isArray(bucket?.eventIds) ? bucket.eventIds : []};
  });
  const normalizeTotals = (totals = {}, fallbackBuckets = []) => {
    const counts = Object.fromEntries(periodCountKeys.map(key => [key, finiteNumber(totals?.[key] ?? fallbackBuckets.reduce((sum, bucket) => sum + finiteNumber(bucket[key]), 0))]));
    return {...totals, ...counts, totalEvents: finiteNumber(totals?.totalEvents ?? fallbackBuckets.reduce((sum, bucket) => sum + finiteNumber(bucket.totalEvents), 0)), completedTasks: finiteNumber(totals?.completedTasks ?? totals?.completed)};
  };
  return {...data, buckets, activity: Array.isArray(data?.activity) ? data.activity : [], completedTasks: Array.isArray(data?.completedTasks) ? data.completedTasks : [], totals: normalizeTotals(data?.totals, buckets), comparison: {...(data?.comparison || {}), totals: normalizeTotals(data?.comparison?.totals)}};
};
const projectLocalDate = value => {
  const [year, month, day] = String(value).slice(0, 10).split('-').map(Number);
  return new Date(year, month - 1, day);
};
const projectLocalDateTime = value => new Date(value);
const humanDateTime = value => value ? new Date(value).toLocaleString([], {dateStyle:'medium',timeStyle:'short',timeZone:userTimeZone}) : 'time not recorded';
const ago = value => {
  if (!value) return 'time not recorded';
  const seconds = Math.round((new Date(value).getTime() - Date.now()) / 1000);
  if (seconds > 300 || Math.abs(seconds) > 31536000) return new Date(value).toLocaleDateString([], {year: 'numeric', month: 'short', day: 'numeric'});
  for (const [size, unit] of [[86400, 'day'], [3600, 'hour'], [60, 'minute']]) {
    if (Math.abs(seconds) >= size) return new Intl.RelativeTimeFormat(undefined, {numeric: 'auto'}).format(Math.round(seconds / size), unit);
  }
  return 'just now';
};
const finiteNumber = value => Number.isFinite(Number(value)) ? Number(value) : 0;

renderNow = function () {
  const briefing = state.briefing || {};
  document.querySelector('#projectName').textContent = briefing.project?.id || 'local workspace';
  const demo = briefing.project?.id === 'atlas-demo';
  document.querySelector('#dataMode').textContent = demo ? 'DEMO PREVIEW · DISPOSABLE' : 'LOCAL ONLY';
  document.querySelector('#nowTitle').textContent = briefing.project?.title || 'Project Tasks';
  document.querySelector('#purpose').textContent = briefing.project?.purpose || 'Private project intelligence from structured lifecycle evidence.';
  const totals = insightData?.totals || {};
  const needsAttention = ['active', 'blocked', 'needs_validation'].reduce((sum, status) => sum + (state.counts?.[status] || 0), 0);
  const metrics = [
    [needsAttention, 'Needs attention', 'Current project state', 'tasks', ''],
    [totals.completedTasks || 0, 'Tasks completed', periodCopy[insightPeriod][1], 'review', ''],
    [totals.passed || 0, 'Proof records passed', `${periodCopy[insightPeriod][1]} · ${totals.failed || 0} failed`, 'review', ''],
    [state.counts?.blocked || 0, 'Blocked now', 'Requires a decision', 'tasks', 'blocked'],
  ];
  document.querySelector('#summaryGrid').innerHTML = metrics.map(([value, title, hint, view, filter]) => `<button class="metric" data-open-view="${view}" data-view-filter="${filter}"><strong>${value}</strong><span>${title}</span><small>${hint}</small></button>`).join('');
  document.querySelector('#activeTasks').innerHTML = state.active?.length ? state.active.map(task => `<button class="focus-card" data-open-task="${escapeHtml(task.id)}"><span class="status-dot ${escapeHtml(task.status)}"></span><span><strong>${escapeHtml(task.title)}</strong><small>${escapeHtml(task.latestSummary || eventName(task.latestEvent))} · ${ago(task.latestAt)}</small></span><span class="badge ${escapeHtml(task.status)}">${escapeHtml(label(task.status))}</span></button>`).join('') : '<div class="empty-message"><strong>Nothing needs attention.</strong><span>Start a small outcome when you are ready.</span></div>';
  document.querySelector('#activeSummary').textContent = needsAttention > (state.active?.length || 0) ? `Showing ${state.active.length} of ${needsAttention}. Open Work to see all.` : '';
  const compatibilityTask = state.active?.[0];
  const recommendation = state.intelligence?.recommendation || (compatibilityTask ? {
    id: 'compatibility-active-task', title: `Continue ${compatibilityTask.title}`,
    reason: 'This task is currently active. Detailed recommendation evidence will appear after the local dashboard service refreshes.',
    taskId: compatibilityTask.id, confidence: finiteNumber(compatibilityTask.confidence), traceable: false,
  } : null);
  document.querySelector('#nextAction').textContent = recommendation?.title || 'No evidence-backed next action is available.';
  document.querySelector('#nextReason').textContent = recommendation ? `${recommendation.reason} · ${Math.round(recommendation.confidence * 100)}% evidence confidence` : 'Evidence coverage is insufficient.';
  document.querySelector('#nextActions').innerHTML = recommendation ? `${recommendation.taskId ? `<button class="quiet-button" data-open-task="${escapeHtml(recommendation.taskId)}">Inspect task</button>` : ''}${recommendation.traceable === false ? '' : `<button class="quiet-button" data-trace-recommendation="${escapeHtml(recommendation.id)}">Trace evidence</button>`}` : '';
  const guidance = insightData?.progressGuidance || {};
  document.querySelector('#guidanceHeadline').textContent = guidance.headline || 'Waiting for selected-period evidence';
  document.querySelector('#guidanceExplanation').textContent = guidance.explanation || 'No interpretation is available yet.';
  document.querySelector('#guidanceNext').textContent = guidance.nextAction ? `Next: ${guidance.nextAction}` : '';
  document.querySelector('#guidanceCaution').textContent = guidance.caution || 'Missing evidence is unknown.';
  const periodStart = new Date(insightData?.periodStart || 0).getTime();
  const periodEnd = new Date(insightData?.periodEnd || 0).getTime();
  const periodSessions = (state.sessions || []).filter(session => { const started = new Date(session.startedAt).getTime(); return Number.isFinite(started) && (!periodStart || started >= periodStart) && (!periodEnd || started < periodEnd); });
  const groupedSessions = [];
  const unattributed = new Map();
  periodSessions.forEach(session => {
    if (session.agentName && session.agentName !== 'unknown') { groupedSessions.push({...session, recordCount: 1}); return; }
    const key = session.taskId || `unlinked:${session.taskTitle || ''}`;
    const current = unattributed.get(key);
    if (!current) unattributed.set(key, {...session, recordCount: 1});
    else { current.recordCount += 1; if (new Date(session.startedAt) > new Date(current.startedAt)) current.startedAt = session.startedAt; }
  });
  groupedSessions.push(...unattributed.values());
  groupedSessions.sort((left, right) => new Date(right.startedAt) - new Date(left.startedAt));
  document.querySelector('.sessions-panel .panel-head span').textContent = periodCopy[insightPeriod][1].toUpperCase();
  document.querySelector('#sessionRows').innerHTML = groupedSessions.slice(0, 4).map(session => { const agent = session.agentName && session.agentName !== 'unknown' ? session.agentName : 'Agent not recorded'; const content = `<span>AI</span><div><strong>${escapeHtml(agent)}${session.modelName ? ` · ${escapeHtml(session.modelName)}` : ''}${session.recordCount > 1 ? ` · ${session.recordCount} records` : ''}</strong><small>${escapeHtml(session.taskTitle || 'Unassigned session')} · ${session.endedAt ? 'ended' : 'active'} · ${ago(session.startedAt)}</small></div>`; return session.taskId ? `<button class="session-row" data-open-task="${escapeHtml(session.taskId)}">${content}</button>` : `<div class="session-row session-unlinked">${content}</div>`; }).join('') || `<div class="empty-message"><strong>No AI session in ${escapeHtml(periodCopy[insightPeriod][1].toLowerCase())}.</strong><span>Changing the selected period also filters this history.</span></div>`;
  const hiddenSessions = Math.max(0, groupedSessions.length - 4);
  const unattributedCount = periodSessions.filter(session => !session.agentName || session.agentName === 'unknown').length;
  document.querySelector('#sessionSummary').textContent = `${periodSessions.length} bounded session record(s) in ${periodCopy[insightPeriod][1].toLowerCase()}${hiddenSessions ? ` · ${hiddenSessions} grouped row(s) not shown` : ''}${unattributedCount ? ` · ${unattributedCount} without recorded agent identity` : ''}.`;
  renderSignal();
  renderActivity(insightData?.activity || state.activity || []);
};

function renderSignal() {
  if (!insightData) return;
  document.querySelector('#periodQuestion').textContent = periodCopy[insightPeriod][0];
  document.querySelector('#signalTitle').textContent = periodCopy[insightPeriod][2];
  document.querySelector('#activityPeriod').textContent = periodCopy[insightPeriod][1].toUpperCase();
  const start = projectLocalDate(insightData.periodStart);
  const endDate = projectLocalDate(insightData.periodEnd); endDate.setDate(endDate.getDate() - 1); const end = endDate;
  const options = insightPeriod === 'year' ? {year: 'numeric'} : {year: 'numeric', month: 'short', day: 'numeric'};
  document.querySelector('#rangeLabel').textContent = insightPeriod === 'day'
    ? start.toLocaleDateString([], options)
    : `${start.toLocaleDateString([], options)} – ${end.toLocaleDateString([], options)}`;
  document.querySelector('#nextRange').disabled = !insightData.canMoveNext;
  const buckets = Array.isArray(insightData.buckets) ? insightData.buckets : [];
  const maximum = Math.max(1, ...buckets.map(bucket => finiteNumber(bucket.totalEvidence ?? bucket.totalEvents)));
  document.querySelector('#signalChart').innerHTML = buckets.map(bucket => {
    const positive = finiteNumber(bucket.completed) + finiteNumber(bucket.passed) + finiteNumber(bucket.automaticPassed);
    const attention = finiteNumber(bucket.blocked) + finiteNumber(bucket.failed) + finiteNumber(bucket.automaticFailed);
    const otherEvidence = finiteNumber(bucket.other) + finiteNumber(bucket.automaticObservations);
    const values = [['started', finiteNumber(bucket.started)], ['positive', positive], ['attention', attention], ['other', otherEvidence]];
    let bottom = 116;
    const segments = values.map(([kind, value]) => { const height = value / maximum * 108; bottom -= height; return value ? `<rect class="${kind}" x="3" y="${bottom}" width="18" height="${height}" rx="2"/>` : ''; }).join('');
    return `<button class="signal-point" data-bucket="${escapeHtml(bucket.id)}" aria-label="${escapeHtml(bucket.label)}: ${finiteNumber(bucket.totalEvidence ?? bucket.totalEvents)} evidence records; ${finiteNumber(bucket.started)} started, ${positive} completed or passed, ${attention} blocked or failed, ${otherEvidence} other evidence"><svg class="signal-stack" viewBox="0 0 24 120" aria-hidden="true"><path d="M3 116H21"/>${segments}</svg><strong>${escapeHtml(bucket.label)}</strong><small>${finiteNumber(bucket.totalEvidence ?? bucket.totalEvents)}</small></button>`;
  }).join('');
  const totals = insightData.totals;
  const takeaway = totals.blocked || totals.failed ? `${totals.blocked + totals.failed} attention signal(s) appeared; inspect the highlighted periods.` : totals.completed ? `${totals.completed} outcome(s) completed with ${totals.passed} passing proof record(s).` : 'No strong lifecycle signal yet. Empty periods are useful context, not failure.';
  document.querySelector('#signalTakeaway').textContent = takeaway;
}

function renderActivity(items, exactTotal = items.length, bounded = false) {
  document.querySelector('#todayTimeline').innerHTML = items.slice(0, 12).map(item => { const content=`<time>${projectLocalDateTime(item.occurredLocal || item.occurredAt).toLocaleString([], {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'})}</time><span class="timeline-mark ${escapeHtml(item.type)}"></span><span><strong>${escapeHtml(eventName(item.type))}: ${escapeHtml(item.taskTitle)}</strong><small>${escapeHtml(item.summary)}${item.agentName ? ` · ${escapeHtml(item.agentName)}` : ''}</small></span>`; return item.taskId ? `<button class="timeline-item" data-open-task="${escapeHtml(item.taskId)}">${content}</button>` : `<div class="timeline-item evidence-only">${content}</div>`; }).join('') || '<div class="empty-message"><strong>No activity in this period.</strong><span>Try another range or record the next task event.</span></div>';
  document.querySelector('#activitySummary').textContent = exactTotal > 12 ? `Showing ${Math.min(12, items.length)} of ${exactTotal} events${bounded ? ' from a bounded detail sample' : ''}. Select a chart point to narrow the evidence.` : items.length ? `Showing all ${items.length} event(s).` : exactTotal ? `No detail for ${exactTotal} event(s) is available in the bounded sample.` : '';
}

function inspectBucket(bucketId) {
  const bucket = insightData.buckets.find(item => item.id === bucketId);
  if (!bucket) return;
  const events = insightData.activity.filter(item => bucket.eventIds.includes(item.id));
  const observations = insightData.activity.filter(item => (bucket.observationIds || []).includes(item.id));
  events.push(...observations);
  document.querySelectorAll('.signal-point').forEach(item => item.classList.toggle('selected', item.dataset.bucket === bucketId));
  document.querySelector('#clearBucket').disabled = false;
  document.querySelector('#bucketDetail').innerHTML = `<p class="eyebrow">${escapeHtml(bucket.label)} · ${bucket.totalEvents} EVENT(S)</p><h3>${bucket.started} started · ${bucket.completed} completed · ${bucket.passed} proof passed · ${bucket.blocked} blocked · ${bucket.failed} proof failed · ${bucket.other} other</h3>${events.length ? `<ul>${events.map(item => `<li><button data-open-task="${escapeHtml(item.taskId)}"><strong>${escapeHtml(eventName(item.type))}</strong><span>${escapeHtml(item.taskTitle)}</span></button></li>`).join('')}</ul>${events.length < bucket.totalEvents ? `<p class="panel-summary">Showing ${events.length} of ${bucket.totalEvents} bounded detail events.</p>` : ''}` : '<p>No lifecycle event detail is available in the bounded sample.</p>'}`;
  renderActivity(events, bucket.totalEvents, events.length < bucket.totalEvents);
}

renderTasks = function (filter = '') {
  const input = document.querySelector('#taskFilter');
  const query = String(filter || input.value || '').trim().toLowerCase();
  const selectedStatus = document.querySelector('#taskStatus').value;
  const sort = document.querySelector('#taskSort').value;
  const from = document.querySelector('#taskDateFrom')?.value || '';
  const to = document.querySelector('#taskDateTo')?.value || '';
  const statusOrder = {blocked: 0, needs_validation: 1, active: 2, paused: 3, proposed: 4, completed: 5, cancelled: 6};
  const tasks = state.tasks.filter(task => { const activityDate=String(task.latestAt||task.updated_at||'').slice(0,10); return (!selectedStatus||task.status===selectedStatus)&&(!from||activityDate>=from)&&(!to||activityDate<=to)&&`${task.title||''} ${task.theme||''} ${task.status||''} ${task.latestSummary||''} ${task.latestEvent||''}`.toLowerCase().includes(query); });
  tasks.sort((left, right) => sort === 'title' ? String(left.title).localeCompare(String(right.title)) : sort === 'status' ? (statusOrder[left.status] ?? 99) - (statusOrder[right.status] ?? 99) || String(left.title).localeCompare(String(right.title)) : sort === 'effort' ? (right.actual_minutes ?? -1) - (left.actual_minutes ?? -1) : String(right.updated_at).localeCompare(String(left.updated_at)));
  const visible = tasks.slice(0, taskRenderLimit);
  document.querySelector('#taskRows').innerHTML = visible.map((task, index) => {
    const evidence = task.openBlockers ? `${task.openBlockers} open blocker(s)` : task.latestProof ? `Proof ${label(task.latestProof)}` : 'Proof not recorded';
    const effort = task.actual_minutes == null && task.estimated_minutes == null ? 'Not reported' : `Actual ${task.actual_minutes ?? 'unknown'} · Estimated ${task.estimated_minutes ?? 'unknown'} min`;
    return `<tr><td><span class="mobile-cell-label" aria-hidden="true">Status</span><span class="badge ${escapeHtml(task.status)}">${escapeHtml(label(task.status))}</span></td><td><span class="mobile-cell-label" aria-hidden="true">Task</span><span><strong>${escapeHtml(task.title)}</strong><small>${escapeHtml(task.theme || 'No theme')}</small></span></td><td><span class="mobile-cell-label" aria-hidden="true">Latest activity</span><span><strong>${escapeHtml(eventName(task.latestEvent))}</strong><small>${escapeHtml(task.latestSummary || 'No summary')} · ${ago(task.latestAt)}</small></span></td><td><span class="mobile-cell-label" aria-hidden="true">Evidence</span><span>${escapeHtml(evidence)}</span></td><td><span class="mobile-cell-label" aria-hidden="true">Effort</span><span>${escapeHtml(effort)}</span></td><td><span class="mobile-cell-label" aria-hidden="true">Action</span><button id="task-inspect-${index}" class="row-action" data-task-id="${escapeHtml(task.id)}">Inspect</button></td></tr>`;
  }).join('') || '<tr><td colspan="6">No matching work. Clear filters or try another term.</td></tr>';
  const activeFilters = [query ? `search “${query}”` : '', selectedStatus ? label(selectedStatus) : '', from ? `from ${from}` : '', to ? `through ${to}` : ''].filter(Boolean);
  document.querySelector('#taskResults').textContent = `Showing ${visible.length} of ${tasks.length} matching · ${state.tasks.length} total${activeFilters.length ? ` · Filtered by ${activeFilters.join(' + ')}` : ''}`;
  const more = document.querySelector('#taskShowMore'); more.hidden = visible.length >= tasks.length; more.textContent = `Show ${Math.min(taskBatchSize, tasks.length - visible.length)} more tasks`;
};

function installTaskDateControls(){
  const controls=document.querySelector('#view-tasks .task-controls');
  if(!controls||document.querySelector('#taskDatePreset'))return;
  controls.querySelector('#clearTaskFilters').insertAdjacentHTML('beforebegin','<label>ACTIVITY RANGE<select id="taskDatePreset"><option value="">Any time</option><option value="7">Last 7 days</option><option value="30">Last 30 days</option><option value="365">Last year</option><option value="custom">Custom dates</option></select></label><label>FROM<input id="taskDateFrom" type="date" aria-label="Latest activity from date"></label><label>TO<input id="taskDateTo" type="date" aria-label="Latest activity through date"></label>');
  document.querySelector('#taskResults').tabIndex=-1;
}

const graphSets = {project: ['source', 'signal', 'recommendation', 'task', 'blocker', 'validation'], tasks: ['recommendation', 'task', 'theme'], evidence: ['source', 'signal', 'recommendation', 'task', 'blocker', 'validation', 'commit'], agents: ['task', 'session'], all: ['source', 'signal', 'recommendation', 'task', 'theme', 'session', 'blocker', 'validation', 'commit']};
const relationshipName = type => ({groups: 'groups', worked_on: 'worked on', blocked_by: 'is blocked by', validated_by: 'is validated by', supported_by: 'is supported by', supports: 'supports', reports: 'reports evidence state', indicates: 'indicates', motivates: 'motivates', prioritizes: 'prioritizes'}[type] || label(type));

renderMap = function () {
  const svg = document.querySelector('#taskMap');
  if (mapData?.graph) state.graph = mapData.graph;
  if (!document.querySelector('#graphSummary')) {
    const toolbar = document.querySelector('#graphFilters');
    toolbar.insertAdjacentHTML('beforebegin', '<article class="decision-summary" id="decisionSummary" aria-live="polite"></article>');
    toolbar.insertAdjacentHTML('beforebegin', `<div class="journey-guide"><div><span>1</span><strong>Source</strong><small>Where bounded evidence came from</small></div><b>→</b><div><span>2</span><strong>Signal</strong><small>What the evidence indicates</small></div><b>→</b><div><span>3</span><strong>Recommendation</strong><small>Why this work should be next</small></div><b>→</b><div><span>4</span><strong>Task + proof</strong><small>The outcome and its validation</small></div></div><p class="graph-summary" id="graphSummary"></p><div class="graph-customize"><div><span>LAYOUT</span><div id="graphLayouts">${['story','flow','timeline','focus'].map(item => `<button class="${item === graphLayout ? 'active' : ''}" data-graph-layout="${item}">${item[0].toUpperCase() + item.slice(1)}</button>`).join('')}</div></div><div><span>DISPLAY</span><div id="graphDisplay"><button class="${graphDensity === 'comfortable' ? 'active' : ''}" data-density="comfortable">Comfortable</button><button class="${graphDensity === 'compact' ? 'active' : ''}" data-density="compact">Compact</button><button data-zoom="out" aria-label="Zoom out">−</button><button data-zoom="reset">Reset</button><button data-zoom="in" aria-label="Zoom in">+</button></div></div></div>`);
  }
  if (!state.graph) {
    document.querySelector('#graphSummary').textContent = mapCompatibilityError || 'Loading bounded project evidence…';
    svg.innerHTML = '';
    document.querySelector('#mapFallback').textContent = mapCompatibilityError || 'Project evidence is loading.';
    document.querySelector('#graphDetail').innerHTML = mapCompatibilityError ? `<p class="eyebrow">UPDATE REQUIRED</p><h2>Restart the local dashboard</h2><p>${escapeHtml(mapCompatibilityError)}</p>` : '<p class="eyebrow">LOADING</p><h2>Preparing the evidence map</h2><p>Tasks, signals, recommendations, and proof will appear when local data is ready.</p>';
    return;
  }
  const guidance = state.graph.contractVersion === 2 ? state.graph.guidance : null;
  const graphCounts = state.graph.nodes.reduce((result, node) => ({...result, [node.type]: (result[node.type] || 0) + 1}), {});
  const intelligence = state.intelligence || {};
  const recommendation = intelligence.recommendation;
  const signal = (intelligence.signals || []).find(item => recommendation?.signalIds?.includes(item.id));
  const sourceNames = (signal?.sourceIds || []).map(id => (intelligence.sources || []).find(source => source.id === id)?.label).filter(Boolean);
  const periodDescription = state.graph.period === 'all' ? 'Bounded all-history evidence' : `${label(state.graph.period)} · ${projectLocalDate(state.graph.periodStart).toLocaleDateString(undefined, {dateStyle: 'medium'})} – ${projectLocalDate(state.graph.periodEnd).toLocaleDateString(undefined, {dateStyle: 'medium'})}`;
  document.querySelector('#decisionSummary').innerHTML = guidance ? `<div><span>WHAT NEEDS ATTENTION</span><strong>${escapeHtml(guidance.headline)}</strong><small>${escapeHtml(periodDescription)}</small></div><b aria-hidden="true">→</b><div><span>USEFUL NEXT STEP</span><strong>${escapeHtml(guidance.nextAction)}</strong><small>Based only on evidence recorded in this period.</small><button class="row-action trace-evidence" data-trace-map-evidence>Trace ${graphCounts.validation || 0} proof and ${graphCounts.blocker || 0} blocker record(s)</button></div><div class="decision-evidence"><span>HOW TO INTERPRET THIS</span><small>${escapeHtml(guidance.caution)}</small></div>` : recommendation ? `<div><span>WHY NOW</span><strong>${escapeHtml(signal?.title || 'Recorded project evidence')}</strong><small>${escapeHtml(signal?.detail || recommendation.reason)}${signal?.observedAt ? ` · ${escapeHtml(humanDateTime(signal.observedAt))}` : ''}</small></div><b aria-hidden="true">→</b><div><span>RECOMMENDED NEXT STEP</span><strong>${escapeHtml(recommendation.title)}</strong><small>${escapeHtml(recommendation.reason)}</small></div><div class="decision-evidence"><span>EVIDENCE</span><strong>${escapeHtml(sourceNames.join(' · ') || 'Evidence source unavailable')}</strong><small>${Math.round(finiteNumber(recommendation.confidence) * 100)}% confidence · ${escapeHtml(intelligence.coverage?.caution || 'Missing evidence is unknown.')}</small></div>` : '<div><span>RECOMMENDATION</span><strong>No evidence-backed next step is available</strong><small>Connect or refresh project evidence before deciding.</small></div>';
  document.querySelectorAll('[data-graph-mode]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.graphMode === graphMode)));
  document.querySelectorAll('[data-graph-layout]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.graphLayout === graphLayout)));
  document.querySelectorAll('[data-density]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.density === graphDensity)));
  const matchingNodes = state.graph.nodes.filter(node => graphSets[graphMode].includes(node.type));
  let nodes = matchingNodes.slice(0, 80);
  const clientTruncated = matchingNodes.length > nodes.length;
  if (graphLayout === 'focus' && graphSelection) {
    const nearby = new Set([graphSelection]);
    state.graph.edges.forEach(edge => { if (edge.source === graphSelection || edge.target === graphSelection) { nearby.add(edge.source); nearby.add(edge.target); } });
    nodes = nodes.filter(node => nearby.has(node.id));
  }
  const nodeIds = new Set(nodes.map(node => node.id));
  const edges = state.graph.edges.filter(edge => nodeIds.has(edge.source) && nodeIds.has(edge.target));
  const typeCounts = nodes.reduce((result, node) => ({...result, [node.type]: (result[node.type] || 0) + 1}), {});
  const layoutCopy = {story: 'Read left to right: purpose, work, then evidence.', flow: 'See current work move toward proof or a blocker.', timeline: 'See tasks from oldest to newest; evidence stays beside its task.', focus: graphSelection ? 'Only the selected item and its closest evidence are visible.' : 'Select a node, then Focus, to remove everything unrelated.'};
  const truncation = state.graph.bounds?.truncated || clientTruncated ? ` Showing a bounded ${nodes.length}-node evidence view.` : '';
  const caution = state.graph.guidance?.caution || state.intelligence?.coverage?.caution || 'Some project-intelligence evidence is unavailable; missing data is unknown.';
  document.querySelector('#graphSummary').textContent = `${layoutCopy[graphLayout]} ${typeCounts.source || 0} source(s), ${typeCounts.signal || 0} signal(s), ${typeCounts.recommendation || 0} recommendation, ${typeCounts.task || 0} task(s), and ${typeCounts.validation || 0} proof record(s).${truncation} ${caution}`;
  const narrowGraph = window.innerWidth < 650;
  const availableWidth = svg.parentElement.clientWidth || (narrowGraph ? window.innerWidth - 40 : 860);
  let width = narrowGraph ? Math.max(280, Math.min(availableWidth, window.innerWidth - 32)) : Math.max(860, availableWidth * graphZoom);
  const nodeWidth = graphDensity === 'compact' ? 166 : 190;
  const nodeHeight = graphDensity === 'compact' ? 50 : 58;
  const gap = graphDensity === 'compact' ? 66 : 86;
  const positions = new Map(); const counts = {};
  if (graphLayout === 'timeline') {
    const tasks = nodes.filter(node => node.type === 'task').sort((a, b) => String(a.updatedAt).localeCompare(String(b.updatedAt)));
    const columns = Math.max(1, Math.floor((width - 60) / (nodeWidth + 55)));
    const columnWidth = (width - 60) / columns;
    const rowHeight = graphDensity === 'compact' ? 280 : 340;
    tasks.forEach((node, index) => positions.set(node.id, {x: 30 + (index % columns) * columnWidth, y: 55 + Math.floor(index / columns) * rowHeight}));
    nodes.filter(node => node.type !== 'task').forEach(node => { const edge = edges.find(item => item.source === node.id || item.target === node.id); const taskId = edge && [edge.source, edge.target].find(id => id.startsWith('task:')); const taskPosition = positions.get(taskId); const index = counts[taskId] || 0; counts[taskId] = index + 1; positions.set(node.id, {x: taskPosition?.x || 30, y: (taskPosition?.y || 55) + 88 + index * gap}); });
  } else if (graphLayout === 'flow') {
    width = Math.max(width, 1200);
    const statusLane = {proposed: .52, active: .52, paused: .52, blocked: .52, needs_validation: .52, completed: .74, cancelled: .74};
    const typeLane = {source: .02, signal: .26, recommendation: .52, theme: .02, session: .02, blocker: .76, validation: .76, commit: .76};
    nodes.forEach(node => { const lane = node.type === 'task' ? (statusLane[node.status] ?? .52) : (typeLane[node.type] ?? .76); const key = `${lane}:${node.type}`; const index = counts[key] || 0; counts[key] = index + 1; positions.set(node.id, {x: width * lane, y: 46 + index * gap}); });
  } else {
    const lane = {source: .02, theme: .02, session: .02, signal: .25, recommendation: .49, task: .72, blocker: .72, validation: .72, commit: .72};
    nodes.forEach(node => { const index = counts[node.type] || 0; counts[node.type] = index + 1; positions.set(node.id, {x: width * lane[node.type], y: 34 + index * gap + (node.type === 'session' ? 280 : 0)}); });
  }
  if (narrowGraph) {
    const typeOrder = {source: 0, signal: 1, recommendation: 2, task: 3, blocker: 4, validation: 4, commit: 4, theme: 5, session: 5};
    nodes.sort((a, b) => (typeOrder[a.type] ?? 6) - (typeOrder[b.type] ?? 6));
    nodes.forEach((node, index) => positions.set(node.id, {x: Math.max(12, (width - nodeWidth) / 2), y: 28 + index * gap}));
  }
  const height = Math.max(500, ...[...positions.values()].map(position => position.y + nodeHeight + 32));
  const connected = new Set(); edges.forEach(edge => { if (edge.source === graphSelection || edge.target === graphSelection) { connected.add(edge.source); connected.add(edge.target); } });
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`); svg.setAttribute('height', height);
  svg.style.height = `${height}px`;
  svg.style.width = narrowGraph ? '100%' : `${Math.max(100, graphZoom * 100)}%`;
  svg.innerHTML = `<defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0L10 5L0 10z"/></marker></defs>${edges.map(edge => { const a = positions.get(edge.source); const b = positions.get(edge.target); const dim = graphSelection && edge.source !== graphSelection && edge.target !== graphSelection; const path = narrowGraph ? `M${a.x + nodeWidth / 2},${a.y + nodeHeight} C${a.x + nodeWidth / 2},${a.y + nodeHeight + 22} ${b.x + nodeWidth / 2},${b.y - 22} ${b.x + nodeWidth / 2},${b.y}` : `M${a.x + nodeWidth},${a.y + nodeHeight / 2} C${a.x + nodeWidth + 36},${a.y + nodeHeight / 2} ${b.x - 34},${b.y + nodeHeight / 2} ${b.x},${b.y + nodeHeight / 2}`; return `<path class="map-edge ${dim ? 'dimmed' : 'connected'}" marker-end="url(#arrow)" d="${path}"/>`; }).join('')}${nodes.map(node => { const position = positions.get(node.id); const dim = graphSelection && node.id !== graphSelection && !connected.has(node.id); const icon = {source: '◉', signal: '!', recommendation: '→', task: '◆', theme: '◇', session: '●', blocker: '!', validation: '✓', commit: '⌁'}[node.type] || '•'; const status = node.status ? `, ${label(node.status)}` : ''; return `<g data-node-id="${escapeHtml(node.id)}" class="map-node type-${escapeHtml(node.type)} status-${escapeHtml(node.status)} ${node.id === graphSelection ? 'selected' : ''} ${dim ? 'dimmed' : ''}" tabindex="0" role="button" aria-label="${escapeHtml(`${label(node.type)}: ${node.label}${status}`)}" transform="translate(${position.x},${position.y})"><rect width="${nodeWidth}" height="${nodeHeight}"/><text class="node-icon" x="13" y="22">${icon}</text><text x="32" y="22">${escapeHtml(node.label.slice(0, graphDensity === 'compact' ? 19 : 24))}</text><text class="node-meta" x="13" y="${nodeHeight - 13}">${escapeHtml(node.type)}${node.status ? ` · ${escapeHtml(label(node.status))}` : ''}</text></g>`; }).join('')}`;
  document.querySelector('#mapFallback').textContent = nodes.length ? nodes.map(node => `${label(node.type)} ${node.label}${node.status ? `: ${label(node.status)}` : ''}`).join('; ') : `No ${graphMode} nodes are available in the bounded graph.`;
  renderGraphDetail(nodes.length);
};

function renderEvidenceCalendar() {
  const days = mapData?.calendar?.days || [];
  const maximum = Math.max(1, ...days.map(item => finiteNumber(item.evidence)));
  const visibleDays = days.slice(-28);
  const selectedDate = mapData?.graph?.period === 'day' ? String(mapData.graph.periodStart).slice(0, 10) : '';
  document.querySelector('#evidenceCalendar').innerHTML = visibleDays.map((item,index) => {
    const level = Math.min(3, Math.ceil(finiteNumber(item.evidence) / maximum * 3));
    const date = new Date(`${item.date}T12:00:00`);
    const shortDate = date.toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
    const weekday = date.toLocaleDateString(undefined, {weekday: 'short'});
    return `<button class="evidence-day level-${level} ${item.attention ? 'attention' : ''} ${item.date === selectedDate ? 'selected' : ''}" data-map-day="${escapeHtml(item.date)}" data-calendar-index="${index}" tabindex="${index===visibleDays.length-1?0:-1}" aria-pressed="${item.date === selectedDate}" aria-label="${escapeHtml(item.date)}: ${finiteNumber(item.evidence)} evidence record(s), ${finiteNumber(item.outcomes)} outcome(s), ${finiteNumber(item.attention)} attention signal(s), ${finiteNumber(item.automatic)} automatic observation(s)"><time datetime="${escapeHtml(item.date)}">${escapeHtml(shortDate)}</time><strong>${finiteNumber(item.evidence)}</strong><small>${escapeHtml(weekday)}</small></button>`;
  }).join('') || '<span class="empty-message">No recorded evidence in the last year.</span>';
  const range = visibleDays.length ? ` Showing ${visibleDays[0].date} through ${visibleDays[visibleDays.length - 1].date}; ${days.length} daily records are retained.` : '';
  document.querySelector('#calendarCaution').innerHTML = `<span>${escapeHtml(mapData?.calendar?.caution || 'Evidence density is context, not productivity.')}${escapeHtml(range)}</span><span class="calendar-legend" aria-label="Evidence density legend"><i class="level-0"></i>None <i class="level-1"></i>Low <i class="level-2"></i>Medium <i class="level-3"></i>High <i class="attention"></i>Needs attention</span>`;
}

function renderGraphDetail(visibleCount = 0) {
  const node = state.graph.nodes.find(item => item.id === graphSelection);
  if (!node) {
    const unavailable = state.graph.contractVersion === 2 ? [] : (state.intelligence?.sources || []).filter(source => source.status !== 'connected');
    document.querySelector('#graphDetail').innerHTML = visibleCount ? `<p class="eyebrow">HOW TO READ THIS</p><h2>Select a node</h2><p>Sources support signals; signals motivate recommendations; tasks and proof show the recorded outcome.</p>${unavailable.length ? `<p class="task-meta">Evidence not connected: ${unavailable.map(source => `${escapeHtml(source.label)} (${escapeHtml(label(source.status))})`).join(' · ')}</p>` : ''}` : `<p class="eyebrow">NO MATCHING NODES</p><h2>This view has no recorded evidence</h2><p>Choose another Map filter. Missing records are unknown, not proof that nothing happened.</p>`;
    return;
  }
  const relations = state.graph.edges.filter(edge => edge.source === node.id || edge.target === node.id).map(edge => { const otherId = edge.source === node.id ? edge.target : edge.source; const other = state.graph.nodes.find(item => item.id === otherId); return other ? `<li><span>${escapeHtml(relationshipName(edge.type))}</span><strong>${escapeHtml(other.label)}</strong></li>` : ''; }).join('');
  document.querySelector('#graphDetail').innerHTML = `<p class="eyebrow">SELECTED ${escapeHtml(node.type)}</p><h2>${escapeHtml(node.label)}</h2><span class="badge ${escapeHtml(node.status)}">${escapeHtml(label(node.status || node.type))}</span><ul class="relationship-list">${relations || '<li>No relationship recorded.</li>'}</ul><button class="quiet-button" id="clearGraphSelection">Reset focus</button>`;
  document.querySelector('#clearGraphSelection').addEventListener('click', () => { graphSelection = ''; renderMap(); });
}

renderReview = function () {
  if (!insightData) return;
  if (!document.querySelector('#reviewEvidence')) {
    document.querySelector('#weekChart').insertAdjacentHTML('beforebegin', '<p class="panel-summary">Select a period block to see the recorded events behind its totals.</p>');
    document.querySelector('#weekChart').insertAdjacentHTML('afterend', '<div id="reviewEvidence" class="review-evidence"><div class="empty-message"><strong>Select a period block</strong><span>Trace its total to recorded task and proof events.</span></div></div>');
  }
  const totals = insightData.totals;
  const previous = insightData.comparison?.totals || {};
  const needsAttention = ['active', 'blocked', 'needs_validation'].reduce((sum, status) => sum + (state.counts?.[status] || 0), 0);
  const change = (current, prior, lowerIsBetter = false) => { const delta=finiteNumber(current)-finiteNumber(prior); if(!delta)return 'same as previous comparable period'; const direction=(delta>0)!==lowerIsBetter?'more':'fewer'; return `${Math.abs(delta)} ${direction} than previous comparable period`; };
  const cards = [[totals.completedTasks, 'Outcomes completed', change(totals.completedTasks,previous.completedTasks)], [totals.totalEvents, 'Recorded activity', change(totals.totalEvents,previous.totalEvents)], [totals.passed, 'Proof passed', `${change(totals.passed,previous.passed)} · ${totals.failed} failed`], [needsAttention, 'Needs attention now', 'Current project state, not period performance']];
  document.querySelector('#reviewCards').innerHTML = cards.map(([value, title, hint]) => `<div class="review-card"><strong>${value}</strong><span>${title}</span><small>${hint}</small></div>`).join('');
  document.querySelector('#weekChart').innerHTML = insightData.buckets.map(bucket => `<button data-review-bucket="${escapeHtml(bucket.id)}" aria-label="Inspect ${escapeHtml(bucket.label)}: ${bucket.totalEvents} recorded events"><strong>${bucket.totalEvents}</strong><span>${escapeHtml(bucket.label)}</span><small>${bucket.completed} done · ${bucket.blocked} blocked · ${bucket.other} other</small></button>`).join('');
  const attentionChange=change(finiteNumber(totals.blocked)+finiteNumber(totals.failed),finiteNumber(previous.blocked)+finiteNumber(previous.failed),true);
  document.querySelector('#weekStory').textContent = `${periodCopy[insightPeriod][1]}: ${totals.completedTasks} outcome(s) completed (${change(totals.completedTasks,previous.completedTasks)}), ${totals.passed} proof record(s) passed, and ${totals.blocked + totals.failed} attention signal(s) appeared (${attentionChange}). ${needsAttention} task(s) need attention now. Times use ${insightData.timeZone || userTimeZone}.`;
  document.querySelector('#weekOutcomes').innerHTML = insightData.completedTasks.map(task => `<li><span>✓</span><div><strong>${escapeHtml(task.title)}</strong><small>${escapeHtml(task.theme || 'Project')} · ${ago(task.completedAt)}</small></div></li>`).join('') || '<li>No completed outcome in this period.</li>';
};

function inspectReviewBucket(bucketId) {
  const bucket = insightData?.buckets.find(item => item.id === bucketId);
  if (!bucket) return;
  const events = (insightData.activity || []).filter(item => bucket.eventIds.includes(item.id));
  document.querySelectorAll('[data-review-bucket]').forEach(item => item.classList.toggle('selected', item.dataset.reviewBucket === bucketId));
  document.querySelector('#reviewEvidence').innerHTML = `<p class="eyebrow">${escapeHtml(bucket.label)} · ${bucket.totalEvents} RECORDED EVENT(S)</p>${events.length ? `<ul class="outcome-list">${events.map(item => `<li><button data-open-task="${escapeHtml(item.taskId)}"><strong>${escapeHtml(eventName(item.type))}</strong><small>${escapeHtml(item.taskTitle)} · ${escapeHtml(humanDateTime(item.occurredLocal || item.occurredAt))}</small></button></li>`).join('')}</ul>${events.length < bucket.totalEvents ? `<p class="panel-summary">Showing ${events.length} bounded event(s) of ${bucket.totalEvents}. Exact totals remain above.</p>` : ''}` : '<div class="empty-message"><strong>No bounded event detail</strong><span>The total is exact, but no matching event is present in the bounded detail response.</span></div>'}`;
}

renderIdeas = function () {
  const ideas=state.ideas||[];
  const groups=[['Needs a decision',ideas.filter(item=>item.status==='proposed')],['Accepted or planned',ideas.filter(item=>['accepted','planned'].includes(item.status))],['Closed decisions',ideas.filter(item=>['rejected','completed'].includes(item.status))]];
  document.querySelector('#ideaRows').innerHTML=groups.filter(([,items])=>items.length).map(([title,items])=>`<section class="idea-group"><div class="idea-group-head"><h2>${title}</h2><span>${items.length}</span></div>${items.map(item=>`<article class="panel idea-card"><div class="idea-card-head"><span class="badge ${escapeHtml(item.status)}">${escapeHtml(label(item.status))}</span><time>${escapeHtml(humanDateTime(item.generatedAt))}</time></div><h3>${escapeHtml(item.title)}</h3><p><strong>Why suggested:</strong> ${escapeHtml(item.reason)}</p><div class="idea-evidence"><strong>Recorded evidence</strong><ul>${(item.evidence||[]).map(evidence=>`<li>${escapeHtml(evidence)}</li>`).join('')||'<li>No supporting evidence recorded.</li>'}</ul></div><div class="idea-actions" aria-label="Decision for ${escapeHtml(item.title)}"><button class="row-action" data-idea-id="${escapeHtml(item.id)}" data-idea-status="accepted" ${item.status==='accepted'?'disabled':''}>Accept</button><button class="row-action" data-idea-id="${escapeHtml(item.id)}" data-idea-status="planned" ${item.status==='planned'?'disabled':''}>Plan</button><button class="row-action" data-idea-id="${escapeHtml(item.id)}" data-idea-status="rejected" ${item.status==='rejected'?'disabled':''}>Reject</button></div></article>`).join('')}</section>`).join('')||'<div class="empty-message"><strong>No evidence-backed idea yet.</strong><span>Ideas appear only when recorded project evidence supports a follow-up.</span></div>';
};

async function selectPeriod(period, requestedAnchor = insightAnchor) {
  const request = ++insightRequest;
  const previousPeriod = insightPeriod;
  const status = document.querySelector('#periodStatus');
  document.querySelector('#signalChart').setAttribute('aria-busy', 'true');
  status.textContent = 'Loading period…';
  try {
    const [nextData, nextMap] = await Promise.all([
      api(`/api/insights?period=${period}${requestedAnchor ? `&anchor=${requestedAnchor}` : ''}${timeZoneQuery}`),
      api(`/api/map?period=${period}${requestedAnchor ? `&anchor=${requestedAnchor}` : ''}${timeZoneQuery}`).catch(error => ({compatibilityError: error.message})),
    ]);
    if (request !== insightRequest) return false;
    insightPeriod = period; insightData = normalizePeriodInsights(nextData); insightAnchor = nextData.anchor;
    mapCompatibilityError = nextMap.compatibilityError ? 'This dashboard server is older than the Map UI. Close it and run Project Tasks again.' : '';
    mapData = nextMap.compatibilityError ? null : nextMap;
    if (mapCompatibilityError) state.graph = null;
    document.querySelectorAll('[data-period]').forEach(button => button.classList.toggle('active', button.dataset.period === period));
    status.textContent = mapCompatibilityError;
    document.querySelector('#mapTitle').textContent = 'Why this work is next';
    renderNow(); renderReview(); renderEvidenceCalendar();
    if (document.querySelector('#tab-map').classList.contains('active')) renderMap();
    return true;
  } catch (error) {
    if (request !== insightRequest) return false;
    insightPeriod = previousPeriod;
    status.textContent = `Could not load this period: ${error.message}`;
    return false;
  } finally {
    if (request === insightRequest) document.querySelector('#signalChart').removeAttribute('aria-busy');
  }
}

async function moveRange(direction) {
  const value = new Date(`${insightAnchor}T12:00:00`);
  let requestedAnchor = insightAnchor;
  if (direction === 0) requestedAnchor = '';
  else if (insightPeriod === 'day') value.setDate(value.getDate() + direction);
  else if (insightPeriod === 'week') value.setDate(value.getDate() + 7 * direction);
  else if (insightPeriod === 'month') { value.setDate(1); value.setMonth(value.getMonth() + direction); }
  else value.setFullYear(value.getFullYear() + direction);
  if (direction !== 0) requestedAnchor = localDate(value);
  await selectPeriod(insightPeriod, requestedAnchor);
}

async function askProjectGuide(question) {
  const answer = document.querySelector('#guideAnswer');
  answer.innerHTML = '<strong>Reading structured project evidence…</strong>';
  const result = await api('/api/coach', {method: 'POST', body: JSON.stringify({question, period: insightPeriod, anchor: insightAnchor})});
  answer.innerHTML = `<strong>${escapeHtml(result.explanation)}</strong><span>${escapeHtml(result.suggestion)}</span><ul>${result.evidence.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul><span>${escapeHtml(result.uncertainty)}</span><span class="guide-caution">${escapeHtml(result.caution)}</span><div class="guide-actions"><button class="quiet-button" id="copyAiContext">Copy AI context</button><button class="quiet-button" data-run-agent="codex">Open in Codex</button><button class="quiet-button" data-run-agent="claude">Open in Claude</button></div><span id="agentLaunchStatus" role="status"></span>`;
  document.querySelector('#copyAiContext').addEventListener('click', async () => {
    await navigator.clipboard.writeText(JSON.stringify(result.aiContext, null, 2));
    document.querySelector('#copyAiContext').textContent = 'Copied';
  });
  answer.querySelectorAll('[data-run-agent]').forEach(button => button.addEventListener('click', async () => {
    const provider = button.dataset.runAgent;
    if (!confirm(`Open ${provider} in a new terminal with read-only/plan permissions? The prepared question will be passed as a local process argument, will not be stored by Project Tasks, and may use your model allowance.`)) return;
    const target = document.querySelector('#agentLaunchStatus'); target.textContent = `Opening ${provider} in the Project Tasks terminal…`;
    button.disabled = true;
    try { await api('/api/coach/run', {method: 'POST', body: JSON.stringify({provider, question: result.question, period: result.period, anchor: result.anchor})}); target.textContent = `A terminal opened for ${provider} in the project folder. Check that terminal for CLI startup or authentication errors.`; }
    catch (error) { target.textContent = error.message; }
    finally { if (!target.textContent.includes('terminal opened')) button.disabled = false; }
  }));
  answer.querySelectorAll('[data-run-agent]').forEach(button => {
    const capability = result.agents[button.dataset.runAgent];
    button.disabled = !capability.available;
    if (!capability.available) button.title = capability.providerInstalled ? 'No supported terminal launcher found' : `${button.dataset.runAgent} CLI is not installed`;
  });
}

installTaskDateControls();
document.querySelector('#periodSwitch').addEventListener('click', event => { const button = event.target.closest('[data-period]'); if (button) selectPeriod(button.dataset.period); });
document.querySelector('#taskFilter').addEventListener('input', event => { taskRenderLimit = taskBatchSize; renderTasks(event.target.value); });
document.querySelector('#taskStatus').addEventListener('change', () => { taskRenderLimit = taskBatchSize; renderTasks(); });
document.querySelector('#taskSort').addEventListener('change', () => { taskRenderLimit = taskBatchSize; renderTasks(); });
document.querySelector('#taskDatePreset').addEventListener('change',event=>{const days=Number(event.target.value);if(days){const to=new Date();const from=new Date();from.setDate(to.getDate()-days+1);document.querySelector('#taskDateFrom').value=localDate(from);document.querySelector('#taskDateTo').value=localDate(to)}else if(event.target.value!=='custom'){document.querySelector('#taskDateFrom').value='';document.querySelector('#taskDateTo').value=''}taskRenderLimit=taskBatchSize;renderTasks()});
['taskDateFrom','taskDateTo'].forEach(id=>document.querySelector(`#${id}`).addEventListener('change',()=>{document.querySelector('#taskDatePreset').value='custom';taskRenderLimit=taskBatchSize;renderTasks()}));
document.querySelector('#clearTaskFilters').addEventListener('click', () => { document.querySelector('#taskFilter').value = ''; document.querySelector('#taskStatus').value = ''; document.querySelector('#taskSort').value = 'updated'; document.querySelector('#taskDatePreset').value=''; document.querySelector('#taskDateFrom').value=''; document.querySelector('#taskDateTo').value=''; taskRenderLimit = taskBatchSize; renderTasks(); document.querySelector('#taskFilter').focus(); });
document.querySelector('#taskShowMore').addEventListener('click', () => { const firstNew = taskRenderLimit; taskRenderLimit += taskBatchSize; renderTasks(); (document.querySelector(`#task-inspect-${firstNew}`) || document.querySelector('#taskResults')).focus(); });
document.querySelector('#taskRows').addEventListener('focusin', event => { const button = event.target.closest('.row-action'); if (button) button.classList.add('keyboard-focused'); });
document.querySelector('#taskRows').addEventListener('focusout', event => { const button = event.target.closest('.row-action'); if (button) button.classList.remove('keyboard-focused'); });
document.querySelector('#previousRange').addEventListener('click', () => moveRange(-1));
document.querySelector('#todayRange').addEventListener('click', () => moveRange(0));
document.querySelector('#nextRange').addEventListener('click', () => moveRange(1));
document.querySelector('#signalChart').addEventListener('click', event => { const point = event.target.closest('[data-bucket]'); if (point) inspectBucket(point.dataset.bucket); });
document.querySelector('#weekChart').addEventListener('click', event => { const point = event.target.closest('[data-review-bucket]'); if (point) inspectReviewBucket(point.dataset.reviewBucket); });
document.querySelector('#clearBucket').addEventListener('click', event => { document.querySelector('#bucketDetail').innerHTML = '<div class="empty-message"><strong>Select a chart point</strong><span>See the lifecycle events behind every value.</span></div>'; document.querySelectorAll('.signal-point').forEach(item => item.classList.remove('selected')); event.currentTarget.disabled = true; renderActivity(insightData.activity); });
document.body.addEventListener('click', event => {
  const task = event.target.closest('[data-open-task]');
  if (task) openTask(task.dataset.openTask).catch(error => { detailTitle.textContent = 'Unable to open task'; taskDetail.textContent = error.message; if (!taskDialog.open) taskDialog.showModal(); });
  const view = event.target.closest('[data-open-view]');
  if (view) { const index = ['now','tasks','map','review','ideas'].indexOf(view.dataset.openView); activateDashboardTab(index); if (view.dataset.viewFilter) { const filter = document.querySelector('#taskFilter'); filter.value = view.dataset.viewFilter; renderTasks(filter.value); filter.focus(); } }
  const recommendation = event.target.closest('[data-trace-recommendation]');
  if (recommendation) { graphSelection = `recommendation:${recommendation.dataset.traceRecommendation}`; activateDashboardTab(2); }
  const traceEvidence = event.target.closest('[data-trace-map-evidence]');
  if (traceEvidence) { graphMode = 'evidence'; graphLayout = 'flow'; graphSelection = ''; document.querySelectorAll('[data-graph-mode]').forEach(item => item.classList.toggle('active', item.dataset.graphMode === 'evidence')); document.querySelector('#graphFilters').scrollIntoView({behavior: 'smooth', block: 'start'}); renderMap(); }
});
document.querySelector('#graphFilters').addEventListener('click', event => { const button = event.target.closest('[data-graph-mode]'); if (!button) return; graphMode = button.dataset.graphMode; graphSelection = ''; document.querySelectorAll('[data-graph-mode]').forEach(item => item.classList.toggle('active', item === button)); renderMap(); });
document.body.addEventListener('click', event => {
  const layout = event.target.closest('[data-graph-layout]');
  if (layout) { graphLayout = layout.dataset.graphLayout; document.querySelectorAll('[data-graph-layout]').forEach(item => item.classList.toggle('active', item === layout)); renderMap(); return; }
  const density = event.target.closest('[data-density]');
  if (density) { graphDensity = density.dataset.density; document.querySelectorAll('[data-density]').forEach(item => item.classList.toggle('active', item === density)); renderMap(); return; }
  const zoom = event.target.closest('[data-zoom]');
  if (zoom) { graphZoom = zoom.dataset.zoom === 'reset' ? 1 : Math.min(1.8, Math.max(.75, graphZoom + (zoom.dataset.zoom === 'in' ? .15 : -.15))); renderMap(); }
});
document.querySelector('#taskMap').addEventListener('click', event => { const node = event.target.closest('[data-node-id]'); if (node) { graphSelection = node.dataset.nodeId; renderMap(); } });
document.querySelector('#taskMap').addEventListener('keydown', event => { const node = event.target.closest('[data-node-id]'); if (node && ['Enter', ' '].includes(event.key)) { event.preventDefault(); graphSelection = node.dataset.nodeId; renderMap(); } });
document.querySelector('#clearGraphSelection').addEventListener('click', () => { graphSelection = ''; renderMap(); });
document.querySelector('#evidenceCalendar').addEventListener('click', event => { const day=event.target.closest('[data-map-day]'); if(day)selectPeriod('day',day.dataset.mapDay); });
document.querySelector('#evidenceCalendar').addEventListener('keydown', event => { const day=event.target.closest('[data-calendar-index]'); if(!day)return; const delta={ArrowLeft:-1,ArrowRight:1,ArrowUp:-7,ArrowDown:7,Home:-365,End:365}[event.key]; if(delta===undefined)return; event.preventDefault(); const cells=[...document.querySelectorAll('[data-calendar-index]')]; const next=cells[Math.max(0,Math.min(cells.length-1,Number(day.dataset.calendarIndex)+delta))]; day.tabIndex=-1; next.tabIndex=0; next.focus(); });
document.querySelector('#mapAllHistory').addEventListener('click', async event => { event.currentTarget.disabled=true; try { mapData=await api(`/api/map?period=all${timeZoneQuery}`); graphSelection=''; document.querySelectorAll('[data-period]').forEach(button => button.classList.remove('active')); document.querySelector('#mapTitle').textContent='How this project evolved'; renderEvidenceCalendar(); renderMap(); document.querySelector('#periodStatus').textContent='Map shows bounded all-history evidence.'; } catch(error) { document.querySelector('#periodStatus').textContent=`Could not load all history: ${error.message}`; } finally { event.currentTarget.disabled=false; } });
document.querySelector('#guideForm').addEventListener('submit', event => { event.preventDefault(); const question = new FormData(event.currentTarget).get('question'); askProjectGuide(question).catch(error => { document.querySelector('#guideAnswer').textContent = error.message; }); });
document.querySelector('#guidePrompts').addEventListener('click', event => { const button = event.target.closest('[data-guide-question]'); if (button) { document.querySelector('#guideQuestion').value = button.dataset.guideQuestion; askProjectGuide(button.dataset.guideQuestion).catch(error => { document.querySelector('#guideAnswer').textContent = error.message; }); } });

Promise.all([api('/api/overview?activity=0'), api(`/api/insights?period=week${timeZoneQuery}`), api(`/api/map?period=week${timeZoneQuery}`).catch(error => ({compatibilityError: error.message}))]).then(([overview, insights, initialMap]) => {
  state = overview;
  insightData = normalizePeriodInsights(insights);
  mapCompatibilityError = initialMap.compatibilityError ? 'This dashboard server is older than the Map UI. Close it and run Project Tasks again.' : '';
  mapData = initialMap.compatibilityError ? null : initialMap;
  if (mapCompatibilityError) state.graph = null;
  insightAnchor = insights.anchor;
  renderNow(); renderTasks(); renderReview(); renderIdeas(); renderEvidenceCalendar();
  if (mapCompatibilityError) document.querySelector('#periodStatus').textContent = mapCompatibilityError;
  const requested = new URLSearchParams(location.search).get('view');
  const tab = document.querySelector(`.tab[data-view="${requested}"]`);
  if (tab) tab.click();
  else if (document.querySelector('#tab-map').classList.contains('active')) renderMap();
}).catch(error => {
  document.querySelector('#nowTitle').textContent = 'Unable to load Project Tasks';
  document.querySelector('#purpose').textContent = error.message;
});
