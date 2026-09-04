"""
登录与角色权限装饰器。

角色约定（USERS.ROLE）：
  0 = root（最高权限，可查看全部数据）
  1 = 产品工程师（仅所属型号）
  8 = 质量部（只读：已处置物料报表，不参与决策）
  9 = 生产（查看生产节点 Hold / 流转）

双通道：
  1) Session Cookie（人类用户登录）
  2) Header X-Hold-Token 与配置 HOLD_API_TOKEN 一致时，跳过登录与角色校验，
     无 Session 则注入系统用户（SYSTEM_USER_ID + ROLE_ROOT）。
"""
import hmac
from functools import wraps

from flask import session, redirect, url_for, flash, request, jsonify, current_app

from app.config import Config

API_TOKEN_HEADER = 'X-Hold-Token'
API_TOKEN_USER_NAME = 'API_TOKEN'
API_TOKEN_EMPLOYEE_NO = 'API_TOKEN'

ROLE_ROOT = 0
ROLE_ENGINEER = 1
ROLE_QUALITY = 8
ROLE_PRODUCTION = 9

ROLE_NAMES = {
    ROLE_ROOT: '超级管理员',
    ROLE_ENGINEER: '产品工程师',
    ROLE_QUALITY: '质量部',
    ROLE_PRODUCTION: '生产',
}


def _configured_api_token():
    """优先读 Flask app.config，便于测试覆盖；未设置则回退 Config。"""
    try:
        token = current_app.config.get('HOLD_API_TOKEN')
        if token is not None:
            return str(token).strip()
    except RuntimeError:
        pass
    return str(getattr(Config, 'HOLD_API_TOKEN', '') or '').strip()


def _api_token_ok():
    expected = _configured_api_token()
    if not expected:
        return False
    provided = request.headers.get(API_TOKEN_HEADER) or ''
    if not isinstance(provided, str):
        provided = str(provided)
    if not provided or len(provided) != len(expected):
        return False
    return hmac.compare_digest(provided, expected)


def _establish_api_token_session():
    """无登录态时注入系统用户，供处置/报表读取 session。"""
    if session.get('user_id'):
        return
    session['user_id'] = Config.SYSTEM_USER_ID
    session['user_name'] = API_TOKEN_USER_NAME
    session['employee_no'] = API_TOKEN_EMPLOYEE_NO
    session['role'] = ROLE_ROOT
    session.permanent = False


def _authorize_api_token():
    if not _api_token_ok():
        return False
    _establish_api_token_session()
    return True


def _wants_json():
    """API / JSON 请求返回 JSON 错误，页面请求则重定向。"""
    if '/api' in (request.path or ''):
        return True
    if request.is_json:
        return True
    accept = (request.accept_mimetypes.best or '')
    return 'application/json' in accept and 'text/html' not in accept


MUST_CHANGE_ALLOWED_ENDPOINTS = frozenset({
    'auth.change_password_page',
    'auth.api_change_password',
    'auth.logout',
})
MUST_CHANGE_MSG = '请先在网页修改密码'


def session_must_change_password():
    """升级前的持久 Cookie 没有该键，视为必须改密。"""
    if 'must_change_password' not in session:
        return True
    return bool(session.get('must_change_password'))


def _reject_if_must_change():
    """未改密用户只能访问改密页 / 改密 API / 登出。"""
    if not session_must_change_password():
        return None
    if request.endpoint in MUST_CHANGE_ALLOWED_ENDPOINTS:
        return None
    if _wants_json():
        return jsonify({
            'code': 403,
            'msg': MUST_CHANGE_MSG,
            'data': {'must_change_password': True},
        }), 403
    flash(MUST_CHANGE_MSG, 'warning')
    return redirect(url_for('auth.change_password_page'))


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if _authorize_api_token():
            return f(*args, **kwargs)
        if not session.get('user_id'):
            if _wants_json():
                return jsonify({'code': 401, 'msg': '请先登录'}), 401
            flash('请先登录', 'warning')
            return redirect(url_for('auth.login_page'))
        blocked = _reject_if_must_change()
        if blocked is not None:
            return blocked
        return f(*args, **kwargs)
    return decorated_function


def role_required(*allowed_roles):
    """
    要求已登录且 ROLE 在 allowed_roles 中。
    用法: @role_required(ROLE_ROOT) 或 @role_required(ROLE_ROOT, ROLE_ENGINEER)
    固定 Token 通道跳过角色校验。
    """
    allowed = set(allowed_roles)

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if _authorize_api_token():
                return f(*args, **kwargs)
            if not session.get('user_id'):
                if _wants_json():
                    return jsonify({'code': 401, 'msg': '请先登录'}), 401
                flash('请先登录', 'warning')
                return redirect(url_for('auth.login_page'))

            role = session.get('role')
            if role not in allowed:
                if _wants_json():
                    return jsonify({'code': 403, 'msg': '权限不足'}), 403
                flash('权限不足', 'danger')
                return redirect(url_for('auth.login_page'))

            blocked = _reject_if_must_change()
            if blocked is not None:
                return blocked
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def root_required(f):
    """仅 root（ROLE=0）可访问。"""
    return role_required(ROLE_ROOT)(f)


def engineer_required(f):
    """仅产品工程师（ROLE=1）可访问。"""
    return role_required(ROLE_ENGINEER)(f)


def production_required(f):
    """仅生产（ROLE=9）可访问。"""
    return role_required(ROLE_PRODUCTION)(f)


def quality_required(f):
    """仅质量部（ROLE=8）可访问。"""
    return role_required(ROLE_QUALITY)(f)


def is_root():
    return session.get('role') == ROLE_ROOT


def is_engineer():
    return session.get('role') == ROLE_ENGINEER


def is_production():
    return session.get('role') == ROLE_PRODUCTION


def is_quality():
    return session.get('role') == ROLE_QUALITY


def current_role_name():
    return ROLE_NAMES.get(session.get('role'), '未知角色')


def home_endpoint_for_role(role=None):
    """按角色返回登录后首页 endpoint。"""
    if role is None:
        role = session.get('role')
    if role == ROLE_ENGINEER:
        return 'engineer.dashboard'
    if role == ROLE_PRODUCTION:
        return 'production.dashboard'
    if role == ROLE_QUALITY:
        return 'quality.dashboard'
    return 'auth.dashboard'
