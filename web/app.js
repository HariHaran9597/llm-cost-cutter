const $ = selector => document.querySelector(selector);
const app = { config: null, token: sessionStorage.getItem('costcutter-token') || '', currentRun: null, availableModelCount: null };
const sampleCases = [
  {id:'ticket-01',input:'My card was charged twice for one order.',expected:'billing'},
  {id:'ticket-02',input:'The tracking page says my parcel is delayed.',expected:'delivery'},
  {id:'ticket-03',input:'I cannot reset the password for my account.',expected:'account'}
];
const samplePrompt = 'Classify the customer support message as exactly one of: billing, delivery, account. Return only the category.';
$('#cases').value = sampleCases.map(row => JSON.stringify(row)).join('\n');

function fmtMoney(value, digits=6) { return `$${Number(value || 0).toFixed(digits)}`; }
function node(tag, className, text) { const item=document.createElement(tag); if(className)item.className=className; if(text!==undefined)item.textContent=text; return item; }
function detailsError(data) { return typeof data.detail === 'string' ? data.detail : data.detail?.message || JSON.stringify(data.detail || data); }

async function api(path, options={}) {
  const headers={...(options.headers||{})};
  if(app.token) headers.Authorization=`Bearer ${app.token}`;
  if(options.body) headers['Content-Type']='application/json';
  const response=await fetch(path,{...options,headers});
  const data=await response.json().catch(()=>({detail:`HTTP ${response.status}`}));
  if(!response.ok) throw new Error(detailsError(data));
  return data;
}

function addVariant(seed={}) {
  const count=$$('.variant-card').length;
  if(count>=4)return;
  const id=seed.id||`variant-${count+1}`;
  const card=node('article','variant-card');
  card.dataset.variantId=id;
  const head=node('div','variant-head');
  const name=document.createElement('input');name.className='variant-name';name.value=seed.name||`Candidate ${count+1}`;name.maxLength=80;name.setAttribute('aria-label','Candidate name');head.append(name);
  if(count>1){const remove=node('button','text-button','Remove');remove.type='button';remove.addEventListener('click',()=>{card.remove();updateEstimate();});head.append(remove);}
  const modelLabel=node('label','','Model');
  const model=document.createElement('select');model.className='variant-model';
  const models=Object.keys(app.config?.models||{});
  for(const value of models)model.add(new Option(value,value));
  model.value=seed.model&&models.includes(seed.model)?seed.model:(models[Math.min(count,models.length-1)]||'');
  model.addEventListener('change',updateEstimate);modelLabel.append(model);
  const promptLabel=node('label','','System prompt');
  const prompt=document.createElement('textarea');prompt.className='variant-prompt';prompt.rows=3;prompt.maxLength=4000;prompt.value=seed.system_prompt||samplePrompt;prompt.addEventListener('input',updateEstimate);promptLabel.append(prompt);
  const userLabel=node('label','','User template');
  const template=document.createElement('input');template.className='variant-template';template.value=seed.user_template||'{{input}}';template.maxLength=8000;template.addEventListener('input',updateEstimate);userLabel.append(template);
  card.append(head,modelLabel,promptLabel,userLabel);$('#variants').append(card);updateEstimate();
}
function $$(selector){return [...document.querySelectorAll(selector)];}

