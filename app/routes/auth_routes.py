from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, flash, current_app
from app.config import Config
from app.controllers import user_ctrl, auth_ctrl
from app.models import User
from app.utils.auth_decorators import (
    login_required,
    ROLE_ROOT,
    ROLE_ENGINEER,
    ROLE_PRODUCTION,
    ROLE_QUALITY,
    home_endpoint_for_role,
    current_role_name,
    session_must_change_password,
)
from app.utils.password_policy import user_must_change_password
from app.utils.rate_limit import (
    LOGIN_TOO_FREQUENT_MSG,
    check_login_rate,
    clear_login_failures,
    client_ip_from_request,
    record_login_failure,
)
from app.utils.web_sso import TicketError, consume_ticket, issue_ticket

_LOGIN_ALLOWED_ROLES = (ROLE_ROOT, ROLE_ENGINEER, ROLE_PRODUCTION, ROLE_QUALITY)
_LOGIN_ROLE_DENIED_MSG = '权限不足：仅超级管理员、产品工程师、生产或质量部可登录后台'


auth_bp = Blueprint('auth', __name__, url_prefix='/')


def _as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _establish_session(
    user_id, user_name, employee_no, role, remember=True, must_change_password=False,
):
    """
    写入登录 Session。
    remember=True 时启用持久 Cookie（自动登录，默认 30 天，请求滑动续期）。
    """
    session.clear()
    session['user_id'] = user_id
    session['user_name'] = user_name
    session['employee_no'] = employee_no
    session['role'] = role
    session['must_change_password'] = bool(must_change_password)
    session.permanent = bool(remember)


def _home_path_for_role(role=None):
    return url_for(home_endpoint_for_role(role))


def _post_login_path(role=None, must_change_password=None):
    if must_change_password is None:
        must_change_password = session_must_change_password()
    if must_change_password:
        return url_for('auth.change_password_page')
    return _home_path_for_role(role)


def _login_rate_block(employee_no):
    limited, retry_after = check_login_rate(client_ip_from_request(request), employee_no)
    if not limited:
        return None
    return max(1, int(retry_after or 1))


def _json_429(retry_after):
    resp = jsonify({'code': 429, 'msg': LOGIN_TOO_FREQUENT_MSG})
    resp.status_code = 429
    resp.headers['Retry-After'] = str(retry_after)
    return resp


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
        return redirect(_post_login_path(session.get('role')))
    return redirect(url_for('auth.login_page'))


