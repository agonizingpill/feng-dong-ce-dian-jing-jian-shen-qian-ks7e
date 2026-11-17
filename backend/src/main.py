import os,logging
from contextlib import asynccontextmanager
import redis
from sqlalchemy import select,delete
from sqlalchemy.exc import IntegrityError
from fastapi import FastAPI,Depends,HTTPException
from fastapi.responses import JSONResponse
from .db import Base,engine,db_session
from .models import User,Probe,Sweep,Pressure,Layout,LayoutProbe,Audit
from .version_models import SourceVersion
from .auth import current,writers,admin,verify,token
from .schemas import Login,ProbeInput,SweepInput,LayoutInput,Selection
from .services import get,pack,pack_layout,pack_sweep,audit,assert_source_not_frozen,invalidate,assign,check_sign,source_checksum
from . import version_service as vs
from .migrations import run_migrations
from .middlewares import RequestTrace,BurstGuard
from .readiness_routes import router as readiness_router
from .testcycle_routes import router as testcycle_router
from .constants import ROLES,ZONES,LAYOUT_STATES
r=redis.Redis.from_url(os.environ['REDIS_URL'],decode_responses=True)
@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    run_migrations()
    if os.getenv('SEED_DEMO','true')=='true':
        try:
            from scripts.seed_demo import seed
            seed()
        except Exception:logging.exception('seed failed')
    # seed 可能首次写入来源，确保存在基线版本
    try:
        from .db import Session
        with Session() as s:
            vs.bootstrap_source_versions(s);s.commit()
    except Exception:logging.exception('bootstrap source version failed')
    yield
app=FastAPI(title='翼衡 · 风洞测点精简审签',lifespan=lifespan);app.include_router(readiness_router);app.include_router(testcycle_router);app.add_middleware(RequestTrace);app.add_middleware(BurstGuard)
@app.exception_handler(IntegrityError)
async def conflict(req,exc):return JSONResponse({'detail':'编号重复、测点重复或关联条件不满足，请核对'},409)
@app.get('/health')
def health(s=Depends(db_session)):s.execute(select(1));r.ping();return {'status':'ok'}
@app.post('/auth/login')
def login(p:Login,s=Depends(db_session)):
    u=s.scalar(select(User).where(User.email==p.email))
    if not u or not verify(p.password,u.password_hash):raise HTTPException(401,'邮箱或口令不正确')
    return {'token':token(u),'user':{'email':u.email,'role':u.role}}
@app.get('/me')
def me(u=Depends(current)):return {'email':u.email,'role':u.role}
@app.get('/enums')
def enums(u=Depends(current)):return {'roles':ROLES,'zones':ZONES,'layout_states':LAYOUT_STATES}
@app.get('/probes')
def probes(s=Depends(db_session),u=Depends(current)):return [pack(x)for x in s.scalars(select(Probe).order_by(Probe.id))]
@app.put('/probes/{pid}')
def probe(pid:int,p:ProbeInput,s=Depends(db_session),u=Depends(writers)):
    v=get(s,Probe,pid)
    vs.lock_source(s);assert_source_not_frozen(s)
    before=source_checksum(s)
    for k,value in p.model_dump().items():setattr(v,k,value)
    after=source_checksum(s)
    if after!=before:
        invalidate(s);version=vs.register_source_revision(s,u.email,'修订标定 '+v.code)
        audit(s,u,f'修订标定 {v.code}，来源升级至 v{version.seq}')
    else:
        audit(s,u,'修订标定 '+v.code+'（内容无变化，不升版本）')
    s.commit();return pack(v)
@app.get('/sweeps')
def sweeps(s=Depends(db_session),u=Depends(current)):return [pack_sweep(s,x)for x in s.scalars(select(Sweep).order_by(Sweep.id))]
def write_sweep(s,p,v=None):
    probes=s.scalars(select(Probe).order_by(Probe.id)).all()
    if set(p.pressures)!={x.code for x in probes}:raise HTTPException(409,'压力记录须覆盖全部已登记测点代码')
    if any(not -10000<=x<=10000 for x in p.pressures.values()):raise HTTPException(409,'压力超出传感器量程±10000Pa')
    if not v:v=Sweep(code=p.code,speed=p.speed,angle=p.angle,lift=p.lift);s.add(v);s.flush()
    else:
        for k in ('code','speed','angle','lift'):setattr(v,k,getattr(p,k))
        s.execute(delete(Pressure).where(Pressure.sweep_id==v.id))
    for probe in probes:s.add(Pressure(sweep_id=v.id,probe_id=probe.id,pascal=p.pressures[probe.code]))
    return v
@app.post('/sweeps')
def add_sweep(p:SweepInput,s=Depends(db_session),u=Depends(writers)):
    vs.lock_source(s);assert_source_not_frozen(s);before=source_checksum(s)
    v=write_sweep(s,p);after=source_checksum(s)
    if after!=before:
        invalidate(s);version=vs.register_source_revision(s,u.email,'录入扫角 '+v.code);audit(s,u,f'录入扫角 {v.code}，来源升级至 v{version.seq}')
    else:audit(s,u,'录入扫角 '+v.code)
    s.commit();return pack_sweep(s,v)
