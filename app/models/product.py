from app import db
from sqlalchemy import Column, Integer, String, Date, ForeignKey

class ProductInfo(db.Model):
    __tablename__ = 'PRODUCT_INFO'
    
    ID = Column(Integer, primary_key=True)
    PRODUCT_ID = Column(String(20), nullable=False, unique=True)
    GROSS_DIE = Column(Integer)
    LINE_TYPE = Column(Integer)
    UPDATE_DTTM = Column(Date)
    
    PRO_ENG_ID = Column(Integer, ForeignKey('USERS.ID'))

    owner = db.relationship('User', back_populates='products')