function readCases() {
  const rows=$('#cases').value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean).map((line,index)=>{
    let value;try{value=JSON.parse(line);}catch{throw new Error(`Dataset line ${index+1} is not valid JSON.`);}
    if(typeof value.id!=='string'||typeof value.input!=='string'||typeof value.expected!=='string')throw new Error(`Dataset line ${index+1} needs string fields: id, input, expected.`);
    return value;
  });
  if(rows.length<1||rows.length>2000)throw new Error('Use between 1 and 2,000 dataset rows.');
  return rows;
}
function readVariants() {
  const variants=$$('.variant-card').map((card,index)=>({
    id:card.dataset.variantId,
    name:card.querySelector('.variant-name').value,
    model:card.querySelector('.variant-model').value,
    system_prompt:card.querySelector('.variant-prompt').value,
    user_template:card.querySelector('.variant-template').value
  }));
  if(variants.length<2)throw new Error('Add at least two candidates.');
  if(variants.some(v=>!v.model||!v.system_prompt||!v.user_template.includes('{{input}}')))throw new Error('Each candidate needs a model, system prompt, and user template containing {{input}}.');
  return variants;
}
function updateEstimate() {
  if(!app.config)return;
  let cases,variants;try{cases=readCases();variants=readVariants();}catch{$('#estimate').textContent='Fix the dataset or candidates to estimate this run.';return;}
  const scorer=$('#scorer').value, outputTokens=Number($('#max-tokens').value)||128;
  const tokenGuess=text=>Math.ceil(text.length/3.2)+8;
  let cost=0;
  for(const variant of variants)for(const row of cases){
    const rate=app.config.models[variant.model];
    const inputTokens=tokenGuess(variant.system_prompt)+tokenGuess(variant.user_template.replaceAll('{{input}}',row.input));
    cost+=(inputTokens*rate.input+outputTokens*rate.output)/1_000_000;
    if(scorer==='judge'){
      const judge=app.config.models[$('#judge-model')?.value||app.config.judge_model];
      const judgeInput=tokenGuess(`${$('#rubric').value} ${row.input} ${row.expected} `+'x'.repeat(outputTokens*3));
      if(judge)cost+=(judgeInput*judge.input+128*judge.output)/1_000_000;
    }
  }
  $('#estimate').textContent=`Preflight estimate: ${fmtMoney(cost,5)} · selected cap ${fmtMoney($('#budget').value,3)}`;
}

function renderPrices() {
  const prices=$('#model-prices');prices.replaceChildren();
  for(const [model,rate] of Object.entries(app.config.models)){
    const inputTokens=Number($('#calc-input')?.value||0),outputTokens=Number($('#calc-output')?.value||0);
    const perRequest=(inputTokens*rate.input+outputTokens*rate.output)/1_000_000;
    const item=node('div','price-item');item.append(node('code','',`${model} · ${rate.provider}`),node('span','',`$${rate.input} / $${rate.output} per MTok · ${fmtMoney(perRequest*1000,4)} / 1k`));prices.append(item);
  }
  $('#price-date').textContent=app.config.prices_checked_on;
  const judge=$('#judge-model');if(!judge.options.length){
    for(const model of Object.keys(app.config.models))judge.add(new Option(model,model));
    judge.value=Object.hasOwn(app.config.models,app.config.judge_model)?app.config.judge_model:(Object.keys(app.config.models)[0]||'');
  }
  if(!$('#variants').children.length){
    addVariant({id:'baseline',name:'Baseline — GPT Luna',model:'gpt-6-luna'});
    addVariant({id:'claude-haiku',name:'Claude Haiku',model:'claude-haiku-5-5'});
    addVariant({id:'kimi-k26',name:'Kimi K2.6',model:'kimi-k2.6'});
    addVariant({id:'groq-qwen',name:'Groq Qwen 3.8 27B',model:'groq-qwen3.8-27b'});
  }
}

async function connect() {
  if(!app.config)try{app.config=await api('./api/config');renderPrices();}catch(error){
    try{app.config=await (await fetch('./data/pricing.json')).json();renderPrices();}catch{}
    $('#connection-state').textContent='Public benchmark and cost estimator are available here. For live BYOK runs, clone the repo and start the local FastAPI app.';
    $('#connection-state').className='status warning-text';$('#run').disabled=true;
    $('#model-status').textContent='Published list prices; enter token volumes to estimate provider charges.';
    return;
  }
  if(!app.token){$('#connection-state').textContent='API ready. Enter the gateway token from your local .env.';$('#run').disabled=true;return;}
  try{
    await api('./api/experiments');
    $('#model-status').textContent='Select a priced model and enter that provider’s BYOK key above. Model access is checked by the provider at call time.';
    $('#connection-state').textContent=app.config.demo_mode?'Connected · set LLMCUTTER_DEMO_MODE=false locally to enable BYOK runs':'Connected · choose models from OpenAI, Anthropic, Moonshot/Kimi, and Groq';
    $('#connection-state').className=app.config.live_enabled?'status good':'status warning-text';
    $('#run').disabled=!!app.config.demo_mode;
    await Promise.all([refreshHistory(),refreshPolicies()]);
  }catch(error){
    $('#connection-state').textContent=`Token not accepted: ${error.message}`;$('#connection-state').className='status warning-text';$('#run').disabled=true;
  }
}

