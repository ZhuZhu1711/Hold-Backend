import os
import sys

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from app.config import Config

# 1. 创建数据库实例
# 此时还没有绑定 app，只是一个全局对象
db = SQLAlchemy()


def _package_dir():
    """Flask 模板/静态资源目录。PyInstaller 冻结后在 sys._MEIPASS/app。"""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, 'app')
    return os.path.dirname(os.path.abspath(__file__))


def create_app():
    # 2. 创建 Flask 应用实例
    pkg = _package_dir()
    app = Flask(
        __name__,
        template_folder=os.path.join(pkg, 'templates'),
        static_folder=os.path.join(pkg, 'static'),
    )
    
    # 3. 配置数据库与 Session Cookie
    app.config['SQLALCHEMY_DATABASE_URI'] = Config.SQLALCHEMY_DATABASE_URI
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = Config.SQLALCHEMY_TRACK_MODIFICATIONS
    app.config['SECRET_KEY'] = Config.SQLALCHEMY_SECRET_KEY
    app.config['PERMANENT_SESSION_LIFETIME'] = Config.PERMANENT_SESSION_LIFETIME
    app.config['SESSION_REFRESH_EACH_REQUEST'] = Config.SESSION_REFRESH_EACH_REQUEST
    app.config['SESSION_COOKIE_NAME'] = Config.SESSION_COOKIE_NAME
    app.config['SESSION_COOKIE_HTTPONLY'] = Config.SESSION_COOKIE_HTTPONLY
    app.config['SESSION_COOKIE_SAMESITE'] = Config.SESSION_COOKIE_SAMESITE
    app.config['SESSION_COOKIE_SECURE'] = Config.SESSION_COOKIE_SECURE
    app.config['HOLD_API_TOKEN'] = Config.HOLD_API_TOKEN

    # 4. 将 db 实例与 app 绑定
    db.init_app(app)

    @app.after_request
    def _set_frame_guard_headers(response):
        """禁止第三方站点用 iframe 嵌套本站页面，缓解点击劫持。"""
        response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        csp = response.headers.get('Content-Security-Policy', '')
        if 'frame-ancestors' not in csp:
            extra = "frame-ancestors 'self'"
            response.headers['Content-Security-Policy'] = (
                f'{csp}; {extra}' if csp else extra
            )
        return response

    from app.utils.mail_alert import install_severe_error_hooks
    install_severe_error_hooks()
    
    # 5. 导入模型
    # 必须在 db.init_app 之后，且使用 with app.app_context() 确保上下文存在
    # 这样可以避免循环导入的问题
    with app.app_context():
        from app.models import User, ProductInfo
        
    return app