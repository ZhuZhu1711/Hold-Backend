from sqlalchemy import Identity

from app import db


class DefectCodeSyncLog(db.Model):
    __tablename__ = 'DEFECT_CODE_SYNC_LOG'

    # 用表内 IDENTITY，避免 create_all 先建表、序列却还不存在（ORA-02289）。
    ID = db.Column(db.Numeric(38, 0), Identity(start=1), primary_key=True)
    RUN_AT = db.Column(db.DateTime, nullable=False)
    PRODUCT_CODE = db.Column(db.String(40), nullable=False)
    PROD_LINE = db.Column(db.String(8))
    PRODUCT_INFO_ID = db.Column(db.Numeric(38, 0))
    STATUS = db.Column(db.String(16), nullable=False)
    SRC_ROWS = db.Column(db.Numeric(11, 0), default=0)
    INSERTED = db.Column(db.Numeric(11, 0), default=0)
    UPDATED = db.Column(db.Numeric(11, 0), default=0)
    DELETED = db.Column(db.Numeric(11, 0), default=0)
    CONFLICTS = db.Column(db.Numeric(11, 0), default=0)
    MESSAGE = db.Column(db.String(1000))
    STARTED_AT = db.Column(db.DateTime)
    FINISHED_AT = db.Column(db.DateTime)

    __table_args__ = (
        db.Index('IDX_DEFECT_SYNC_LOG_RUN', 'RUN_AT', 'PRODUCT_CODE'),
    )

    def to_dict(self):
        return {
            'id': int(self.ID) if self.ID is not None else None,
            'run_at': _fmt_dt(self.RUN_AT),
            'product_code': self.PRODUCT_CODE or '',
            'prod_line': self.PROD_LINE or '',
            'product_info_id': (
                int(self.PRODUCT_INFO_ID) if self.PRODUCT_INFO_ID is not None else None
            ),
            'status': self.STATUS or '',
            'src_rows': _fmt_int(self.SRC_ROWS),
            'inserted': _fmt_int(self.INSERTED),
            'updated': _fmt_int(self.UPDATED),
            'deleted': _fmt_int(self.DELETED),
            'conflicts': _fmt_int(self.CONFLICTS),
            'message': self.MESSAGE or '',
            'started_at': _fmt_dt(self.STARTED_AT),
            'finished_at': _fmt_dt(self.FINISHED_AT),
        }


def _fmt_dt(value):
    if value is None:
        return ''
    return value.strftime('%Y-%m-%d %H:%M:%S')


def _fmt_int(value):
    if value is None:
        return 0
    return int(value)
