from sqlalchemy import String,Integer,JSON,ForeignKey
from sqlalchemy.orm import Mapped,mapped_column
from .db import Base

class SourceVersion(Base):
    """来源（测点标定 + 扫角压力/升力）的不可变版本。

    每次源数据修订产生一个新版本并冻结当时的完整快照与 SHA256。
    已审签布置永久引用其计算/签署所依据的版本，后续修订不影响旧版本。
    版本身份是自增 seq；内容可能改回历史状态，故 checksum 不加唯一约束，
    仅用于内容完整性校验，锚定以版本 id 为准。
    """
    __tablename__='source_versions'
    id:Mapped[int]=mapped_column(primary_key=True)
    seq:Mapped[int]=mapped_column(Integer,unique=True)
    checksum:Mapped[str]=mapped_column(String(64),index=True)
    change_note:Mapped[str]=mapped_column(String(200),default='')
    created_by:Mapped[str]=mapped_column(String(100))
    created_at:Mapped[str]=mapped_column(String(40))
    # 冻结的完整原始依据：全部测点、扫角与逐点压力，后续不得改写
    source_snapshot:Mapped[dict]=mapped_column(JSON)
    probe_count:Mapped[int]=mapped_column(Integer)
    sweep_count:Mapped[int]=mapped_column(Integer)
    pressure_count:Mapped[int]=mapped_column(Integer)
