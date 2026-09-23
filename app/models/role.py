from app import db
from sqlalchemy import Column, Integer, String


class Role(db.Model):
    """角色目录。USERS.ROLE 存的是 ROLE_ID，不是本表主键 ID。"""

    __tablename__ = 'ROLE'
    __table_args__ = {'quote': True}

    ID = Column(Integer, primary_key=True)
    ROLE_ID = Column(Integer, unique=True, nullable=False)
    ROLE_DESC = Column(String(100), nullable=False)
