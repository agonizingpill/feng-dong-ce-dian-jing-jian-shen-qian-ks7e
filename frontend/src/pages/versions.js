import {esc} from '../utils/format.js';
import {STATES} from '../constants.js';
import {empty,badge,notify} from '../components/instruments.js';
import {api,send} from '../api.js';
import {versionSeq} from '../store.js';

let selectedVersionId=null;
let versionDetail=null;

const short=sha=>sha?esc(sha.slice(0,16))+'…':'—';
const stamp=x=>x?esc(x.replace('T',' ').slice(0,19)):'—';
const anchor=(s,l)=>l.source_version_id?('v'+versionSeq(s,l.source_version_id)):'—';

export function render(s){
  const pending=s.layouts.filter(x=>x.status==='calculating');
  const invalid=s.layouts.filter(x=>['invalidated','failed'].includes(x.status));
  const ready=s.layouts.filter(x=>x.status==='ready');
  const signed=s.layouts.filter(x=>x.status==='signed');
  const selId=selectedVersionId??(s.sourceVersions[0]?.id);
  return `<div class="head"><div><div class="eyebrow">SOURCE VERSION / 10</div><h1>来源修订留版本，重算才能再审签。</h1><p>每份来源版本冻结当时的测点标定、扫角压力与训练留出依据；已签布置永久锚定其原始版本。</p></div><span class="mono">${s.sourceVersions.length} VERSIONS</span></div>
<div class="widecolumns">
<section class="section">
  <div class="sectionhead"><h2>来源版本</h2><small>点击查看冻结的原始依据与锚定方案</small></div>
  <div class="tablewrap"><table><thead><tr><th>版本</th><th>来源校验 SHA256</th><th>变更说明</th><th>测点/扫角/压力</th><th>登记人 / 时刻</th><th></th></tr></thead><tbody>
  ${s.sourceVersions.map(v=>`<tr style="cursor:pointer" data-version="${v.id}">
    <td class="mono">v${v.seq}${v.is_current?' <span class="status ready">当前</span>':''}</td>
    <td class="mono">${short(v.checksum)}</td>
    <td>${esc(v.change_note||'—')}</td>
    <td class="mono">${v.probe_count} / ${v.sweep_count} / ${v.pressure_count}</td>
    <td><small>${esc(v.created_by)}<br>${stamp(v.created_at)}</small></td>
    <td>${v.id===selId?'●':''}</td></tr>`).join('')}
  </tbody></table></div>
  <div id="version-detail">${versionDetail?renderDetail(versionDetail):empty('选择一个来源版本查看冻结的原始依据。')}</div>
</section>
<aside class="section">
  <h2>方案状态看板</h2>
  <h3 class="run-check">待算（计算中） · ${pending.length}</h3>
  ${pending.length?pending.map(l=>layoutRow(s,l)).join(''):empty('当前没有计算中的方案。')}
  <h3 class="run-check" style="margin-top:18px">待重算（已失效/失败） · ${invalid.length}</h3>
  ${invalid.length?invalid.map(l=>invalidRow(s,l)).join(''):empty('没有失效方案，来源一致。')}
  <h3 class="run-check" style="margin-top:18px">待审签 · ${ready.length}</h3>
  ${ready.length?ready.map(l=>layoutRow(s,l)).join(''):empty('暂无待审签方案。')}
  <h3 class="run-check" style="margin-top:18px">已审签（锚定原始版本） · ${signed.length}</h3>
  ${signed.length?signed.map(l=>signedRow(s,l)).join(''):empty('尚无已审签布置。')}
  <p class="noteblock">源数据更新会立即使未签的旧计算失效（保留旧版本锚点可追溯）；在途计算的迟到结果不能恢复旧资格，须重算。首份审签后来源冻结。</p>
</aside>
</div>`;
}

function layoutRow(s,l){
  return `<div class="run-entry"><b class="mono">${esc(l.code)}</b> <small>预算 ${l.max_channels} 通道 · 锚定 ${anchor(s,l)}</small><div>${badge(l)}</div></div>`;
}
function invalidRow(s,l){
  return `<div class="run-entry"><b class="mono">${esc(l.code)}</b> <small>原锚定 ${anchor(s,l)}</small><div>${badge(l)}</div>
    <small class="muted">失效原因：${esc(l.invalid_reason||'来源已变更')}</small>
    <div class="actions"><button class="primary" data-recalc="${l.id}">发起重算</button></div></div>`;
}
function signedRow(s,l){
  return `<div class="run-entry"><b class="mono">${esc(l.code)}</b> <small>审签人 ${esc(l.signed_by||'—')} · 锚定 ${anchor(s,l)}</small><div>${badge(l)}</div>
    ${l.legacy_basis_missing?'<small class="muted">历史升级方案，缺少原始来源依据，不能用现值补造。</small>':'<small class="muted">原始压力、选点与训练留出依据已随版本冻结。</small>'}</div>`;
}

function renderDetail(d){
  return `<div style="margin-top:16px">
    <div class="metricstrip"><div>测点<b>${d.probe_count}</b></div><div>扫角<b>${d.sweep_count}</b></div><div>压力记录<b>${d.pressure_count}</b></div><div>版本<b>v${d.seq}</b></div></div>
    <h3 class="run-check" style="margin-top:14px">冻结原始依据（不可改写）</h3>
    <p class="muted">来源校验 <span class="mono">${esc(d.checksum)}</span><br>变更说明：${esc(d.change_note||'—')}<br>登记：${esc(d.created_by)} · ${stamp(d.created_at)}</p>
    <h3 class="run-check">锚定本版本的布置（${d.layouts.length}）</h3>
    ${d.layouts.length?`<div class="tablewrap"><table><thead><tr><th>布置</th><th>状态</th><th>选点</th><th>失效/遗留</th></tr></thead><tbody>
      ${d.layouts.map(l=>`<tr><td class="mono">${esc(l.code)}</td><td>${badge(l)}</td><td class="mono">${(l.probe_ids||[]).join(', ')||'—'}</td><td><small>${esc(l.invalid_reason||'')}${l.legacy_basis_missing?'（缺少原始依据，禁止补造）':''}</small></td></tr>`).join('')}
    </tbody></table></div>`:empty('没有布置锚定此版本。')}
    ${!d.is_current?'<p class="noteblock">这是历史版本，仅供复查旧签署与原始依据；新计算锚定当前版本。</p>':''}
  </div>`;
}

export function bind(s,reload){
  document.querySelectorAll('[data-version]').forEach(el=>el.onclick=async()=>{
    selectedVersionId=Number(el.dataset.version);
    try{versionDetail=await api('/source-versions/'+selectedVersionId);await reload();}
    catch(e){notify(e.message);}
  });
  document.querySelectorAll('[data-recalc]').forEach(btn=>btn.onclick=async()=>{
    try{await send('/layouts/'+btn.dataset.recalc+'/recalculate',{},'POST');notify('已发起重算，完成后锚定当前来源版本');await reload();}
    catch(e){notify(e.message);}
  });
}
