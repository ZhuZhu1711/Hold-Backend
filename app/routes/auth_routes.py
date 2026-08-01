from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, flash
from app.controllers import user_ctrl, auth_ctrl
from functools import wraps


auth_bp = Blueprint('auth', __name__, url_prefix='/')

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 检查 session 中是否有 user_id
        if not session.get('user_id'):
            flash('请先登录', 'warning') # 可选：显示提示信息
            return redirect(url_for('auth.login_page'))
        return f(*args, **kwargs)
    return decorated_function

# ==========================================
# 页面路由
# ==========================================

@auth_bp.route('/')
def index():
    """
    首页重定向
    URL: /
    """
    # 访问根目录时，自动跳转到登录页
    return redirect(url_for('auth.login_page'))

@auth_bp.route('/login', methods=['GET', 'POST'])
def login_page():
    # 1. 如果已经登录了，直接跳转到仪表盘（实现“免密”体验）
    if session.get('user_id'):
        return redirect(url_for('auth.dashboard'))

    # 2. 处理登录提交
    if request.method == 'POST':
        employee_no = request.form.get('employee_no')
        password = request.form.get('password')
        
        # 调用控制器验证
        user = auth_ctrl.authenticate(employee_no, password)
        
        if user:
            session['user_id'] = user.ID
            session['user_name'] = user.NAME
            session['employee_no'] = user.EMPLOYEE_NO
            session['role'] = user.ROLE

            if user.ROLE != 0:
                session.clear()
                flash('权限不足：后台仅限管理员(root)登录', 'danger')
            else:
                flash('登录成功', 'success')
                return redirect(url_for('auth.dashboard'))
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
    """
    data = request.get_json()
    emp_no = data.get('employee_no')
    password = data.get('password')
    
    # 调用控制器逻辑
    success, msg, user_data = user_ctrl.login_logic(emp_no, password)
    
    if success:
        # Web 后台页面仍仅 root；API 登录对全部角色开放（流转查询等只读接口不按角色限制）
        session['user_id'] = user_data['id']
        session['user_name'] = user_data['name']
        session['role'] = user_data['role']
        session['employee_no'] = user_data.get('employee_no')
        return jsonify({'code': 200, 'msg': msg, 'data': user_data})
    else:
        return jsonify({'code': 401, 'msg': msg}), 401
    
@auth_bp.route('dashboard')
@login_required
def dashboard():
    """
    管理后台主页
    URL: /dashboard
    """
    # 1. 权限检查
    if not session.get('user_id') or session.get('role') != 0:
        return redirect(url_for('auth.login_page'))
    
    # 2. 准备数据传递给模板
    context = {
        "user_name": session.get('user_name'),
        "role_name": "超级管理员" if session.get('role') == 0 else "普通用户"
    }

    # 3. 渲染模板
    return render_template('dashboard.html', **context)

@auth_bp.route('logout')
def logout():
    """
    退出登录
    URL: /logout
    """
    session.clear() 
    flash('已安全退出', 'info')
    return redirect(url_for('auth.login_page'))