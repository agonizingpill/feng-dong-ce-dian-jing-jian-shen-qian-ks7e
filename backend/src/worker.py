import os,json,logging
from pathlib import Path
import redis
from .db import Session
from .models import Layout
from .selection import calculate
from .services import source_checksum
from . import version_service as vs

ARTIFACT_DIR=os.environ.get('ARTIFACT_DIR','/artifacts')
r=redis.Redis.from_url(os.environ['REDIS_URL'],decode_responses=True)
Path(ARTIFACT_DIR).mkdir(exist_ok=True)

def process_job(layout_id):
    """处理一个计算作业。迟到结果按来源 checksum/版本判定失效，不能恢复旧资格。"""
    with Session() as s:
        l=s.get(Layout,int(layout_id))
        if not l or l.status!='calculating':
            return
        anchored_checksum=l.source_checksum
        anchored_version=l.source_version_id
        try:
            calculate(s,l)
            current_checksum=source_checksum(s)
            current_version=vs.latest_version(s)
            stale=(anchored_checksum is not None and anchored_checksum!=current_checksum) or \
                  (anchored_version is not None and (not current_version or current_version.id!=anchored_version))
            if stale:
                # 修订、计算并发：迟到结果不能恢复旧资格，标记失效并要求重算。
                # 恢复入队时锚定的旧版本/checksum 用于追溯（calculate 已改写为当前值）。
                s.flush()
                l.status='invalidated';l.evidence=None
                l.source_checksum=anchored_checksum;l.source_version_id=anchored_version
                l.invalid_reason=vs.REASON_STALE_RESULT
                s.commit()
                logging.warning('layout %s stale result rejected',l.id)
                return
            l.status='ready';l.invalid_reason=None;s.commit()
            Path(f'{ARTIFACT_DIR}/layout-{l.id}.json').write_text(json.dumps(l.evidence,ensure_ascii=False))
        except Exception as ex:
            s.rollback();l=s.get(Layout,int(layout_id));l.status='failed';l.evidence={'error':str(ex)};l.invalid_reason=vs.REASON_CALC_FAILED;s.commit();logging.exception('selection failed')

if __name__=='__main__':
    while True:
        Path('/tmp/worker-heartbeat').touch()
        entry=r.blpop('tunnel-selection',timeout=2)
        if not entry:continue
        process_job(entry[1])
