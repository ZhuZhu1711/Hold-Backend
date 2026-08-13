from flask import Blueprint, render_template, request, jsonify, session
from app.controllers import user_ctrl
from app.utils.auth_decorators import current_role_name, root_required

user_bp = Blueprint('user', __name__, url_prefix='/admin/users')

# --- 页面路由 ---
@user_bp.route('')
@root_required
def user_list_page():
    """
    用户管理页面
    URL: /admin/users
    """
    return render_template(
        'users/list.html',
        user_name=session.get('user_name'),
        role_name=current_role_name(),
    )

# --- 数据接口路由 ---
@user_bp.route('/api', methods=['GET'])
@root_required
def get_users():
    """
    获取用户列表数据（支持搜索和排序）
    URL: /admin/users/api
    参数: ?search=xxx&sort_by=employee_no&order=asc
    """
    # 获取 URL 参数
    search = request.args.get('search', '')
    sort_by = request.args.get('sort_by', 'employee_no')
    order = request.args.get('order', 'asc')

    success, msg, users = user_ctrl.get_all_users(search, sort_by, order)
    
    if success:
        user_list = [
            {
                'id': user.ID,
                'employee_no': user.EMPLOYEE_NO,
                'name': user.NAME,
                'role': user.ROLE
            } for user in users
        ]
        return jsonify({'code': 200, 'msg': msg, 'data': user_list})
    else:
        return jsonify({'code': 500, 'msg': msg, 'data': []})

@user_bp.route('/api', methods=['POST'])
@root_required
def create_user():
    """
    新增用户
    URL: /admin/users/api
    数据格式: { "employee_no": "E1001", "name": "张三", "password": "123456", "role": 1 }
    """
    data = request.get_json()
    
    # 简单校验
    if not data:
        return jsonify({'code': 400, 'msg': '请求数据为空'}), 400
        
    success, msg = user_ctrl.add_user(data)
    
    if success:
        return jsonify({'code': 200, 'msg': msg})
    else:
        return jsonify({'code': 400, 'msg': msg}), 400

@user_bp.route('/api/<int:user_id>', methods=['DELETE'])
@root_required
def delete_user(user_id):
    """
    删除用户
    URL: /admin/users/api/1
    """
    success, msg = user_ctrl.remove_user(user_id)
    
    if success:
        return jsonify({'code': 200, 'msg': msg})
    else:
        return jsonify({'code': 400, 'msg': msg}), 400
    