"""
登录与角色权限装饰器。

角色约定（USERS.ROLE）：
  0 = root（最高权限，可查看全部数据）
  1 = 普通工程师
"""
from functools import wraps

from flask import session, redirect, url_for, flash, request, jsonify

ROLE_ROOT = 0
ROLE_ENGINEER = 1

ROLE_NAMES = {
    ROLE_ROOT: '超级管理员',
    ROLE_ENGINEER: '普通工程师',
}


def _wants_json():
    """API / JSON 请求返回 JSON 错误，页面请求则重定向。"""
    if '/api' in (request.path or ''):
        return True
    if request.is_json:
        return True
    accept = (request.accept_mimetypes.best or '')
    return 'application/json' in accept and 'text/html' not in accept


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('user_id'):
            if _wants_json():
                return jsonify({'code': 401, 'msg': '请先登录'}), 401
            flash('请先登录', 'warning')
            return redirect(url_for('auth.login_page'))
        return f(*args, **kwargs)
    return decorated_function


def role_required(*allowed_roles):
    """
    要求已登录且 ROLE 在 allowed_roles 中。
    用法: @role_required(ROLE_ROOT) 或 @role_required(ROLE_ROOT, ROLE_ENGINEER)
    """
    allowed = set(allowed_roles)

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
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

            return f(*args, **kwargs)
        return decorated_function
    return decorator


def root_required(f):
    """仅 root（ROLE=0）可访问。"""
    return role_required(ROLE_ROOT)(f)


def is_root():
    return session.get('role') == ROLE_ROOT


def current_role_name():
    return ROLE_NAMES.get(session.get('role'), '未知角色')