@auth_bp.route('/login', methods=['GET', 'POST'])
def login_page():
    # 已登录：按角色跳转首页（含持久 Cookie 自动登录）
    if session.get('user_id'):
        return redirect(_post_login_path(session.get('role')))

    if request.method == 'POST':
        employee_no = request.form.get('employee_no')
        password = request.form.get('password')
        remember = _as_bool(request.form.get('remember'), default=True)

        retry_after = _login_rate_block(employee_no)
        if retry_after is not None:
            flash(LOGIN_TOO_FREQUENT_MSG, 'danger')
            return render_template('login.html'), 429

        user = auth_ctrl.authenticate(employee_no, password)

        if user:
            if user.ROLE not in _LOGIN_ALLOWED_ROLES:
                flash(_LOGIN_ROLE_DENIED_MSG, 'danger')
            else:
                must_change = user_must_change_password(user)
                clear_login_failures(employee_no)
                _establish_session(
                    user.ID, user.NAME, user.EMPLOYEE_NO, user.ROLE,
                    remember=remember, must_change_password=must_change,
                )
                if must_change:
                    flash('请先修改密码后再使用系统', 'warning')
                    return redirect(url_for('auth.change_password_page'))
                flash('登录成功', 'success')
                return redirect(url_for(home_endpoint_for_role(user.ROLE)))
        else:
            record_login_failure(employee_no)
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
      employee_no, password  (password 为 MD5(明文) 的 32 位 hex，勿传明文)
      remember  是否持久 Cookie 自动登录，默认 true
    """
    data = request.get_json(silent=True) or {}
    emp_no = data.get('employee_no')
    password = data.get('password')
    remember = _as_bool(data.get('remember'), default=True)

    retry_after = _login_rate_block(emp_no)
    if retry_after is not None:
        return _json_429(retry_after)

    success, msg, user_data = user_ctrl.login_logic(emp_no, password)

    if not success:
        record_login_failure(emp_no)
        return jsonify({'code': 401, 'msg': msg}), 401

    role = user_data.get('role')
    if role not in _LOGIN_ALLOWED_ROLES:
        return jsonify({
            'code': 403,
            'msg': _LOGIN_ROLE_DENIED_MSG,
        }), 403

    must_change = bool(user_data.get('must_change_password'))
    clear_login_failures(emp_no)
    _establish_session(
        user_data['id'],
        user_data['name'],
        user_data.get('employee_no'),
        role,
        remember=remember,
        must_change_password=must_change,
    )
    redirect_url = _post_login_path(role, must_change)
    return jsonify({
        'code': 200,
        'msg': '请先修改密码' if must_change else msg,
        'data': {
            **user_data,
            'must_change_password': must_change,
            'remember': bool(remember),
            'redirect': redirect_url,
        },
    })


@auth_bp.route('api/web-sso-ticket', methods=['POST'])
@login_required
def api_web_sso_ticket():
    """
    已登录客户端申请打开 Web 后台的一次性票据。
    URL: /api/web-sso-ticket
    """
    max_age = int(getattr(Config, 'WEB_SSO_TICKET_MAX_AGE', 60))
    ticket = issue_ticket(
        current_app.config['SECRET_KEY'],
        {
            'user_id': session.get('user_id'),
            'user_name': session.get('user_name'),
            'employee_no': session.get('employee_no'),
            'role': session.get('role'),
        },
        max_age=max_age,
    )
    path = url_for('auth.web_sso', ticket=ticket)
    return jsonify({
        'code': 200,
        'msg': 'ok',
        'data': {
            'ticket': ticket,
            'path': path,
            'url': url_for('auth.web_sso', ticket=ticket, _external=True),
            'expires_in': max_age,
        },
    })


@auth_bp.route('web-sso', methods=['GET'])
def web_sso():
    """
    浏览器消费一次性票据，写入 hold_session 后按角色进入后台。
    URL: /web-sso?ticket=...
    """
    if session.get('user_id'):
        return redirect(_post_login_path(session.get('role')))

    max_age = int(getattr(Config, 'WEB_SSO_TICKET_MAX_AGE', 60))
    try:
        user = consume_ticket(
            current_app.config['SECRET_KEY'],
            request.args.get('ticket'),
            max_age=max_age,
        )
    except TicketError as exc:
        flash(str(exc), 'warning')
        return redirect(url_for('auth.login_page'))

    if user.get('role') not in _LOGIN_ALLOWED_ROLES:
        flash(_LOGIN_ROLE_DENIED_MSG, 'danger')
        return redirect(url_for('auth.login_page'))

    db_user = User.query.get(user['user_id']) if user.get('user_id') else None
    must_change = user_must_change_password(db_user)
    _establish_session(
        user['user_id'],
        user['user_name'],
        user['employee_no'],
        user['role'],
        remember=True,
        must_change_password=must_change,
    )
    return redirect(_post_login_path(user['role'], must_change))


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
        if session.get('role') == ROLE_QUALITY:
            return redirect(url_for('quality.dashboard'))
        return redirect(url_for('auth.login_page'))

    return render_template(
        'dashboard.html',
        user_name=session.get('user_name'),
        role_name=current_role_name(),
    )


@auth_bp.route('change-password', methods=['GET'])
@login_required
def change_password_page():
    """
    修改密码页面（全角色）。MUST_CHANGE_PWD=1 时登录后强制进入。
    URL: /change-password
    """
    return render_template(
        'change_password.html',
        user_name=session.get('user_name'),
        employee_no=session.get('employee_no') or '',
        must_change=bool(session.get('must_change_password')),
    )


@auth_bp.route('api/change-password', methods=['POST'])
@login_required
def api_change_password():
    """
    已登录用户修改密码。
    URL: /api/change-password
    Body: old_password, new_password, confirm_password（明文）
    """
    data = request.get_json(silent=True) or request.form or {}
    old_password = data.get('old_password')
    new_password = data.get('new_password')
    confirm = data.get('confirm_password')
    if confirm is not None and str(confirm) != str(new_password or ''):
        return jsonify({'code': 400, 'msg': '两次输入的新密码不一致'}), 400

    success, msg = user_ctrl.change_password(
        session.get('user_id'), old_password, new_password,
    )
    if not success:
        return jsonify({'code': 400, 'msg': msg}), 400

    session['must_change_password'] = False
    return jsonify({
        'code': 200,
        'msg': msg,
        'data': {
            'must_change_password': False,
            'redirect': _home_path_for_role(session.get('role')),
        },
    })


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
