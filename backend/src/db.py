import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
_url=os.environ['DATABASE_URL']
# MySQL 生产/容器使用 READ COMMITTED；SQLite 仅用于本地测试，不支持该隔离级别
_kwargs={'pool_pre_ping':True}
if not _url.startswith('sqlite'):
    _kwargs['isolation_level']='READ COMMITTED'
engine = create_engine(_url, **_kwargs)
Session = sessionmaker(engine, expire_on_commit=False)
class Base(DeclarativeBase): pass
def db_session():
    with Session() as s:
        yield s
