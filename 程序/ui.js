'use strict';
(() => {
  const get = id => document.getElementById(id);
  const palette = {ink:'#243936', grid:'#e1e7e2', axis:'#798b83', blue:'#355f8b', green:'#15705a', red:'#b44d3f', gold:'#ad742d', violet:'#7b5a85', gap:'#818985'};
  const titles = ['指数价格位置','固定样本宽基 ETF 分组净申赎','股票宽基 ETF 每日及累计净申赎','三只沪深300 ETF 份额与指数','沪深两市融资余额','沪深300股息率与十年国债收益率'];
  const ordinals = ['①','②','③','④','⑤','⑥'];
  const financial = new Intl.NumberFormat('zh-CN', { maximumFractionDigits:2 });
  const precision = new Intl.NumberFormat('zh-CN', { maximumFractionDigits:6 });
  const number = Number.isFinite;
  const val = (x, unit='') => number(x) ? financial.format(x) + unit : '缺失';
  const detailed = x => number(x) ? precision.format(x) : (x ?? '缺失');
  const cards = [];
  const startBox = get('start-date'), endBox = get('end-date'), modeBox = get('position-mode');
  let ticket = 0;
  let debounce = 0;
  let pickerTarget = null;
  let shownMonth = null;
  let calendar = [];
  const presets=[25,250,'1y','3y','all'];
  const cardPresets=[['1y','近1年'],['3y','近3年'],['all','全部历史']];
  let chosenPreset=25;
  let topDatesEdited=false;

  function el(tag, className, txt) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (txt != null) node.textContent = txt;
    return node;
  }
  function setStatus(state, message, session = '') {
    const status = get('app-status');
    status.dataset.state = state;
    status.textContent = message;
    get('session-info').textContent = session;
  }
  function cardRangeControls(n) {
    const controls=el('div','card-date-range');
    controls.dataset.rangeCard=String(n);
    controls.setAttribute('role','group');
    controls.setAttribute('aria-label','图'+ordinals[n-1]+'的观察区间');
    for (const [side,label] of [['start','开始日期'],['end','结束日期']]) {
      const field=get(side+'-date').closest('.date-field').cloneNode(true);
      const prefix='chart'+n+'-'+side;
      const input=field.querySelector('input'), button=field.querySelector('button');
      input.id=prefix+'-date';
      field.querySelector('label').htmlFor=input.id;
      button.id=prefix+'-calendar';
      button.setAttribute('aria-label','选择图'+ordinals[n-1]+'的'+label);
      controls.append(field);
    }
    const reset=el('button','card-range-reset','跟随顶部');
    reset.type='button';
    reset.id='chart'+n+'-range-reset';
    reset.setAttribute('aria-pressed','true');
    const shortcuts=el('div','preset-group card-range-presets');
    shortcuts.setAttribute('role','group');
    shortcuts.setAttribute('aria-label','图'+ordinals[n-1]+'的快捷区间');
    for(const [key,label] of cardPresets) {
      const button=el('button','',label);
      button.type='button';
      button.id='chart'+n+'-preset-'+key;
      button.dataset.preset=key;
      button.setAttribute('aria-pressed','false');
      shortcuts.append(button);
    }
    const status=el('p','card-range-status');
    status.setAttribute('role','status');
    controls.append(reset,shortcuts,status);
    return controls;
  }
  function buildCards() {
    titles.forEach((title, i) => {
      const card = el('article', 'chart-card' + (i < 2 ? ' cross-section' : ''));
      card.id = 'chart' + (i + 1);
      const head = el('header', 'card-header');
      head.append(el('span','num',ordinals[i]), el('h2','',title));
      const coverage = el('p','coverage');
      const legend = el('div','legend');
      const pane = el('div','chart-pane');
      const plot = el('div','plot');
      plot.setAttribute('role', 'img');
      plot.setAttribute('aria-label', title + '。数值见下方数据明细。');
      const empty = el('div','plot-empty');
      empty.hidden = true;
      pane.append(plot, empty);
      const note = el('p','card-note');
      const source = el('p','card-note source');
      const details = el('details','data-details');
      const summary = el('summary','','查看数据明细');
      const scroll = el('div','table-scroll');
      details.append(summary, scroll);
      card.append(head, coverage);
      if (i>=2) card.append(cardRangeControls(i+1));
      if (i===2) card.append(get('flow-controls-template').content.cloneNode(true));
      if (i===5) card.append(get('yield-controls-template').content.cloneNode(true));
      card.append(legend, pane, note, source, details);
      get('chart-deck').append(card);
      const config = { card, coverage, legend, plot, empty, note, source, details, scroll, chart:null, table:null };
      if (i>=2) {
        config.range={start:get(card.id+'-start-date'),end:get(card.id+'-end-date'),
          reset:get(card.id+'-range-reset'),status:card.querySelector('.card-range-status'),
          presetButtons:[...card.querySelectorAll('.card-range-presets button')],chosenPreset:null,
          independent:false,revision:0,timer:0};
      }
      details.addEventListener('toggle', () => { if (details.open) renderTable(config); });
      cards.push(config);
    });
    if (typeof ResizeObserver === 'function') {
      const observer = new ResizeObserver(entries => {
        for (const entry of entries) {
          const item = cards.find(c => c.plot === entry.target);
          if (item?.chart && entry.contentRect.width) item.chart.resize();
        }
      });
      cards.forEach(item => observer.observe(item.plot));
    } else {
      window.addEventListener('resize', () => cards.forEach(item => item.chart?.resize()));
    }
  }
  function renderTable(card) {
    if (!card.table || !card.details.open) return;
    const [headers, rows] = card.table;
    const table = el('table'), thead = el('thead'), tbody = el('tbody'), head = el('tr');
    headers.forEach(h => head.append(el('th','',h)));
    thead.append(head);
    table.append(thead, tbody);
    card.scroll.replaceChildren(table);
    let displayed=0;
    let newestFirst=rows.length>150 && card.tableNewest!==false;
    const more=el('button','table-more');
    more.type='button';
    if(rows.length>150) {
      const order=el('button','table-order');
      order.type='button';
      const orderLabel=()=>{order.textContent=(newestFirst?'最新在前':'最早在前')+' · 切换日期排序';};
      orderLabel();
      order.addEventListener('click',()=>{
        newestFirst=!newestFirst;
        card.tableNewest=newestFirst;
        displayed=0;
        tbody.replaceChildren();
        orderLabel();
        appendPage();
      });
      card.scroll.prepend(order);
    }
    function appendPage() {
      const limit=Math.min(displayed+150,rows.length);
      const batch=document.createDocumentFragment();
      while(displayed<limit) {
        const line=el('tr');
        const idx=newestFirst?rows.length-displayed-1:displayed;
        rows[idx].forEach(cell=>line.append(el('td','',detailed(cell))));
        displayed++;
        batch.append(line);
      }
      tbody.append(batch);
      more.hidden=displayed===rows.length;
      more.textContent='再显示 '+Math.min(150,rows.length-displayed)+' 行（已显示 '+displayed+'/'+rows.length+' 行）';
    }
    more.addEventListener('click',appendPage);
    card.scroll.append(more);
    appendPage();
  }
  function legend(card, items) {
    card.legend.replaceChildren(...items.map(([label, color, kind]) => {
      const item = el('span','legend-item');
      const swatch = el('i','legend-swatch ' + (kind || ''));
      swatch.style.setProperty('--swatch', color);
      item.append(swatch, document.createTextNode(label));
      return item;
    }));
  }
  function shared() {
    return {
      animation:false,
      textStyle:{fontFamily:'Inter, Microsoft YaHei UI, Microsoft YaHei, sans-serif',color:palette.ink,fontSize:11},
      tooltip:{renderMode:'richText', confine:true, backgroundColor:'#fcfdfa', borderColor:'#c0cfc3', borderWidth:1,
        textStyle:{fontSize:12,color:'#233f32'},axisPointer:{type:'line',lineStyle:{color:'#91a49a',type:'dashed'}}},
      grid:{left:54,right:18,top:34,bottom:38}
    };
  }
  const axisLabel = {color:'#52665c',fontSize:11};
  const axisLine = {lineStyle:{color:'#b7c4bb'}};
  const gridLine = {lineStyle:{color:palette.grid, type:'dashed'}};
  function valueAxis(name, opposite=false, scale=true) {
    return {type:'value',name,position:opposite ? 'right':'left',nameLocation:'end',nameGap:11,scale,
      nameTextStyle:{fontSize:10,color:'#5a6b60', align:opposite ? 'right':'left'},axisLabel:{...axisLabel,formatter:v=>financial.format(v)},
      axisLine:{show:false},axisTick:{show:false},splitNumber:4,splitLine:opposite ? {show:false}:gridLine};
  }
  function positionPlot(rows) {
    const byName = new Map(rows.map(row => [row.name,row]));
    const labels = rows.map(row => row.name);
    const option = shared();
    option.grid = {left:99,right:62,top:28,bottom:34};
    const category = {type:'category',data:labels,inverse:true,axisTick:{show:false},axisLine:{show:false}};
    option.yAxis = [{...category,axisLabel:{...axisLabel,interval:0,lineHeight:13,formatter:name => name+'\n'+(byName.get(name)?.date || '无有效日')}},
      {...category,position:'right',name:'距高点',nameLocation:'start',nameTextStyle:{fontSize:10,color:palette.axis},
        axisLabel:{...axisLabel,align:'left',margin:5,interval:0,formatter:name => val(byName.get(name)?.drawdown,'%')}}];
    option.xAxis = {type:'value',min:0,max:100,interval:25,axisLabel:{...axisLabel,formatter:'{value}%'},axisLine:{show:false},splitLine:gridLine};
    option.series = [{type:'bar',name:'收盘价分位',barMaxWidth:14,
      data:rows.map(r=>({value:r.pct,label:{position:r.pct>=84?'insideRight':'right',color:r.pct>=84?'#ffffff':'#29445a'}})),
      itemStyle:{color:palette.blue,borderRadius:[0,2,2,0]},
      label:{show:true,fontSize:11,formatter:p=>number(p.value) ? val(p.value,'%') : ''},labelLayout:{hideOverlap:true}}];
    option.tooltip.trigger = 'axis';
    option.tooltip.formatter = params => {
      const r = rows[params[0]?.dataIndex];
      return r ? [r.name+'  '+(r.date || '缺失'),'窗口起日  '+(r.first || '缺失'),'实际观测数  '+(r.n ?? '缺失'),'收盘价分位  '+val(r.pct,'%'),'距高点  '+val(r.drawdown,'%')].join('\n') : '';
    };
    return option;
  }
  function snapshotPlot(rows) {
    const option = shared();
    option.grid = {left:99,right:28,top:24,bottom:35};
    const values = rows.map(r=>r.value).filter(number);
    const min = Math.min(0,...values), max = Math.max(0,...values), length = max-min || 1;
    const map = new Map(rows.map(r=>[r.name,r]));
    option.xAxis = {type:'value',min:min-length*.19,max:max+length*.21,splitNumber:4,axisLabel:{...axisLabel,formatter:v=>financial.format(v)},axisLine:{show:false},splitLine:gridLine};
    option.yAxis = {type:'category',inverse:true,data:rows.map(r=>r.name),axisLine:{show:false},axisTick:{show:false},
      axisLabel:{...axisLabel,interval:0,lineHeight:13,formatter:name=>name+'\n'+(map.get(name)?.date || '无完整日')}};
    option.series = [{type:'bar',barMaxWidth:14,name:'估算净申赎',data:rows.map(r=>({
      value:r.value,itemStyle:{color:r.value<0?palette.green:palette.red},label:{position:r.value<0?'left':'right'}
    })),label:{show:true,fontSize:10,color:palette.ink,formatter:p=>number(p.value)?val(p.value):''},
      markLine:{silent:true,symbol:'none',label:{show:false},lineStyle:{color:'#788b80',width:1,type:'solid'},data:[{xAxis:0}]}}];
    option.tooltip.trigger = 'axis';
    option.tooltip.formatter = params => {
      const r=rows[params[0]?.dataIndex];
      return r ? r.name+'  '+(r.date || '无完整日')+'\n估算净申赎  '+val(r.value,' 亿元') : '';
    };
    return option;
  }
  function singleLine(name,values,color,index=0,dashed=false) {
    return {name,type:'line',data:values,yAxisIndex:index,connectNulls:false,showSymbol:true,
      symbolSize:values.length<40?4:2,emphasis:{focus:'series'},lineStyle:{width:2,type:dashed?'dashed':'solid',color},itemStyle:{color}};
  }
  function timePlot(dates, axes, series, measures) {
    const option=shared();
    option.grid={left:55,right:axes.length>1?57:17,top:31,bottom:39};
    option.xAxis={type:'category',data:dates,axisLine,axisTick:{show:false},
      boundaryGap:series.some(s=>s.type==='bar') || dates.length===1,
      axisLabel:{...axisLabel,interval:'auto',hideOverlap:true,showMinLabel:true,showMaxLabel:true,margin:9,
        formatter:date=> dates[0]?.slice(0,4)===dates[dates.length-1]?.slice(0,4) ? date.slice(5):date.slice(2)}};
    option.yAxis=axes;
    option.series=series;
    option.tooltip.trigger='axis';
    option.tooltip.formatter=params=>{
      if (!params.length) return '';
      const index=params[0].dataIndex;
      return [dates[index], ...measures.map(([name,values,unit])=>name+'  '+val(values[index],unit))].join('\n');
    };
    return option;
  }
  function extentText(dates) {
    if (!dates.length) return '无有效观测';
    return dates[0]===dates[dates.length-1]?dates[0]:dates[0]+' 至 '+dates[dates.length-1];
  }
  function validDates(dates, values) { return dates.filter((_,i)=>number(values[i])); }
  function seriesCoverage(label,dates,values) {
    const valid=validDates(dates, values);
    return label+' '+extentText(valid)+'（'+valid.length+'/'+dates.length+'日）';
  }
  function rowsByDate(dates, ...columns) { return dates.map((date,i)=>[date,...columns.map(c=>c[i])]); }
  function showYieldLatest() {
    const latest=window.Monitor.yieldHistory.latest;
    for (const [field,observation] of Object.entries(latest)) {
      const card=cards[5].card;
      card.querySelector('[data-yield-value="'+field+'"]').textContent=observation ?
        observation.value.toFixed(4)+(field==='spread'?' 个百分点':'%') : '暂无观测';
      const time=card.querySelector('[data-yield-date="'+field+'"]');
      time.dateTime=observation?.date || '';
      time.textContent=observation?.date || '';
    }
  }
  function setCard(index, config) {
    const card=cards[index];
    card.coverage.textContent=config.coverage;
    card.note.textContent=config.note;
    card.source.textContent='来源 · '+(config.source || '未记录');
    legend(card,config.legend);
    card.table=[config.headers,config.rows];
    if (card.details.open) renderTable(card); else card.scroll.replaceChildren();
    card.empty.hidden=config.hasData;
    card.empty.textContent=config.hasData?'':config.emptyMessage;
    card.card.dataset.status=config.hasData?'available':'empty';
    if (config.hasData) {
      if (!card.chart) card.chart=echarts.init(card.plot, null, {renderer:'canvas'});
      card.chart.setOption(config.option,{notMerge:true,lazyUpdate:false});
    } else if(card.chart) card.chart.clear();
  }
  function marketFlowCard(result) {
    const d=result.dates, f=result.flows;
    const headline=get('flow-conclusion'), quality=get('flow-quality');
    if(!f?.marketStart || !f?.eligible || !f?.covered || !f?.strict || !f?.observed) {
      headline.textContent='动态宽基 ETF 申赎数据尚未载入。';
      quality.textContent='请核对本地动态样本数据文件。其余五图可独立查看。';
      quality.parentElement.dataset.quality='partial';
      setCard(2,{coverage:'观察区间 '+extentText(d),note:'每日按当时可识别的股票宽基 ETF 及其上市状态取样。',
        legend:[],source:'动态样本数据未载入',hasData:false,emptyMessage:'缺少动态全市场份额与收盘价数据。',
        headers:['交易日','日净申赎（亿元）','累计（亿元）'],rows:[],option:null});
      return;
    }
    const observed=f.scope==='observed';
    const historic=get('flow-base').value==='history';
    const partial=f.observed.map((v,i)=>number(v) && !number(f.strict[i]));
    const cumulativePartial=f.cumulativePartial || d.map(()=>false);
    const baseName=historic?'历史累计':'区间累计';
    const ending=f.cumulative[d.length-1];
    const basePartial=f.basePartialDays ?? f.partialDays, baseMissing=f.baseMissingDays ?? f.missingDays;
    const incomplete=Boolean(basePartial || baseMissing || cumulativePartial[d.length-1]);
    const amountName=observed && incomplete?'已观测'+baseName+'净申赎':baseName+'净申赎';
    if(observed && !number(ending) && number(f.observedSubtotal) && f.lastObservedDate) {
      headline.textContent='截至 '+result.end+'，'+baseName+'已记录净申赎 '+val(f.observedSubtotal,' 亿元')+'，最近有金额的交易日为 '+f.lastObservedDate+'。截止日完整总额未知。';
    } else {
      headline.textContent='截至 '+result.end+'，'+amountName+(number(ending)?'估算为 '+val(ending,' 亿元'):'无法计算')+'。';
    }
    quality.parentElement.dataset.quality=incomplete?'partial':'complete';
    const last=d.length-1;
    const endCount=number(f.covered[last]) && number(f.eligible[last])?'末日覆盖 '+f.covered[last]+'/'+f.eligible[last]+' 只。':'';
    const periodLabel='图中共 '+d.length+' 个交易日，其中完整 '+f.completeDays+' 日、部分覆盖 '+f.partialDays+' 日、整日缺失 '+f.missingDays+' 日。'+endCount;
    let baseLabel='累计起点 '+(f.baseDate || '无可用日期')+'。';
    if(incomplete) {
      baseLabel+=observed?' 从起点至末日，'+basePartial+' 日仅计已取得金额，'+baseMissing+' 日无可计金额。':' 从起点至末日有 '+basePartial+' 个部分覆盖日、'+baseMissing+' 个整日缺失，完整累计在首个缺口后留空。';
    }
    if(f.leftTruncated) baseLabel+=' 所选开始日早于本地日历，更早日期的金额未知。';
    quality.textContent=periodLabel+' '+baseLabel;
    const amountLabel=observed?'已覆盖日净额':'完整日净额';
    const dailyBar={name:amountLabel,type:'bar',barMaxWidth:22,data:f.daily.map((v,i)=>{
      if(!number(v)) return null;
      const style={color:v<0?palette.green:palette.red};
      if(partial[i]) {
        style.opacity=.63;
        style.decal={symbol:'rect',color:'#554b4166',dashArrayX:[1,0],dashArrayY:[2,3],rotation:-Math.PI/4};
      }
      return {value:v,itemStyle:style};
    })};
    const exactLine=f.cumulative.map((v,i)=>cumulativePartial[i]?null:v);
    const estimatedLine=f.cumulative.map((v,i)=>observed && (cumulativePartial[i] ||
      (cumulativePartial[i+1] && number(f.cumulative[i+1])))?v:null);
    const gapSeries={name:'当日无可绘金额',type:'scatter',data:f.daily.map(v=>number(v)?null:0),
      symbol:'path://M-4,-5 L0,-1 L4,-5 L5,-4 L1,0 L5,4 L4,5 L0,1 L-4,5 L-5,4 L-1,0 L-5,-4 Z',symbolSize:8,itemStyle:{color:palette.gap}};
    const lines=[dailyBar,singleLine('累计（逐日完整）',exactLine,palette.blue,1)];
    if(observed && estimatedLine.some(number)) lines.push(singleLine('累计（含不完整覆盖）',estimatedLine,palette.gold,1,true));
    if(f.daily.some(v=>!number(v))) lines.push(gapSeries);
    const option=timePlot(d,[valueAxis('日净额 / 亿元',false,false),valueAxis('累计 / 亿元',true,false)],lines,[]);
    if (d.length > 1 && d.length <= 31) {
      const slots = Math.min(d.length, window.innerWidth < 540 ? 4 : 6);
      const ticks = new Set(Array.from({length:slots}, (_, j) => Math.round(j * (d.length - 1) / (slots - 1))));
      option.xAxis.axisLabel.interval = i => ticks.has(i);
    }
    option.tooltip.formatter=params=>{
      if(!params.length) return '';
      const i=params[0].dataIndex;
      const state=number(f.strict[i])?'完整':partial[i]?'部分覆盖':'无有效金额';
      return [d[i]+'  '+state,'有金额 / 当日识别  '+(f.covered[i]??'—')+' / '+(f.eligible[i]??'—')+' 只',
        '已覆盖日净额  '+val(f.observed[i],' 亿元'),'完整日净额  '+val(f.strict[i],' 亿元'),
        (cumulativePartial[i] && observed?'已观测':'')+baseName+'  '+val(f.cumulative[i],' 亿元')].join('\n');
    };
    if(d.length>100) {
      option.grid.bottom=62;
      option.dataZoom=[{type:'slider',xAxisIndex:0,filterMode:'none',bottom:6,height:16,brushSelect:false,
        textStyle:{fontSize:10},showDetail:false,backgroundColor:'#f4f7f3',fillerColor:'#71968433',borderColor:'#d1ddd5'}];
    }
    const reportStatus=f.observed.map((v,i)=>number(f.strict[i])?'完整':number(v)?'部分覆盖':'整日缺失');
    setCard(2,{
      coverage:'按公开目录回溯 · 观察区间 '+extentText(d)+' · 本口径日净额实际观测 '+extentText(validDates(d,f.daily)),
      legend:[['净申购 / 左轴',palette.red,'bar'],['净赎回 / 左轴',palette.green,'bar'],
        ...(observed && f.partialDays?[['仅计已覆盖基金',palette.gold,'pattern']]:[]),
        ...(exactLine.some(number)?[['完整累计 / 右轴',palette.blue]]:[]),...(observed && estimatedLine.some(number)?[['已观测累计 / 右轴',palette.gold,'dashed']]:[]),
        ...(f.daily.some(v=>!number(v))?[['当日无可绘金额 ×',palette.gap]]:[])],
      note:(f.universeNote || '按每日可识别的股票宽基 ETF 统计，样本随上市、退市和可用数据变化。')+
        '\n每日净额按当日份额较前一交易日的变化乘以未复权收盘价估算。'+(observed?'部分覆盖日仅合计已有金额；整日缺失显示断点。':'完整口径要求当日纳入计算的基金均有可用金额。')+
        (d.length>100?' 底部滑块可放大查看，累计起点保持不变。':''),
      source:f.source,hasData:f.daily.some(number),emptyMessage:result.end<f.marketStart?
        '所选截止日早于申赎日净额的最早可用日 '+f.marketStart+'，此时没有可计金额。':
        (observed?'区间内没有可计的宽基 ETF 日净申赎金额。':'区间内没有完整覆盖的交易日，可切换“已覆盖金额”查看可用观测。'),
      headers:['交易日','覆盖状态','有金额只数','当日识别只数','已覆盖日净额（亿元）','完整日净额（亿元）',amountName+'（亿元）'],
      rows:rowsByDate(d,reportStatus,f.covered,f.eligible,f.observed,f.strict,f.cumulative),option
    });
  }
  function renderSnapshots(result,mode) {
    const src=result.sources;
    const positions=result.positions, snapshot=result.snapshot;
    const knownPos=positions.filter(r=>number(r.pct));
    const posDates=knownPos.map(r=>r.date).filter(Boolean).sort();
    const knownSnapshots=snapshot.filter(r=>number(r.value));
    const snapDates=knownSnapshots.map(r=>r.date).filter(Boolean).sort();
    const windowText = mode==='250'?'各指数回看250个交易日':'以所选区间为计算窗口';
    const earliest=knownPos.map(r=>r.first).filter(Boolean).sort()[0];
    setCard(0,{
      coverage:windowText+' · 有效指数 '+knownPos.length+'/'+positions.length+' 只',
      legend:[['收盘价历史分位（%）',palette.blue,'bar']],
      note:'各指数实际截至日见名称下方。'+(earliest?'实际窗口最早始于 '+earliest+'，截至日 '+extentText(posDates)+'。':'所选窗口内有效历史不足。')+' 逐只窗口起止和观测数见明细。',
      source:src.indices,hasData:knownPos.length>0,emptyMessage:'所选窗口下，没有满足历史要求的指数。',
      headers:['指数','代码','实际窗口起日','实际截至日','有效观察数','收盘分位（%）','距最高（%）'],
      rows:positions.map(r=>[r.name,r.code,r.first,r.date,r.n,r.pct,r.drawdown]),option:positionPlot(positions)
    });
    setCard(1,{
      coverage:'截至 '+result.end+' · 原研究固定 '+result.poolSize+' 只 ETF',
      legend:[['净申购（亿元）',palette.red,'bar'],['净赎回（亿元）',palette.green,'bar']],
      note:'有效分组 '+knownSnapshots.length+'/'+snapshot.length+'，实际计算日 '+extentText(snapDates)+'。各组日期见柱旁。图③使用随交易日变化的宽基 ETF 样本。',
      source:src.flow,hasData:knownSnapshots.length>0,emptyMessage:'所选结束日及之前，各组均无完整的申赎观测。',
      headers:['分组','实际计算日','估算净申赎（亿元）'],rows:snapshot.map(r=>[r.name,r.date,r.value]),option:snapshotPlot(snapshot)
    });
  }
  function renderShares(result) {
    const d=result.dates, src=result.sources, selected=extentText(d);
    const sh=result.shares;
    setCard(3,{
      coverage:'观察区间 '+selected,
      legend:[['三只 ETF 合计份额 / 左轴',palette.red],['沪深300收盘 / 右轴',palette.blue,'dashed']],
      note:seriesCoverage('合计份额',d,sh.fund)+'；'+seriesCoverage('沪深300',d,sh.index)+'。合计包含 510300、510310、510330。',
      source:src.shares,hasData:sh.fund.some(number)||sh.index.some(number),emptyMessage:'区间内没有可绘制的基金份额或沪深300收盘值。',
      headers:['交易日','三只合计份额（亿份）','沪深300收盘（点）'],rows:rowsByDate(d,sh.fund,sh.index),
      option:timePlot(d,[valueAxis('合计份额 / 亿份'),valueAxis('指数 / 点',true)],[singleLine('三只 ETF 合计份额',sh.fund,palette.red),singleLine('沪深300',sh.index,palette.blue,1,true)],[['合计份额',sh.fund,' 亿份'],['沪深300',sh.index,' 点']])
    });
  }
  function renderMargin(result) {
    const d=result.dates, src=result.sources, selected=extentText(d);
    const mg=result.margin;
    setCard(4,{
      coverage:'观察区间 '+selected,
      legend:[['沪市融资余额',palette.violet],['深市融资余额',palette.blue]],
      note:seriesCoverage('沪市',d,mg.sh)+'；'+seriesCoverage('深市',d,mg.sz)+'。两条线分别截至各自实际披露日。',
      source:src.margin,hasData:mg.sh.some(number)||mg.sz.some(number),emptyMessage:'区间内没有可绘制的沪市或深市融资余额。',
      headers:['交易日','沪市融资余额（亿元）','深市融资余额（亿元）'],rows:rowsByDate(d,mg.sh,mg.sz),
      option:timePlot(d,[valueAxis('融资余额 / 亿元')],[singleLine('沪市',mg.sh,palette.violet),singleLine('深市',mg.sz,palette.blue)],[['沪市余额',mg.sh,' 亿元'],['深市余额',mg.sz,' 亿元']])
    });
  }
  function renderYields(result) {
    const src=result.sources, y=result.yields, yd=y.dates, selected=extentText(yd);
    const dividendName=window.PUBLIC_YIELDS?.dividendLabel || '沪深300股息率';
    const bondName=window.PUBLIC_YIELDS?.bondLabel || '十年期国债收益率';
    const headers=['观测日期',dividendName+'（%）',bondName+'（%）','同日利差（百分点）'];
    const columns=[y.dividend,y.bond,y.spread];
    for (const [field, label] of Object.entries({dividendCount:'有效成分股（只）',dividendCoverage:'有效成分权重（%）',bondCode:'国债代码',bondBanks:'报价银行（家）'})) {
      if (y[field]) { headers.push(label); columns.push(y[field]); }
    }
    setCard(5,{
      coverage:'观察区间 '+selected,
      legend:[[dividendName+' / 左轴',palette.red],[bondName+' / 左轴',palette.blue],['同日利差 / 右轴',palette.gold,'dashed']],
      note:'区间内'+seriesCoverage('股息率',yd,y.dividend)+'，'+seriesCoverage('国债',yd,y.bond)+'，'+seriesCoverage('同日利差',yd,y.spread)+'。利差按同日股息率减去国债收益率计算。',
      source:src.yields,hasData:y.dividend.some(number)||y.bond.some(number),emptyMessage:'所选区间没有股息率或国债收益率观测。',
      headers,rows:rowsByDate(yd,...columns),
      option:timePlot(yd,[valueAxis('收益率 / %'),valueAxis('利差 / 百分点',true)],[singleLine(dividendName,y.dividend,palette.red),singleLine(bondName,y.bond,palette.blue),singleLine('同日利差',y.spread,palette.gold,1,true)],[[dividendName,y.dividend,'%'],[bondName,y.bond,'%'],['同日利差',y.spread,' 个百分点']])
    });
  }
  const renderTimeSeries=[marketFlowCard,renderShares,renderMargin,renderYields];

  function parseDate(text) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return null;
    const [y,m,d]=text.split('-').map(Number);
    const date=new Date(y,m-1,d,12);
    return date.getFullYear()===y && date.getMonth()===m-1 && date.getDate()===d ? date:null;
  }
  function isoDate(date) {
    return [date.getFullYear(),String(date.getMonth()+1).padStart(2,'0'),String(date.getDate()).padStart(2,'0')].join('-');
  }
  function calendarYearStart(end,years) {
    const date=parseDate(end), year=date.getFullYear()-years, month=date.getMonth();
    const day=Math.min(date.getDate(),new Date(year,month+1,0).getDate());
    return isoDate(new Date(year,month,day+1,12));
  }
  function presetBounds(len) {
    let end=endBox.value;
    if (!parseDate(end)) {
      const latest=window.Monitor.yieldHistory.latest;
      end=[calendar[calendar.length-1],latest.dividend?.date,latest.bond?.date].filter(Boolean).sort().at(-1);
    }
    if(end<calendar[0]) return [end,end];
    if(len==='all') {
      const first=window.Monitor.marketStart || calendar[0];
      return [end<first?end:first,end];
    }
    if(len==='1y' || len==='3y') {
      const start=calendarYearStart(end,len==='1y'?1:3);
      return [start<calendar[0]?calendar[0]:start,end];
    }
    let last=calendar.length-1;
    while(last>0 && calendar[last]>end) last--;
    return [calendar[Math.max(0,last-len+1)],end];
  }
  function updatePresetStates() {
    const matching=presets.filter(len=>{
      const [s,e]=presetBounds(len);
      return startBox.value===s && endBox.value===e;
    });
    const active=matching.includes(chosenPreset)?chosenPreset:matching[0];
    presets.forEach(len=>{
      get('preset-'+len).setAttribute('aria-pressed',String(len===active));
    });
  }
  function cardPresetStart(index,key,end) {
    if(!parseDate(end)) return null;
    if(key==='all') {
      const first=window.Monitor.historyStartByChart[index+1];
      return first && first<end ? first : end;
    }
    return calendarYearStart(end,key==='1y'?1:3);
  }
  function applyCardPreset(index,key) {
    closePicker();
    const range=cards[index].range;
    const start=cardPresetStart(index,key,range.end.value.trim());
    if(start!==null) {
      range.start.value=start;
      range.chosenPreset=key;
    } else {
      range.end.focus();
    }
    queueRange(index,true);
  }
  function saveSelection() {
    const url=new URL(location.href);
    if (topDatesEdited) {
      url.searchParams.set('start',startBox.value);
      url.searchParams.set('end',endBox.value);
    } else {
      url.searchParams.delete('start');
      url.searchParams.delete('end');
    }
    for (const [k,v] of Object.entries({mode:modeBox.value,
      flowBase:get('flow-base').value,flowScope:get('flow-scope').value})) url.searchParams.set(k,v);
    for (const {card,range} of cards.filter(c=>c.range)) {
      for (const side of ['start','end']) {
        const key=card.id+'-'+side;
        if (range.independent) url.searchParams.set(key,range[side].value);
        else url.searchParams.delete(key);
      }
    }
    try { history.replaceState(null,'',url.href); }
    catch { try { sessionStorage.setItem('ranges:'+location.pathname,url.search); } catch {} }
  }
  function rangeMode(range,independent) {
    range.independent=independent;
    range.reset.setAttribute('aria-pressed',String(!independent));
  }
  function markRange(index,state,message) {
    const {card,range}=cards[index];
    card.dataset.rangeState=state;
    card.dataset.rangeStart=range.start.value;
    card.dataset.rangeEnd=range.end.value;
    card.setAttribute('aria-busy',String(state==='loading'));
    range.status.textContent=message;
    const matches=range.independent ? range.presetButtons.filter(button=>
      range.start.value===cardPresetStart(index,button.dataset.preset,range.end.value.trim())) : [];
    const active=matches.find(button=>button.dataset.preset===range.chosenPreset) || matches[0];
    range.presetButtons.forEach(button=>button.setAttribute('aria-pressed',String(button===active)));
  }
  function showRangeError(index,message) {
    const {card,range}=cards[index];
    delete card.dataset.start;
    delete card.dataset.end;
    if(index===2) card.querySelector('.flow-summary').hidden=true;
    setCard(index,{coverage:'',note:'',legend:[],source:'',hasData:false,emptyMessage:message,
      headers:[],rows:[],option:null});
    markRange(index,'error',range.independent?'本图区间有误':'顶部区间有误');
  }
  function renderRange(index,result) {
    const {card,range}=cards[index];
    if(result.error) { showRangeError(index,result.error); return; }
    if(index===2) card.querySelector('.flow-summary').hidden=false;
    renderTimeSeries[index-2](result);
    card.dataset.start=index===5 ? (result.yields.dates[0] || result.start) : result.start;
    card.dataset.end=index===5 ? (result.yields.dates.at(-1) || result.end) : result.end;
    const scope=range.independent?'本图独立区间':'跟随顶部日期';
    const warning=index===5 ? result.yields.warning : result.warning;
    markRange(index,'ready',warning?scope+' · '+warning:scope);
    if(index===2) {
      document.body.dataset.flowBase=get('flow-base').value;
      document.body.dataset.flowScope=get('flow-scope').value;
    }
  }
  function queueRange(index,independent,persist=true) {
    const {card,range}=cards[index];
    rangeMode(range,independent);
    if(!independent) {
      range.start.value=startBox.value;
      range.end.value=endBox.value;
    }
    const start=range.start.value.trim(), end=range.end.value.trim();
    const flowBase=get('flow-base').value, flowScope=get('flow-scope').value;
    const revision=++range.revision;
    clearTimeout(range.timer);
    markRange(index,'loading','正在计算本图…');
    if(independent) get('chart-deck').hidden=false;
    if(persist) saveSelection();
    range.timer=setTimeout(async ()=>{
      if(revision!==range.revision) return;
      try {
        if(!parseDate(end)) {
          showRangeError(index,'请先在本图填写有效的结束日期（YYYY-MM-DD）。');
          return;
        }
        const result=await window.Monitor.calculate(start,end,modeBox.value,flowBase,flowScope,index+1);
        if(revision===range.revision) renderRange(index,result);
      } catch(error) {
        if(revision===range.revision) showRangeError(index,'计算未完成。'+error.message);
      }
    },130);
  }
  function fail(message) {
    const keepIndependent=cards.some(c=>c.range?.independent);
    get('chart-deck').hidden=!keepIndependent;
    for(const [i,c] of cards.entries()) {
      if(i<2) c.card.hidden=true;
      else if(!c.range.independent) showRangeError(i,message);
    }
    get('chart-deck').setAttribute('aria-busy','false');
    delete document.body.dataset.start;
    delete document.body.dataset.end;
    setStatus('error',message);
  }
  function calculateSoon(persist=true) {
    const request=++ticket;
    clearTimeout(debounce);
    const start=startBox.value.trim(), end=endBox.value.trim(), mode=modeBox.value;
    const flowBase=get('flow-base').value, flowScope=get('flow-scope').value;
    const revisions=cards.map(({range},i)=>{
      if(!range || range.independent) return null;
      clearTimeout(range.timer);
      range.start.value=start;
      range.end.value=end;
      markRange(i,'loading','正在计算…');
      return ++range.revision;
    });
    if(persist) saveSelection();
    get('chart-deck').setAttribute('aria-busy','true');
    setStatus('loading','正在根据顶部日期计算…');
    updatePresetStates();
    debounce=setTimeout(async ()=>{
      if(request!==ticket) return;
      try {
        const result=await window.Monitor.calculate(start,end,mode,flowBase,flowScope);
        if(request!==ticket) return;
        if(result.error) {
          fail(result.error);
          // The bond market can publish on a date absent from the equity calendar.
          if(!cards[5].range.independent && revisions[5]===cards[5].range.revision) {
            const own=await window.Monitor.calculate(start,end,mode,flowBase,flowScope,6);
            if(request!==ticket || cards[5].range.revision!==revisions[5]) return;
            if(!own.error && (own.yields.bond.some(number) || own.yields.dividend.some(number))) {
              get('chart-deck').hidden=false;
              renderRange(5,own);
              Object.assign(document.body.dataset,{start,end,mode});
              setStatus('ready','顶部区间在本地 A 股日历中没有交易日。图⑥按所选数据来源显示。');
            }
          }
          return;
        }
        get('chart-deck').hidden=false;
        for(const c of cards.slice(0,2)) {
          c.card.hidden=false;
          c.card.dataset.start=result.start;
          c.card.dataset.end=result.end;
        }
        for(const c of cards) if(c.chart && c.chart.getWidth() !== c.plot.clientWidth) c.chart.resize();
        renderSnapshots(result,mode);
        for(const [i,c] of cards.entries()) {
          if(c.range && !c.range.independent && c.range.revision===revisions[i]) renderRange(i,result);
        }
        document.body.dataset.start=start;
        document.body.dataset.end=end;
        document.body.dataset.mode=mode;
        get('chart-deck').setAttribute('aria-busy','false');
        setStatus('ready','实际交易区间 '+extentText(result.dates)+' · '+result.dates.length+' 个交易日',result.warning || '');
      } catch(error) {
        if(request!==ticket) return;
        console.error(error);
        fail('计算未完成。'+(error?.message || '请核对本地数据文件，并重新选择日期。'));
      }
    },130);
  }
  function closePicker(restoreFocus=false) {
    const previous=pickerTarget;
    pickerTarget=null;
    get('calendar-popover').hidden=true;
    if(previous) get(previous+'-calendar').setAttribute('aria-expanded','false');
    if(restoreFocus && previous) get(previous+'-calendar').focus();
  }
  function positionPicker() {
    if(!pickerTarget) return;
    const pop=get('calendar-popover');
    const anchor=get(pickerTarget+'-date').closest('.input-wrap').getBoundingClientRect();
    const top=anchor.bottom+pop.offsetHeight+5<=window.innerHeight ? anchor.bottom+5 : anchor.top-pop.offsetHeight-5;
    pop.style.left=Math.max(6,Math.min(anchor.left,document.documentElement.clientWidth-pop.offsetWidth-6))+'px';
    pop.style.top=Math.max(6,Math.min(top,window.innerHeight-pop.offsetHeight-6))+'px';
  }
  function paintCalendar() {
    const year=shownMonth.getFullYear(), month=shownMonth.getMonth(), selected=get(pickerTarget+'-date').value;
    const yearMenu=get('calendar-year'), monthMenu=get('calendar-month');
    const lower=Math.min(Number(calendar[0].slice(0,4))-1,year-1);
    const upper=Math.max(Number(calendar[calendar.length-1].slice(0,4))+1,year+1);
    yearMenu.replaceChildren();
    for(let y=lower;y<=upper;y++) yearMenu.add(new Option(y+'年',String(y),false,y===year));
    monthMenu.replaceChildren();
    for(let m=0;m<12;m++) monthMenu.add(new Option((m+1)+'月',String(m),false,m===month));
    const dayGrid=get('calendar-popover').querySelector('.day-grid');
    const offset=(shownMonth.getDay()+6)%7, count=new Date(year,month+1,0).getDate();
    dayGrid.replaceChildren();
    for(let i=0;i<offset;i++) dayGrid.append(el('span'));
    for(let d=1;d<=count;d++) {
      const date=new Date(year,month,d,12), str=isoDate(date), btn=el('button','',String(d));
      btn.type='button';
      btn.dataset.date=str;
      btn.setAttribute('aria-label',str);
      btn.setAttribute('aria-pressed',String(str===selected));
      if(str===selected) btn.classList.add('selected');
      if(date.getDay()===0 || date.getDay()===6) btn.classList.add('weekend');
      btn.addEventListener('click',()=>{
        const box=get(pickerTarget+'-date');
        box.value=str;
        closePicker(true);
        box.dispatchEvent(new Event('input',{bubbles:true}));
      });
      dayGrid.append(btn);
    }
    positionPicker();
  }
  function openPicker(which) {
    if(pickerTarget===which) { closePicker(); return; }
    closePicker();
    pickerTarget=which;
    const selected=parseDate(get(which+'-date').value) || parseDate(calendar[calendar.length-1]);
    shownMonth=new Date(selected.getFullYear(),selected.getMonth(),1,12);
    const pop=get('calendar-popover');
    pop.setAttribute('aria-label',get(which+'-calendar').getAttribute('aria-label'));
    pop.hidden=false;
    get(which+'-calendar').setAttribute('aria-expanded','true');
    paintCalendar();
    (pop.querySelector('button.selected') || pop.querySelector('.day-grid button')).focus({preventScroll:true});
  }
  function installControls() {
    document.body.append(get('calendar-popover'));
    function attachDate(which,onEdit) {
      get(which+'-calendar').addEventListener('click',()=>openPicker(which));
      const input=get(which+'-date');
      input.addEventListener('input',onEdit);
      input.addEventListener('keydown',event=>{
        if(event.key==='ArrowDown') { event.preventDefault(); openPicker(which); }
      });
    }
    for(const side of ['start','end']) attachDate(side,()=>{topDatesEdited=true; calculateSoon();});
    for(const [i,c] of cards.entries()) {
      if(!c.range) continue;
      for(const side of ['start','end']) attachDate(c.card.id+'-'+side,()=>queueRange(i,true));
      c.range.reset.addEventListener('click',()=>queueRange(i,false));
      for(const button of c.range.presetButtons) {
        button.addEventListener('click',()=>applyCardPreset(i,button.dataset.preset));
      }
    }
    for(const count of presets) get('preset-'+count).addEventListener('click',()=>{
      closePicker();
      chosenPreset=count;
      topDatesEdited=true;
      [startBox.value,endBox.value]=presetBounds(count);
      calculateSoon();
    });
    modeBox.addEventListener('change',()=>calculateSoon());
    for(const control of ['flow-base','flow-scope']) {
      get(control).addEventListener('change',()=>queueRange(2,cards[2].range.independent));
    }
    for (const [id,step] of [['calendar-prev',-1],['calendar-next',1]]) get(id).addEventListener('click',()=>{
      shownMonth=new Date(shownMonth.getFullYear(),shownMonth.getMonth()+step,1,12);paintCalendar();
    });
    for(const id of ['calendar-year','calendar-month']) get(id).addEventListener('change',()=>{
      shownMonth=new Date(+get('calendar-year').value,+get('calendar-month').value,1,12);paintCalendar();
    });
    document.addEventListener('pointerdown',event=>{
      if(pickerTarget && !event.target.closest('#calendar-popover,.calendar-button')) closePicker();
    });
    document.addEventListener('keydown',event=>{ if(event.key==='Escape' && pickerTarget) closePicker(true); });
    window.addEventListener('resize',positionPicker);
    window.addEventListener('scroll',positionPicker,{passive:true,capture:true});
  }
  try {
    if(!window.echarts) throw new Error('绘图库未载入，请保留“程序”文件夹。');
    if(!window.Monitor || !Array.isArray(window.Monitor.calendar) || !window.Monitor.calendar.length)
      throw new Error('未读取到交易日历，请将入口与“程序”“数据”两个文件夹放在一起。');
    calendar=window.Monitor.calendar;
    get('available-range').textContent=extentText(calendar);
    buildCards();
    if(window.Monitor.marketStart) {
      get('flow-base').querySelector('option[value=history]').textContent='可用历史起点（'+window.Monitor.marketStart+'）';
    }
    installControls();
    [startBox.value,endBox.value]=presetBounds(25);
    let saved=location.search;
    if(!saved && location.protocol==='file:') {
      try { saved=sessionStorage.getItem('ranges:'+location.pathname) || ''; } catch {}
    }
    const params=new URLSearchParams(saved);
    const s=params.get('start'), e=params.get('end');
    if(parseDate(s || '') && parseDate(e || '')) {startBox.value=s;endBox.value=e;topDatesEdited=true;}
    if(['250','range'].includes(params.get('mode'))) modeBox.value=params.get('mode');
    if(['selection','history'].includes(params.get('flowBase'))) get('flow-base').value=params.get('flowBase');
    if(['observed','strict'].includes(params.get('flowScope'))) get('flow-scope').value=params.get('flowScope');
    showYieldLatest();
    for(const [i,c] of cards.entries()) {
      if(!c.range || !params.has(c.card.id+'-start') || !params.has(c.card.id+'-end')) continue;
      c.range.start.value=params.get(c.card.id+'-start');
      c.range.end.value=params.get(c.card.id+'-end');
      queueRange(i,true,false);
    }
    calculateSoon(false);
  } catch(error) {
    console.error(error);
    fail('页面未能启动。'+error.message);
  }
})();
