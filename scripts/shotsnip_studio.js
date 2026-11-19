let studioJobsSignature='',studioPreviewSignature='';
function initStudio(){
  titles.studio='Create clip';titles.jobs='Work queue';
  const nav=document.querySelector('.shot-nav');
  for(const [id,label] of [['studio','Create clip'],['jobs','Work queue']]){const a=document.createElement('a');a.href='#'+id;a.textContent=label;nav.insertBefore(a,nav.children[2]);const o=document.createElement('option');o.value=id;o.textContent=label;$('mobileView').appendChild(o)}
  const overview=$('overview'),hero=document.createElement('div');hero.className='control-hero';hero.innerHTML='<div><span class="eyebrow">SHOTSNIP / CONTROL ROOM</span><h2>Catch the moment. Keep it moving.</h2><p>Your automatic pipeline and manual clips, together in one place.</p></div><div class="control-actions"><a class="primary" href="#studio">Create a clip</a><button id="autoNow">Create clip now</button><button id="autoPause"></button></div>';overview.prepend(hero);
  const ops=document.createElement('div');ops.className='ops-layout';hero.after(ops);ops.appendChild($('liveOps').closest('section'));
  const preview=document.createElement('section');preview.className='panel preview-stage';preview.id='latestPreview';ops.appendChild(preview);
  $('startWorker').hidden=true;$('stopWorker').hidden=true;
  $('autoNow').onclick=async()=>{const button=$('autoNow');button.disabled=true;try{await act('create_clip',{mode:'auto',publish:true,platforms:['youtube','facebook'],privacy:'public'});location.hash='jobs'}catch(e){fail(e)}finally{button.disabled=false}};
  $('autoPause').onclick=()=>act('set_scheduler',{enabled:!D.scheduler_config.enabled}).catch(fail);
  const updateForm=()=>{const mode=$('clipMode').value; $('clipUrlGroup').hidden=mode==='auto';$('clipUrl').required=mode!=='auto';$('clipRangesGroup').hidden=mode!=='exact';$('clipRanges').required=mode==='exact';$('clipCaptureGroup').hidden=mode==='exact';$('submitClip').textContent=$('clipOutcome').value==='publish'?'Queue clip & publish':'Create preview'};
  $('clipMode').onchange=updateForm;$('clipOutcome').onchange=updateForm;updateForm();
  $('createClipForm').onsubmit=async e=>{e.preventDefault();const b=$('submitClip');b.disabled=true;$('createFeedback').textContent='Adding your job…';try{const platforms=[];if($('clipYoutube').checked)platforms.push('youtube');if($('clipFacebook').checked)platforms.push('facebook');await act('create_clip',{mode:$('clipMode').value,url:$('clipUrl').value,ranges:$('clipRanges').value,hook:$('clipHook').value,language:$('clipLanguage').value,capture_seconds:Number($('clipCapture').value),publish:$('clipOutcome').value==='publish',platforms,privacy:$('clipPrivacy').value});$('createFeedback').textContent='Queued. Follow it in Work queue.';location.hash='jobs'}catch(err){$('createFeedback').textContent=err.message;fail(err)}finally{b.disabled=false}};
}
function renderStudio(){
  $('autoPause').textContent=D.scheduler_config.enabled?'Pause auto':'Resume auto';
  if(!$('autoStop')){const b=document.createElement('button');b.id='autoStop';b.textContent='Stop after stage';$('autoPause').after(b);b.onclick=()=>act('stop_auto_after_stage').catch(fail)}
  $('autoStop').disabled=!['capturing','analyzing','finishing'].includes(D.scheduler.phase)||(D.manual_jobs||[]).some(j=>j.status==='running');
  const jobs=D.manual_jobs||[];
  if(typeof renderQueue==='function')renderQueue();
  if(typeof renderInsights==='function')renderInsights();
  if(typeof renderLibraryPages==='function')renderLibraryPages();
  let current=jobs.find(j=>j.result?.file),file=current?.result.file,title=current?.result.title;
  if(!file){for(const run of D.runs||[]){const clip=(run.clips||[]).find(c=>c.finished);if(clip){file=clip.file;title=clip.title;break}}}
  const previewKey=JSON.stringify([file,title]);if(previewKey!==studioPreviewSignature){studioPreviewSignature=previewKey;$('latestPreview').innerHTML='<span class="eyebrow">LATEST FINISHED CLIP</span><h2>'+esc(title||'A preview will appear here')+'</h2>'+(file?'<video controls preload="metadata" src="/api/shotsnip/media?file='+encodeURIComponent(file)+'"></video>':'<div class="empty-state"><span class="empty-symbol">▷</span><p>Finished clips appear here.<br>The active run is shown in Live operations.</p></div>')+'<p class="shot-muted">This is saved output, not a live stream preview.</p><a href="#jobs">Open work queue →</a>'}
}
initStudio();


