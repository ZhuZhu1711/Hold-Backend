from datetime import datetime

from sqlalchemy import Sequence

from app import db


class ClientError(db.Model):
    __tablename__ = 'FT_CLIENT_ERROR'

    ID = db.Column(
        db.Numeric(38, 0),
        primary_key=True,
        server_default=Sequence('SEQ_FT_CLIENT_ERROR', schema='FT_OWEN').next_value(),
    )
    REPORT_ID = db.Column(db.String(36), unique=True, nullable=False)
    OCCURRED_AT = db.Column(db.DateTime)
    RECEIVED_AT = db.Column(db.DateTime, default=datetime.now)
    EVENT_TYPE = db.Column(db.String(32))
    EXCEPTION_TYPE = db.Column(db.String(256))
    MESSAGE = db.Column(db.String(1024))
    STACK_TRACE = db.Column(db.Text)
    HOSTNAME = db.Column(db.String(128))
    OS_USER = db.Column(db.String(64))
    EMPLOYEE_NO = db.Column(db.String(20))
    USER_ID = db.Column(db.Numeric(38, 0))
    APP_MODE = db.Column(db.String(16))
    FROZEN = db.Column(db.Numeric(1, 0), default=0)
    CLIENT_IP = db.Column(db.String(64))

    def to_dict(self):
        return {
            'id': int(self.ID) if self.ID is not None else None,
            'report_id': self.REPORT_ID or '',
            'event_type': self.EVENT_TYPE or '',
        }
