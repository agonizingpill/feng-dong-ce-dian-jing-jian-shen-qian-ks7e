import logging
from sqlalchemy import inspect
from .db import engine, Session
from . import version_service as vs
from .version_models import SourceVersion
from .models import Layout


def run_migrations():
    """幂等升级：补列 → 建表 → 基线来源版本 → 遗留旧方案标注。"""
    with Session() as s:
        vs.ensure_layout_columns(s)
        s.commit()
    # create_all 仅创建缺失的新表 source_versions，不动既有表
    from .db import Base
    from . import version_models  # noqa: F401 确保模型已注册
    Base.metadata.create_all(engine, tables=[SourceVersion.__table__], checkfirst=True)
    with Session() as s:
        vs.mark_legacy_layouts(s)
        vs.bootstrap_source_versions(s)
        s.commit()
    logging.info('source version migrations complete')
