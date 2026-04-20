from app import db
from flask_sqlalchemy import SQLAlchemy


class DefectCode(db.Model):
    __tablename__ = 'DEFECT_CODE'

    ID = db.Column(db.NUMERIC, primary_key=True)
    PRODUCT_ID = db.Column(db.NUMERIC(11,0), db.ForeignKey('PRODUCT_INFO.ID'), nullable=False)
    GRADE = db.Column(db.VARCHAR(2))
    CODE = db.Column(db.NUMERIC(11,0))
    NAME = db.Column(db.VARCHAR(100))
    BSL = db.Column(db.FLOAT)

    product_info = db.relationship(
        'ProductInfo', 
        backref=db.backref('defect_codes', lazy=True),
        lazy='joined' 
    )

    def to_dict(self, include_product=False):
        """
        序列化模型为字典
        :param include_product: 是否包含关联的产品信息详情
        """
        data = {
            'id': self.ID,
            'product_id': self.PRODUCT_ID,
            'grade': self.GRADE,
            'code': self.CODE,
            'name': self.NAME,
            'bsl': self.BSL
        }
        
        # 如果需要包含产品详细信息
        if include_product and self.product_info:
            # 假设 ProductInfo 也有 to_dict 方法，或者直接访问属性
            data['product_info'] = self.product_info.to_dict() if hasattr(self.product_info, 'to_dict') else str(self.product_info)
            
        return data