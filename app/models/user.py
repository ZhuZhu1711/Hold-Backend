from app import db
from sqlalchemy import Column, Integer, String
from werkzeug.security import generate_password_hash, check_password_hash

class User(db.Model):
    __tablename__ = 'USERS'
    
    ID = Column(Integer, primary_key=True)
    EMPLOYEE_NO = Column(String(20), unique=True, nullable=False)
    NAME = Column(String(50), nullable=False)
    PASSWORD = Column(String(128), nullable=False)
    ROLE = Column(Integer, default=1)

    products = db.relationship('ProductInfo', back_populates='owner', lazy=True)

    def set_password(self, password):
        self.PASSWORD_HASH = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.PASSWORD_HASH, password)