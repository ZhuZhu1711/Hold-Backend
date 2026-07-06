"""
TEST_WAFER  TEST_BINCODE  表的映射
"""
import json
from app import db
from sqlalchemy import Column, Integer, String, Date, DateTime, Sequence, ForeignKey
from sqlalchemy.orm import relationship


class TestWafer(db.Model):
    # 指定表名和 Schema
    __tablename__ = 'TEST_WAFER'
    __table_args__ = {'schema': 'FT_OWEN'}
    
    # ID: NUMBER(38,0), 对应 Oracle 序列
    ID = Column(Integer, primary_key=True, server_default=Sequence('SEQ_TEST_WAFER', schema='FT_OWEN').next_value())
    
    # WAFER_ID: VARCHAR2(50)
    WAFER_ID = Column(String(50), nullable=False)
    
    # OPERATION_ID: VARCHAR2(20)
    OPERATION_ID = Column(String(20), nullable=False)
    
    # FT_TIME: DATE
    FT_TIME = Column(Date, nullable=True)
    
    # PRODUCT_ID: VARCHAR2(20)
    PRODUCT_ID = Column(String(20), nullable=False)
    
    # SECOND_CODE: VARCHAR2(20)
    SECOND_CODE = Column(String(20), nullable=True)
    
    # TEST_PROGRAM: VARCHAR2(128)
    TEST_PROGRAM = Column(String(128), nullable=True)
    
    # RCV_TIME: DATE
    RCV_TIME = Column(Date, nullable=True)
    
    # LOT_ID: VARCHAR2(50)
    LOT_ID = Column(String(50), nullable=True)
    
    # WAFER_NO: NUMBER(10,0)
    WAFER_NO = Column(Integer, nullable=True)
    
    # LOCATION: VARCHAR2(20)
    LOCATION = Column(String(20), nullable=True)
    
    # GROSS_DIE: NUMBER(10,0)
    GROSS_DIE = Column(Integer, nullable=True)
    
    # WAFER_NUM: NUMBER(38,0)
    WAFER_NUM = Column(Integer, nullable=True)
    
    # ROUTE: VARCHAR2(20)
    ROUTE = Column(String(20), nullable=True)
    
    # EQUIP_ID: VARCHAR2(20)
    EQUIP_ID = Column(String(20), nullable=True)
    
    # ASS_VENDER: VARCHAR2(20)
    ASS_VENDER = Column(String(20), nullable=True)
    
    # PACK_LOTID: VARCHAR2(50)
    PACK_LOTID = Column(String(50), nullable=True)
    
    # PASS_DIE: NUMBER(38,0)
    PASS_DIE = Column(Integer, nullable=True)
    
    # NG_NUM: NUMBER(38,0)
    NG_NUM = Column(Integer, nullable=True)
    
    # CP: NUMBER(10,0)
    CP = Column(Integer, nullable=True)
    
    # RECORD_DTTM: TIMESTAMP(6), 默认 SYSDATE
    RECORD_DTTM = Column(DateTime, nullable=False, server_default='SYSDATE')
    
    # GRADES_QTY: VARCHAR2(1024)
    GRADES_QTY = Column(String(1024), nullable=True)

    def __repr__(self):
        try:
            grades = json.loads(self.GRADES_QTY) if self.GRADES_QTY else {}
            grades_str = json.dumps(grades, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            grades_str = self.GRADES_QTY
        return f'<TestWafer WAFER_ID={self.WAFER_ID} PRODUCT_ID={self.PRODUCT_ID} OPERATION_ID={self.OPERATION_ID} FT_TIME={self.FT_TIME} GRADES_QTY={grades_str}>'
    
    
class TestBincode(db.Model):
    # 指定表名和 Schema
    __tablename__ = 'TEST_BINCODE'
    __table_args__ = {'schema': 'FT_OWEN'}
    
    # ID: NUMBER(38,0), 对应 Oracle 序列
    ID = Column(Integer, primary_key=True, server_default=Sequence('SEQ_TEST_BINCODE', schema='FT_OWEN').next_value())
    
    # TEST_WAFER_SEQ: NUMBER(38,0), 外键关联 TEST_WAFER.ID
    TEST_WAFER_SEQ = Column(Integer, ForeignKey('FT_OWEN.TEST_WAFER.ID', ondelete='CASCADE'), nullable=False)
    
    # WAFER_ID: VARCHAR2(50)
    WAFER_ID = Column(String(50), nullable=False)
    
    # BIN_CODE: NUMBER(6,0)
    BIN_CODE = Column(Integer, nullable=True)
    
    # BIN_CODE_QTY: NUMBER(7,0)
    BIN_CODE_QTY = Column(Integer, nullable=True)
    
    # 定义与 TestWafer 的关系（可选，方便联表查询）
    test_wafer = relationship('TestWafer', backref='bincodes')

    def __repr__(self):
        return f'<TestBincode WAFER_ID={self.WAFER_ID} BIN_CODE={self.BIN_CODE} BIN_CODE_QTY={self.BIN_CODE_QTY}>'
    
    