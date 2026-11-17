from copy import deepcopy
from datetime import datetime, timezone

from sqlalchemy import select, inspect

from .models import Probe, Sweep, Pressure, Layout
from .version_models import SourceVersion
from .services import pack, pack_sweep, source_checksum

# 失效原因，前端按此展示“失效原因”
REASON_SOURCE_REVISED = '源数据已修订，旧计算依据失效，请重算后再审签'
REASON_STALE_RESULT = '计算期间来源发生修订，迟到结果不能恢复旧资格，请重算'
REASON_CALC_FAILED = '互信息/回归计算失败，请修正来源后重算'
REASON_LEGACY = '历史升级方案，缺少原始来源依据，不能用现值补造'


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def build_snapshot(s):
    """冻结当前完整来源：全部测点、扫角及逐点压力（原始依据）。"""
    probes = [pack(p) for p in s.scalars(select(Probe).order_by(Probe.id))]
    sweeps = [pack_sweep(s, x) for x in s.scalars(select(Sweep).order_by(Sweep.id))]
    total_pressures = s.query(Pressure).count()
    return {
        'probes': probes,
        'sweeps': sweeps,
        'pressure_count': total_pressures,
        'probe_count': len(probes),
        'sweep_count': len(sweeps),
    }


def latest_version(s):
    return s.scalar(select(SourceVersion).order_by(SourceVersion.seq.desc()).limit(1))


def lock_source(s):
    """串行化“源数据修订”与“布置审签”。

    两者都先锁定当前最新来源版本行（基线版本由 bootstrap 保证存在），
    使修订与审签互斥：审签持锁期间，修订在拿到锁后会看到已签布置而被冻结拦截；
    修订持锁期间，审签等待后按 READ COMMITTED 读到最新版本再判定，避免漏判。
    SQLite 不支持 FOR UPDATE（SQLAlchemy 静默忽略），本地单线程测试不受影响。
    """
    bootstrap_source_versions(s)
    row = s.scalars(
        select(SourceVersion).order_by(SourceVersion.seq.desc()).limit(1).with_for_update()
    ).first()
    return row


def create_version(s, checksum, snapshot, actor, note=''):
    seq = (s.scalar(select(SourceVersion.seq).order_by(SourceVersion.seq.desc()).limit(1)) or 0) + 1
    v = SourceVersion(
        seq=seq, checksum=checksum, change_note=note, created_by=actor,
        created_at=now_iso(), source_snapshot=deepcopy(snapshot),
        probe_count=snapshot['probe_count'], sweep_count=snapshot['sweep_count'],
        pressure_count=snapshot['pressure_count'],
    )
    s.add(v)
    s.flush()
    return v


def bootstrap_source_versions(s):
    """确保来源至少有一个版本。升级前已存在的来源补登基线版本（幂等）。"""
    if not inspect(s.bind).has_table(SourceVersion.__tablename__):
        return None
    if latest_version(s) is not None:
        return latest_version(s)
    if not s.scalars(select(Probe).order_by(Probe.id)).first():
        return None
    checksum = source_checksum(s)
    snapshot = build_snapshot(s)
    return create_version(s, checksum, snapshot, 'system:bootstrap', '来源版本基线（升级补登）')


def mark_legacy_layouts(s):
    """升级前遗留、缺少原始依据的旧方案明确标注，禁止用现值补造。"""
    if not inspect(s.bind).has_table('layouts'):
        return
    cols = {c['name'] for c in inspect(s.bind).get_columns('layouts')}
    if 'legacy_basis_missing' not in cols:
        return
    # 仅标注未锚定任何来源版本的旧方案；新方案（含失效待重算）不属遗留
    for l in s.scalars(select(Layout).where(Layout.source_version_id.is_(None))):
        if not l.legacy_basis_missing:
            l.legacy_basis_missing = True
            # 无论状态都写明原因，页面可明确提示无法用现值补造
            l.invalid_reason = REASON_LEGACY


def register_source_revision(s, actor, note):
    """源数据修订后登记新版本；checksum 未变化则不产生新版本。返回当前版本。"""
    bootstrap_source_versions(s)
    checksum = source_checksum(s)
    current = latest_version(s)
    if current and current.checksum == checksum:
        return current
    snapshot = build_snapshot(s)
    return create_version(s, checksum, snapshot, actor, note)


def invalidate_dependents(s, reason):
    """源数据修订使未签布置的旧计算失效；已签布置锚定旧版本，不受影响。

    calculating 的在途方案不立即改状态，留给 worker 按 checksum/版本判定
    “迟到结果不能恢复旧资格”；其余可审签/失败/已失效方案立即标记失效。
    保留失效方案的旧版本/checksum 锚点用于追溯（不等于遗留方案），仅清除
    已失效的计算证据；重算时由入队逻辑重新锚定当前完整版本。
    """
    for l in s.scalars(select(Layout).where(Layout.status.in_(['ready', 'failed', 'invalidated']))):
        if l.status == 'signed':
            continue
        l.status = 'invalidated'
        l.evidence = None
        l.invalid_reason = reason


def ensure_layout_columns(s):
    """轻量幂等迁移：为旧库补列（create_all 不修改既有表）。"""
    if not inspect(s.bind).has_table('layouts'):
        return
    existing = {c['name'] for c in inspect(s.bind).get_columns('layouts')}
    additions = {
        'source_version_id': 'INTEGER NULL',
        'invalid_reason': 'VARCHAR(200) NULL',
        'legacy_basis_missing': 'BOOLEAN NOT NULL DEFAULT 0',
        'basis_snapshot': 'JSON NULL',
    }
    for name, ddl in additions.items():
        if name not in existing:
            s.connection().exec_driver_sql(f'ALTER TABLE layouts ADD COLUMN {name} {ddl}')