function renderAgentStudio(){
  const ag=D.agent||{},policy=D.policy||{},findings=ag.findings||[],issues=ag.errors||[],actions=ag.actions||[];
  const observed=(D.age_performance?.videos||[]).filter(v=>v.checkpoints?.['24']).length;
  $('agentMetrics').innerHTML=[['LEARNING',policy.learning_enabled?'Enabled':'Paused','Local source priorities'],['LAST REVIEW',ag.checked_at?date(ag.checked_at):'Not reviewed','Review snapshot, not a live diagnosis'],['CREATOR SIGNALS',findings.length,'Creators with supported comparisons'],['24H OBSERVATIONS',observed,'Uploads with a saved near-24h checkpoint']].map(([label,value,note])=>'<article><span class="eyebrow">'+label+'</span><strong>'+esc(value)+'</strong><span class="shot-muted">'+esc(note)+'</span></article>').join('');
  $('agentVerdict').textContent=findings.length?'Evidence available':'Building evidence';
  const next=ag.next_steps?.length?ag.next_steps:['Run a fresh review to inspect the current analytics and capture results.'];
  $('agentNext').innerHTML=next.map((text,i)=>'<div class="agent-next-item"><span class="step-number">'+String(i+1).padStart(2,'0')+'</span><div><h3>'+esc(i===0?'Next recommended step':'Keep in mind')+'</h3><p>'+esc(text)+'</p></div></div>').join('');
  $('agentSample').textContent=ag.sample_rule||'Comparisons need at least 6 linked uploads with near-24h observations, including 3 per creator.';
  $('agentFindings').innerHTML=findings.length?findings.map(f=>'<article class="agent-signal"><div><h3>'+esc(f.creator)+'</h3><p class="shot-muted">'+fmt(f.n)+' linked uploads · '+fmt(f.median_views_near_24_hours)+' median views near 24 hours</p></div><span class="agent-factor">'+fmt(f.priority)+'×</span></article>').join(''):'<div class="agent-empty"><span class="agent-orbit" aria-hidden="true"></span><h3>Let the results mature.</h3><p>No creator has a supported preference in the latest review. The agent needs linked uploads and comparable observation ages before it can rank sources.</p><a href="#performance">Explore video performance →</a></div>';
  $('agentLearningState').textContent=policy.learning_enabled?'Learning is on. Supported source adjustments can be applied during a review.':'Learning is paused. Reviews can still inspect the evidence.';
  $('agentWeights').innerHTML=Object.entries(policy.source_priorities||{}).map(([name,value])=>'<div class="agent-weight"><span>'+esc(name)+'</span><strong>'+fmt(value)+'×</strong></div>').join('')||'<p class="shot-muted">No learned source adjustments saved.</p>';
  $('agentActions').innerHTML=actions.length?actions.map(text=>'<div class="agent-action"><span class="agent-dot"></span><p>'+esc(text)+'</p></div>').join(''):'<p class="shot-muted">'+(ag.checked_at?'No automatic strategy changes recorded in the latest review.':'Run a review to see its findings and any supported changes.')+'</p>';
  $('agentComparison').textContent=ag.comparison||'Compare observed views at similar upload ages. Associations do not establish causal impact.';
  $('agentIssues').innerHTML=issues.length?issues.slice(0,5).map(row=>'<div class="agent-issue"><h3>'+esc(row.creator||'Capture run')+'</h3><p class="shot-muted">'+esc(captureFailureDate(row.run))+'</p><p>'+esc((Array.isArray(row.errors)?row.errors:[row.errors]).filter(Boolean).join(' · '))+'</p><span class="shot-muted">'+esc(row.run)+'</span></div>').join(''):'<p class="shot-muted">'+(ag.checked_at?'No past capture failures listed in this review snapshot.':'Past capture failures appear here after a review.')+'</p>';
  $('agentReview').disabled=!!D.job?.running;$('agentReview').textContent=D.job?.running?'Working…':'Run a fresh review';
  $('agentReview').onclick=()=>act('review').catch(fail);
  document.querySelectorAll('[data-prompt]').forEach(button=>{button.onclick=()=>{$('chatQuestion').value=button.dataset.prompt;$('chatQuestion').focus()}});
}

function captureFailureDate(run){
  const match=/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})-/.exec(run||'');
  if(!match)return 'Capture date unavailable';
  const day=new Date(Number(match[1]),Number(match[2])-1,Number(match[3]));
  return day.toLocaleDateString(undefined,{day:'numeric',month:'short',year:'numeric'})+' · '+match[4]+':'+match[5]+':'+match[6]+' laptop time';
}
