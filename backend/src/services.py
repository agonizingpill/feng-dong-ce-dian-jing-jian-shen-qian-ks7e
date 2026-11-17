from datetime import datetime,timezone
import hashlib,json
from fastapi import HTTPException
from sqlalchemy import select,delete
from .models import Probe,Sweep,Pressure,Layout,LayoutProbe,Audit

def get(s,cls,id):
    v=s.get(cls,id)
    if not v:raise HTTPException(404,'记录不存在')
    return v
def pack(v):return {c.name:getattr(v,c.name) for c in v.__table__.columns}
def selected(s,lid):return list(s.scalars(select(LayoutProbe.probe_id).where(LayoutProbe.layout_id==lid).order_by(LayoutProbe.probe_id)))
def pack_layout(s,l):
    data=pack(l)|{'probe_ids':selected(s,l.id)}
    # 来源版本/失效依据对前端可见；完整原始依据另由版本详情接口提供
    data.pop('basis_snapshot',None)
    return data
def pack_sweep(s,x):
    return pack(x)|{'pressures':{probe.code:pressure.pascal for pressure,probe in s.execute(select(Pressure,Probe).join(Probe,Probe.id==Pressure.probe_id).where(Pressure.sweep_id==x.id))}}
def audit(s,u,action):s.add(Audit(actor=u.email,action=action,stamp=datetime.now(timezone.utc).isoformat()))

def current_source_version(s):
    """来源版本服务的延迟导入，避免循环依赖。"""
    from . import version_service as vs
    return vs.latest_version(s)

def assert_source_not_frozen(s):
    # 首份审签后整个试验来源冻结；未签期间允许继续修订源数据（计算中也可改，
    # 迟到结果由 worker 按 checksum 判定失效，不能恢复旧资格）。
    if s.scalar(select(Layout.id).where(Layout.status=='signed')):
        raise HTTPException(409,'已有审签布置，标定与扫角来源已冻结；新采样须另立来源版本')

def invalidate(s, reason=None):
    """源数据确认变化后调用：失效未签方案。已签布置锚定旧版本，不受影响。"""
    from . import version_service as vs
    vs.invalidate_dependents(s, reason or vs.REASON_SOURCE_REVISED)

def assign(s,l,ids):
    if len(ids)!=len(set(ids)):raise HTTPException(409,'测点不能重复占用通道')
    if len(ids)>l.max_channels:raise HTTPException(409,'所选测点超过本方案通道预算')
    probes=[get(s,Probe,i) for i in ids]
    if any(not p.enabled for p in probes):raise HTTPException(409,'停用测点不能加入布置')
    s.execute(delete(LayoutProbe).where(LayoutProbe.layout_id==l.id))
    for id in ids:s.add(LayoutProbe(layout_id=l.id,probe_id=id))

def source_checksum(s):
    content={'probes':[pack(p) for p in s.scalars(select(Probe).order_by(Probe.id))],'sweeps':[pack_sweep(s,x)for x in s.scalars(select(Sweep).order_by(Sweep.id))]}
    return hashlib.sha256(json.dumps(content,sort_keys=True).encode()).hexdigest()

def check_sign(s,l):
    if l.status!='ready':raise HTTPException(409,'仅最新计算完成的方案可审签')
    if l.legacy_basis_missing or l.source_version_id is None:
        raise HTTPException(409,'历史升级方案缺少原始来源依据，不能用现值补造；请基于当前来源重算')
    ids=selected(s,l.id);probes=[get(s,Probe,i) for i in ids]
    if not probes or len(probes)>l.max_channels:raise HTTPException(409,'测点数超过通道预算或尚未选点')
    if any(not p.calibrated or not p.enabled for p in probes):raise HTTPException(409,'包含未标定或已停用测点，不能审签')
    if set(p.zone for p in probes)!={'root','mid','tip'}:raise HTTPException(409,'布置须同时覆盖翼根、翼中、翼尖')
    checksum=source_checksum(s)
    if l.source_checksum!=checksum:raise HTTPException(409,'来源校验已变化，请重新计算')
    from . import version_service as vs
    v=vs.latest_version(s)
    if not v or v.checksum!=checksum or l.source_version_id!=v.id:
        raise HTTPException(409,'方案未锚定当前完整来源版本，请重新计算后审签')
