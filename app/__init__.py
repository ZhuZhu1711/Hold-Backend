from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from app.config import Config

# 1. 创建数据库实例
# 此时还没有绑定 app，只是一个全局对象
db = SQLAlchemy()

def create_app():
    # 2. 创建 Flask 应用实例
    app = Flask(__name__)
    
    # 3. 配置数据库
    app.config['SQLALCHEMY_DATABASE_URI'] = Config.SQLALCHEMY_DATABASE_URI
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = Config.SQLALCHEMY_TRACK_MODIFICATIONS
    app.config['SECRET_KEY'] = Config.SQLALCHEMY_SECRET_KEY
    
    # 4. 将 db 实例与 app 绑定
    db.init_app(app)
    
    # 5. 导入模型
    # 必须在 db.init_app 之后，且使用 with app.app_context() 确保上下文存在
    # 这样可以避免循环导入的问题
    with app.app_context():
        from app.models import User, ProductInfo
        
    return app