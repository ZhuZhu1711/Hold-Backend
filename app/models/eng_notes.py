from app import db
from sqlalchemy import Sequence


class EngNote(db.Model):
    __tablename__ = 'FT_ENG_NOTES'

    ID = db.Column(
        db.Numeric(38, 0),
        primary_key=True,
        server_default=Sequence('SEQ_FT_ENG_NOTES', schema='FT_OWEN').next_value(),
    )
    PRODUCT_ID = db.Column(db.Numeric(38, 0), db.ForeignKey('PRODUCT_INFO.ID'))
    NOTE = db.Column(db.String(500))
    IS_AVAILABLE = db.Column(db.Numeric(38, 0), default=1)
    TYPE = db.Column(db.String(100))

    product_info = db.relationship(
        'ProductInfo',
        backref=db.backref('eng_notes', lazy=True),
        lazy='joined',
    )

    def to_dict(self):
        return {
            'id': int(self.ID) if self.ID is not None else None,
            'product_id': int(self.PRODUCT_ID) if self.PRODUCT_ID is not None else None,
            'note': self.NOTE or '',
            'is_available': int(self.IS_AVAILABLE) if self.IS_AVAILABLE is not None else 0,
            'type': self.TYPE or '',
        }
