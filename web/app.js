const money = n => `$${Number(n).toFixed(6)}`;
const percent = n => `${(Number(n) * 100).toFixed(1)}%`;
let report;
function el(tag, cls, value) { const node=document.createElement(tag); if(cls) node.className=cls; node.textContent=value; return node; }
function render() {
  const optimized=report.optimized, baseline=report.baseline;
  const metrics=[
    ['EST. COST REDUCTION',percent(optimized.estimated_cost_reduction_rate),'Modeled token charges'],
    ['QUALITY RETENTION',percent(optimized.quality_retention_vs_baseline),`${optimized.pass_count}/${report.n} vs ${baseline.pass_count}/${report.n} exact checks`],
    ['CHEAPER MODEL ROUTES',`${optimized.model_mix['gpt-4.1-mini']}/${report.n}`,'Only bounded task types'],
    ['EST. ROUTED SPEND',money(optimized.estimated_cost_usd),`Baseline ${money(baseline.estimated_cost_usd)}`]
  ];
  const metricsNode=document.querySelector('#metrics');
  for(const [label,value,sub] of metrics){const card=el('article','metric');card.append(el('span','',label),el('b','',value),el('small','',sub));metricsNode.append(card);}
  document.querySelector('#method').textContent=`${report.benchmark_id} · n=${report.n} · ${report.scoring}.`;
  document.querySelector('#price-date').textContent=report.price_source_checked_on;
  const filter=document.querySelector('#task-filter');
  [...new Set(report.rows.map(x=>x.type))].forEach(x=>filter.append(new Option(x,x)));
  function fill(){const body=document.querySelector('#rows');body.replaceChildren();for(const row of report.rows.filter(x=>filter.value==='all'||x.type===filter.value)){const tr=document.createElement('tr');for(const [value,cls] of [[row.id,''],[row.type,''],[row.selected_model,'model'],[row.optimized_pass?'PASS':'FAIL',row.optimized_pass?'pass':''],[money(row.baseline_cost_usd),''],[money(row.optimized_cost_usd),''],[row.route_reason,'reason']])tr.append(el('td',cls,value));body.append(tr);}}
  filter.addEventListener('change',fill);fill();
}
fetch('./data/benchmark-report.json').then(r=>{if(!r.ok)throw Error('report unavailable');return r.json();}).then(x=>{report=x;render();}).catch(()=>document.querySelector('#metrics').append(el('p','warning','Benchmark report could not be loaded. Run the benchmark generator and publish the data folder.')));