async function checkAvailableModels(){
  try{
    const available=await api('./api/models/available');
    if(available.verified){
      app.config.models=available.models;app.availableModelCount=Object.keys(available.models).length;
      $('#model-status').textContent=app.availableModelCount?`${available.message} ${app.availableModelCount} priced model(s) are available.`:`No available provider models have configured prices. Add a rate entry in config/pricing.json. Unpriced account models: ${available.unpriced_models.join(', ')||'none returned'}.`;
      if(app.availableModelCount)app.config.judge_model=Object.hasOwn(available.models,app.config.judge_model)?app.config.judge_model:Object.keys(available.models)[0];
      renderPrices();
    }else{
      app.availableModelCount=null;$('#model-status').textContent=`${available.message} Using the configured local model list.`;
    }
  }catch(error){app.availableModelCount=null;$('#model-status').textContent=`Could not verify model access: ${error.message}. Using the configured local model list.`;}
}

function setBusy(busy){$('#run').disabled=busy||!!app.config?.demo_mode;$('#run').textContent=busy?'Running candidate matrix…':'Run live comparison';}

async function runExperiment() {
  let cases,variants;try{cases=readCases();variants=readVariants();}catch(error){alert(error.message);return;}
  const provider_api_keys={};
  for(const [provider,id] of [['openai','key-openai'],['anthropic','key-anthropic'],['moonshot','key-moonshot'],['groq','key-groq']])if($(`#${id}`).value.trim())provider_api_keys[provider]=$(`#${id}`).value.trim();
  const body={name:$('#experiment-name').value,task_type:$('#task-type').value,cases,variants,scorer:$('#scorer').value,judge_model:$('#judge-model').value,rubric:$('#rubric').value||null,max_output_tokens:Number($('#max-tokens').value),max_budget_usd:Number($('#budget').value),provider_api_keys};
  if(body.scorer==='judge'&&!body.rubric){alert('Write a rubric before using LLM judge scoring.');return;}
  const rough=$('#estimate').textContent;
  if(!confirm(`${rough}\n\nThis sends your cases and candidate prompts to the configured provider. Continue?`))return;
  setBusy(true);$('#connection-state').textContent='Experiment running. Each model receives the same cases; this can take a little while.';
  try{app.currentRun=await api('./api/experiments/run',{method:'POST',body:JSON.stringify(body)});renderRun(app.currentRun);await Promise.all([refreshHistory(),refreshPolicies()]);$('#connection-state').textContent=`Connected · completed ${app.currentRun.rows.length} candidate/case runs · estimated charges ${fmtMoney(app.currentRun.estimated_total_cost_usd,6)}`;}
  catch(error){$('#connection-state').textContent=`Run stopped: ${error.message}`;$('#connection-state').className='status warning-text';}
  finally{for(const id of ['key-openai','key-anthropic','key-moonshot','key-groq'])$(`#${id}`).value='';setBusy(false);}
}

