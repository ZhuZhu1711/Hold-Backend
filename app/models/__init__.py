# 必须先导入 db，确保基类准备好
from app import db 

# 导入具体的模型类，触发模型注册
from .user import User
from .product import ProductInfo
from .rawdata import TestWafer, TestBincode

__all__ = ['User', 'ProductInfo', 'db']