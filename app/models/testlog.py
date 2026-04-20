"""
FT_WLT_TESTLOG表的映射
"""
from app import db
from sqlalchemy import Column, Integer, String, Date, Sequence
from datetime import date

class FtWltTestlog(db.Model):
    # 指定表名和 Schema
    __tablename__ = 'FT_WLT_TESTLOG'
    
    # ID: NUMBER, 对应 Oracle 序列
    # 注意：Oracle 需要显式指定序列默认值
    ID = Column(Integer, primary_key=True, server_default=Sequence('FT_WLT_LOG_SEQ', schema='FT_OWEN').next_value())
    
    # CREATE_TIME: DATE, 默认 SYSDATE
    CREATE_TIME = Column(Date, nullable=False)
    
    # WAFER_ID: VARCHAR2(20)
    WAFER_ID = Column(String(20), nullable=False)
    
    # EQUIP_ID: VARCHAR2(20)
    EQUIP_ID = Column(String(20), nullable=False)
    
    # PRODUCT_ID: VARCHAR2(20)
    PRODUCT_ID = Column(String(20), nullable=False)
    
    # FTP_PATH: VARCHAR2(200)
    FTP_PATH = Column(String(200), nullable=False)
    
    # STEP: VARCHAR2(20) 
    # 注意：因为 'STEP' 在部分语境下可能是保留字，这里变量名用 STEP_VAL 或 STEP_NAME 避免冲突，但列名映射为 'STEP'
    STEP = Column('STEP', String(20), nullable=False)
    
    # TEST_DATE: DATE
    TEST_DATE = Column(Date, nullable=False)

    def __repr__(self):
        return f'<FtWltTestlog {self.WAFER_ID}>'
    