function renderRun(run) {
  if(!run._cases)run._cases=readCases();
  $('#result-section').classList.remove('hidden');$('#result-title').textContent=run.name;
  const rec=run.report.variants.find(x=>x.id===run.report.recommendation);
  const box=$('#result-summary');box.replaceChildren();
  const base=run.report.variants.find(x=>x.id===run.report.baseline_variant_id);
  const cards=[['Baseline',base?.name||'—'],['Recommended',rec?.name||'Keep baseline'],['Cases',`${base?.scored_cases||0}/${base?.cases||0} scored`],['Total estimated charges',fmtMoney(run.estimated_total_cost_usd,6)]];
  const grid=node('div','result-metrics');for(const [label,value] of cards){const item=node('div','result-metric');item.append(node('span','',label),node('b','',value));grid.append(item);}box.append(grid,node('p','muted',run.report.recommendation_rule));
  const table=$('#summary-rows');table.replaceChildren();
  for(const summary of run.report.variants){
    const tr=document.createElement('tr');
    const ci=summary.quality_ci_95?` [${(summary.quality_ci_95[0]*100).toFixed(1)}–${(summary.quality_ci_95[1]*100).toFixed(1)}%]`:'';
    const quality=summary.quality_rate===null?'Not scored':`${summary.passed}/${summary.scored_cases} (${(summary.quality_rate*100).toFixed(1)}%)${ci}`;
    const values=[summary.name,summary.model,quality,fmtMoney(summary.estimated_cost_per_1000_requests_usd,4),`${summary.p50_latency_ms.toFixed(0)} / ${summary.p95_latency_ms.toFixed(0)}`,String(summary.errors)];
    for(const value of values)tr.append(node('td','',value));
    const action=node('td','');
    const promote=node('button','button tiny',summary.id===run.report.recommendation?'Promote recommended':'Promote');promote.type='button';promote.disabled=summary.errors>0||summary.quality_rate===null;promote.addEventListener('click',()=>promoteVariant(run.id,summary.id));action.append(promote);tr.append(action);table.append(tr);
  }
  const details=$('#case-results');details.replaceChildren();
  for(const row of run.rows){
    const variant=run.report.variants.find(x=>x.id===row.variant_id);const item=node('details','case-card');
    const score=row.score===null?'Manual review':run.scorer==='judge'?`${row.score}/5`:`${row.score?'PASS':'FAIL'}`;
    item.append(node('summary','',`${row.case_id} · ${variant?.name||row.variant_id} · ${score} · est. ${fmtMoney(row.estimated_total_cost_usd,7)} · ${row.latency_ms.toFixed(0)} ms`));
    const content=node('div','case-content');content.append(node('p','',`Expected: ${run._cases.find(c=>c.id===row.case_id)?.expected||'see saved run'}`),node('p','',`Actual: ${row.output||row.error||'No output'}`),node('p','muted',`${row.input_tokens} input tokens · ${row.output_tokens} output tokens · ${row.provider_usage_reported?'provider-reported token counts':'estimated token counts'} · ${row.score_reason||''}`));item.append(content);details.append(item);
  }
  // Keep cases alongside the exportable in-memory run for this view only.
  $('#result-section').scrollIntoView({behavior:'smooth',block:'start'});
}

async function promoteVariant(experimentId,variantId) {
  try{const result=await api('./api/policies/promote',{method:'POST',body:JSON.stringify({experiment_id:experimentId,variant_id:variantId})});await refreshPolicies();alert(`Promoted ${result.policy.model} for task key “${result.policy.task_type}”. Call the gateway with x-costcutter-task: ${result.policy.task_type}`);}
  catch(error){alert(`Promotion failed: ${error.message}`);}
}
async function refreshHistory() {
  const list=$('#history-list');
  try{const runs=await api('./api/experiments');list.replaceChildren();if(!runs.length){list.append(node('p','muted','No runs yet. Your experiment history appears here after a live comparison.'));return;}
    for(const run of runs){const item=node('div','history-item'),open=node('button','history-open',`${run.name} · ${run.task_type} · ${new Date(run.created_at).toLocaleString()}`);open.type='button';open.addEventListener('click',async()=>{try{const full=await api(`./api/experiments/${run.id}`);renderRun({id:full.id,name:full.name,scorer:full.scorer,report:full.results.report,rows:full.results.rows,preflight_estimate_usd:full.results.preflight_estimate_usd,estimated_total_cost_usd:full.results.estimated_total_cost_usd,_cases:full.payload.cases});}catch(error){alert(error.message);}});const del=node('button','text-button','Delete');del.type='button';del.addEventListener('click',async()=>{if(confirm(`Delete local run “${run.name}” and its promoted policy?`)){await api(`./api/experiments/${run.id}`,{method:'DELETE'});await refreshHistory();await refreshPolicies();}});item.append(open,del);list.append(item);}
  }catch(error){list.replaceChildren(node('p','muted',error.message));}
}
async function refreshPolicies(){
  const list=$('#policies');try{const policies=await api('./api/policies');list.replaceChildren();if(!policies.length){list.append(node('p','muted','No policies promoted yet.'));return;}
    for(const policy of policies){const item=node('div','policy-item');item.append(node('b','',policy.task_type),node('span','',`${policy.model} · ${(policy.quality_rate*100).toFixed(0)}% quality · est. ${fmtMoney(policy.estimated_cost_per_case_usd,7)}/case`));list.append(item);}
  }catch(error){list.replaceChildren(node('p','muted',error.message));}
}