@app.put('/sweeps/{sid}')
def edit_sweep(sid:int,p:SweepInput,s=Depends(db_session),u=Depends(writers)):
    vs.lock_source(s);assert_source_not_frozen(s);before=source_checksum(s)
    v=write_sweep(s,p,get(s,Sweep,sid));after=source_checksum(s)
    if after!=before:
        invalidate(s);version=vs.register_source_revision(s,u.email,'修订扫角 '+v.code);audit(s,u,f'修订扫角 {v.code}，来源升级至 v{version.seq}')
    else:audit(s,u,'修订扫角 '+v.code+'（内容无变化，不升版本）')
    s.commit();return pack_sweep(s,v)

# ---------- 来源版本管理 ----------
def pack_version(s,v):
    data=pack(v);data.pop('source_snapshot',None)
    # 当前是否为最新有效版本
    data['is_current']=vs.latest_version(s).id==v.id
    return data
@app.get('/source-versions')
def source_versions(s=Depends(db_session),u=Depends(current)):
    return [pack_version(s,v) for v in s.scalars(select(SourceVersion).order_by(SourceVersion.seq.desc()))]
@app.get('/source-versions/{vid}')
def source_version_detail(vid:int,s=Depends(db_session),u=Depends(current)):
    v=get(s,SourceVersion,vid);data=pack(v)
    # 该版本下锚定的布置（含已签与失效），便于复查旧签署
    data['layouts']=[pack_layout(s,l) for l in s.scalars(select(Layout).where(Layout.source_version_id==vid).order_by(Layout.id))]
    data['is_current']=vs.latest_version(s).id==v.id
    return data

@app.get('/layouts')
def layouts(s=Depends(db_session),u=Depends(current)):return [pack_layout(s,x)for x in s.scalars(select(Layout).order_by(Layout.id.desc()))]
def enqueue(s,l):
    version=vs.latest_version(s)
    if not version:raise HTTPException(409,'来源版本尚未建立，无法计算')
    l.status='calculating';l.evidence=None
    # 入队即锚定当前完整版本；worker 完成时再次核对，迟到结果不能恢复旧资格
    l.source_checksum=version.checksum;l.source_version_id=version.id
    l.invalid_reason=None;l.legacy_basis_missing=False
    s.commit()
    try:r.rpush('tunnel-selection',l.id)
    except Exception:l.status='failed';l.invalid_reason=vs.REASON_CALC_FAILED;s.commit();raise HTTPException(503,'计算队列暂不可用，请重试')
@app.post('/layouts')
def add_layout(p:LayoutInput,s=Depends(db_session),u=Depends(writers)):
    l=Layout(**p.model_dump(),auto_select=True,status='calculating');s.add(l);audit(s,u,'建立通道方案 '+p.code);enqueue(s,l);return pack_layout(s,l)
@app.put('/layouts/{lid}/probes')
def reselect(lid:int,p:Selection,s=Depends(db_session),u=Depends(writers)):
    l=get(s,Layout,lid)
    if l.status in ('signed','calculating'):raise HTTPException(409,'已审签或计算中的方案不能改点')
    assign(s,l,p.probe_ids);l.auto_select=False;audit(s,u,'人工修订测点 '+l.code);enqueue(s,l);return pack_layout(s,l)
@app.post('/layouts/{lid}/recalculate')
def recalculate(lid:int,s=Depends(db_session),u=Depends(writers)):
    l=get(s,Layout,lid)
    if l.status=='signed':raise HTTPException(409,'已审签布置锚定原始依据，不能重算覆盖')
    if l.status=='calculating':raise HTTPException(409,'方案计算中，请等待当前结果')
    # 重算基于当前完整来源版本，重新获得可审签资格（含历史遗留方案）
    audit(s,u,'发起重算 '+l.code);enqueue(s,l);return pack_layout(s,l)
@app.post('/layouts/{lid}/sign')
def sign(lid:int,s=Depends(db_session),u=Depends(admin)):
    # 先取来源锁（与源修订串行），再锁布置行；锁顺序全局一致，避免死锁
    version=vs.lock_source(s)
    l=s.query(Layout).filter_by(id=lid).with_for_update().one_or_none()
    if not l:raise HTTPException(404,'布置方案不存在')
    check_sign(s,l)
    # 签名只落在当前完整版本，并冻结完整原始依据（独立留存，后续修订不影响）
    l.basis_snapshot=version.source_snapshot
    l.status='signed';l.signed_by=u.email;l.invalid_reason=None
    audit(s,u,'审签布置 '+l.code+f'（锚定来源 v{version.seq}）');s.commit();return pack_layout(s,l)
@app.get('/audits')
def audits(s=Depends(db_session),u=Depends(current)):return [pack(x)for x in s.scalars(select(Audit).order_by(Audit.id.desc()).limit(30))]
