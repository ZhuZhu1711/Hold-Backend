from app import db
from sqlalchemy import Column, Integer, String


class User(db.Model):
    __tablename__ = 'USERS'

    ID = Column(Integer, primary_key=True)
    EMPLOYEE_NO = Column(String(20), unique=True, nullable=False)
    NAME = Column(String(20), nullable=False)
    PASSWORD = Column(String(100), nullable=False)
    ROLE = Column(Integer, nullable=False, default=1)
    MUST_CHANGE_PWD = Column(Integer, nullable=False, default=1)

    products = db.relationship('ProductInfo', back_populates='owner', lazy=True)

    def set_password(self, password):
        from app.controllers.auth_ctrl import normalize_login_password
        hashed = normalize_login_password(password)
        if not hashed:
            raise ValueError('密码不能为空')
        self.PASSWORD = hashed

    def check_password(self, password):
        from app.controllers.auth_ctrl import password_matches
        return password_matches(self.PASSWORD, password)