async function loadPublishedBenchmark(){
  try{
    const response=await fetch('./data/banking77-result.json');if(!response.ok)return;
    const result=await response.json();$('#public-benchmark').classList.remove('hidden');
    $('#public-benchmark-meta').textContent=`Run date ${result.run_date_utc} · pinned PolyAI official test split · ${result.dataset.evaluated_rows} sampled cases · ${result.dataset.sampling} · ${result.protocol.pricing_date} price card`;
    const rows=$('#public-benchmark-rows');rows.replaceChildren();
    for(const [model,metrics] of Object.entries(result.models)){
      const tr=document.createElement('tr'),ci=metrics.accuracy_ci_95_wilson;
      const values=[model,`${metrics.correct}/${metrics.cases} · ${(metrics.accuracy*100).toFixed(1)}% [${(ci[0]*100).toFixed(1)}–${(ci[1]*100).toFixed(1)}%]`,(metrics.macro_f1*100).toFixed(1)+'%',fmtMoney(metrics.estimated_cost_per_1000_requests_usd,4),String(metrics.p95_latency_ms_successes_only??'—'),String(metrics.errors)];
      for(const value of values)tr.append(node('td','',value));rows.append(tr);
    }
    const picker=$('#public-model');picker.replaceChildren();
    for(const model of Object.keys(result.models))picker.add(new Option(model,model));
    const renderItems=()=>{
      const list=$('#public-items');list.replaceChildren();
      for(const item of (result.items[picker.value]||[])){
        const row=node('details','case-card');row.append(node('summary','',`${item.passed?'PASS':'MISS'} · expected ${item.expected} · returned ${item.output??'provider error'}`));
        row.append(node('p','case-content',item.error?'Request failed; counted incorrect.':`Example ID ${item.case_id} · ${item.input_tokens} input tokens · ${item.output_tokens} output tokens · ${item.latency_ms} ms`));list.append(row);
      }
    };
    picker.addEventListener('change',renderItems);renderItems();
  }catch{}
}

$('#token-form').addEventListener('submit',event=>{event.preventDefault();app.token=$('#token').value.trim();if(app.token)sessionStorage.setItem('costcutter-token',app.token);else sessionStorage.removeItem('costcutter-token');connect();});
$('#token').value=app.token;
$('#add-variant').addEventListener('click',()=>{addVariant();updateEstimate();});
$('#run').addEventListener('click',runExperiment);
$('#scorer').addEventListener('change',()=>{$('#rubric-wrap').classList.toggle('hidden',$('#scorer').value!=='judge');updateEstimate();});
$('#cases').addEventListener('input',updateEstimate);$('#budget').addEventListener('input',updateEstimate);$('#max-tokens').addEventListener('input',updateEstimate);$('#rubric').addEventListener('input',updateEstimate);$('#judge-model').addEventListener('change',updateEstimate);
$('#refresh-history').addEventListener('click',()=>Promise.all([refreshHistory(),refreshPolicies()]));
$('#calc-input').addEventListener('input',renderPrices);$('#calc-output').addEventListener('input',renderPrices);
$('#load-banking77').addEventListener('click',async()=>{try{const response=await fetch('./data/banking77-385.jsonl');if(!response.ok)throw new Error(`HTTP ${response.status}`);$('#cases').value=await response.text();const labels=[...new Set(readCases().map(row=>row.expected))].sort();const prompt=`Classify the banking customer message into exactly one intent from the list below. Return only the exact intent label, with no punctuation or explanation.\nAllowed intents: ${labels.join(', ')}`;$$('.variant-prompt').forEach(field=>field.value=prompt);$('#max-tokens').value='32';$('#budget').value='1.00';$('#experiment-name').value='Banking77 · 5 examples per intent';$('#task-type').value='banking77-intent';$('#dataset-status').textContent='Loaded 385 stratified official test cases (5 per each of 77 intents), with a matching label prompt.';updateEstimate();}catch(error){$('#dataset-status').textContent=`Could not load sample: ${error.message}`;}});
$('#export').addEventListener('click',()=>{if(!app.currentRun)return;const blob=new Blob([JSON.stringify(app.currentRun,null,2)],{type:'application/json'});const link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download=`llmcost-experiment-${app.currentRun.id}.json`;link.click();URL.revokeObjectURL(link.href);});
connect();
loadPublishedBenchmark();
