from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, flash
from app.controllers import user_ctrl, auth_ctrl
from app.utils.auth_decorators import (
    login_required,
    ROLE_ROOT,
    ROLE_ENGINEER,
    ROLE_PRODUCTION,
    home_endpoint_for_role,
    current_role_name,
)

_LOGIN_ALLOWED_ROLES = (ROLE_ROOT, ROLE_ENGINEER, ROLE_PRODUCTION)


auth_bp = Blueprint('auth', __name__, url_prefix='/')


def _as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _establish_session(user_id, user_name, employee_no, role, remember=True):
    """
    写入登录 Session。
    remember=True 时启用持久 Cookie（自动登录，默认 30 天，请求滑动续期）。
    """
    session.clear()
    session['user_id'] = user_id
    session['user_name'] = user_name
    session['employee_no'] = employee_no
    session['role'] = role
    session.permanent = bool(remember)


def _home_path_for_role(role=None):
    return url_for(home_endpoint_for_role(role))


# ==========================================
# 页面路由
# ==========================================

@auth_bp.route('/')
def index():
    """
    首页：已登录按角色进后台，否则去登录页（持久 Cookie 可自动登录）。
    URL: /
    """
    if session.get('user_id'):
        return redirect(url_for(home_endpoint_for_role()))
    return redirect(url_for('auth.login_page'))


@auth_bp.route('/login', methods=['GET', 'POST'])
def login_page():
    # 已登录：按角色跳转首页（含持久 Cookie 自动登录）
    if session.get('user_id'):
        return redirect(url_for(home_endpoint_for_role()))

    if request.method == 'POST':
        employee_no = request.form.get('employee_no')
        password = request.form.get('password')
        remember = _as_bool(request.form.get('remember'), default=True)

        user = auth_ctrl.authenticate(employee_no, password)

        if user:
            if user.ROLE not in _LOGIN_ALLOWED_ROLES:
                flash('权限不足：仅超级管理员、产品工程师或生产可登录后台', 'danger')
            else:
                _establish_session(
                    user.ID, user.NAME, user.EMPLOYEE_NO, user.ROLE, remember=remember
                )
                flash('登录成功', 'success')
                return redirect(url_for(home_endpoint_for_role(user.ROLE)))
        else:
            flash('工号或密码错误', 'danger')

    return render_template('login.html')


# ==========================================
# 数据接口路由
# ==========================================

@auth_bp.route('api/login', methods=['POST'])
def api_login():
    """
    处理登录请求
    URL: /api/login

    Body JSON:
      employee_no, password
      remember  是否持久 Cookie 自动登录，默认 true
    """
    data = request.get_json(silent=True) or {}
    emp_no = data.get('employee_no')
    password = data.get('password')
    remember = _as_bool(data.get('remember'), default=True)

    success, msg, user_data = user_ctrl.login_logic(emp_no, password)

    if not success:
        return jsonify({'code': 401, 'msg': msg}), 401

    role = user_data.get('role')
    if role not in _LOGIN_ALLOWED_ROLES:
        return jsonify({
            'code': 403,
            'msg': '权限不足：仅超级管理员、产品工程师或生产可登录后台',
        }), 403

    _establish_session(
        user_data['id'],
        user_data['name'],
        user_data.get('employee_no'),
        role,
        remember=remember,
    )
    redirect_url = _home_path_for_role(role)
    return jsonify({
        'code': 200,
        'msg': msg,
        'data': {
            **user_data,
            'remember': bool(remember),
            'redirect': redirect_url,
        },
    })


@auth_bp.route('dashboard')
@login_required
def dashboard():
    """
    管理后台主页（仅 root）
    URL: /dashboard
    """
    if session.get('role') != ROLE_ROOT:
        if session.get('role') == ROLE_ENGINEER:
            return redirect(url_for('engineer.dashboard'))
        if session.get('role') == ROLE_PRODUCTION:
            return redirect(url_for('production.dashboard'))
        return redirect(url_for('auth.login_page'))

    return render_template(
        'dashboard.html',
        user_name=session.get('user_name'),
        role_name=current_role_name(),
    )


@auth_bp.route('logout')
def logout():
    """
    退出登录：清空 Session，并清除持久 Cookie。
    URL: /logout
    """
    session.clear()
    # 确保响应不再带永久登录 Cookie
    session.permanent = False
    flash('已安全退出', 'info')
    return redirect(url_for('auth.login_page'))
