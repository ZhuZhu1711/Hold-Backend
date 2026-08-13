from app import db
from app.models import User
from app.controllers.auth_ctrl import normalize_login_password
from app.utils.auth_decorators import ROLE_NAMES

ALLOWED_ROLES = set(ROLE_NAMES.keys())
EMPLOYEE_NO_MAX = 20
NAME_MAX = 20


def login(employee_no, password_input):
    """
    登录逻辑
    """
    from app.controllers.auth_ctrl import password_matches

    user = User.query.filter_by(EMPLOYEE_NO=employee_no).first()
    
    if not user:
        return False, "用户不存在", None
        
    if password_matches(user.PASSWORD, password_input):
        return True, f"欢迎 {user.NAME}", {
            "id": user.ID,
            "name": user.NAME,
            "role": user.ROLE
        }
    else:
        return False, "密码错误", None
    
def login_logic(employee_no, password_input):
    """
    核心登录逻辑。
    password_input：客户端应传 MD5(明文) 的 32 位 hex，避免明文上送。
    :return: (bool: 是否成功, str: 消息, dict: 用户信息或None)
    """
    from app.controllers.auth_ctrl import password_matches

    user = User.query.filter_by(EMPLOYEE_NO=employee_no).first()

    if not user:
        return False, "用户不存在", None

    if password_matches(user.PASSWORD, password_input):
        return True, "登录成功", {
            "id": user.ID,
            "name": user.NAME,
            "role": user.ROLE,
            "employee_no": user.EMPLOYEE_NO
        }
    return False, "密码错误", None

def create_user(employee_no, name, password, role=1):
    """
    创建用户逻辑。密码按登录约定存 MD5 hex。
    """
    if User.query.filter_by(EMPLOYEE_NO=employee_no).first():
        return False, "用户已存在"

    new_user = User(EMPLOYEE_NO=employee_no, NAME=name, ROLE=role)
    try:
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        return True, "创建成功"
    except Exception as e:
        db.session.rollback()
        return False, str(e)
    
def get_all_users(search="", sort_by="employee_no", order="asc"):
    """
    从数据库获取用户列表（支持搜索和排序）
    :param search: 搜索关键词（工号或姓名）
    :param sort_by: 排序字段 ('employee_no' 或 'name')
    :param order: 排序方向 ('asc' 或 'desc')
    """
    try:
        # 1. 构建基础查询
        query = User.query

        # 2. 处理搜索逻辑：如果有关键词，筛选工号或姓名包含该词的记录
        if search:
            search_filter = f"%{search}%"
            query = query.filter(
                db.or_(
                    User.EMPLOYEE_NO.like(search_filter),
                    User.NAME.like(search_filter)
                )
            )

        # 3. 处理排序逻辑
        # 默认按工号排序
        if sort_by == 'name':
            sort_column = User.NAME
        else:
            sort_column = User.EMPLOYEE_NO

        # 处理升降序
        if order == 'desc':
            query = query.order_by(sort_column.desc())
        else:
            query = query.order_by(sort_column.asc())

        # 4. 执行查询
        users = query.all()
        
        return True, "获取成功", users
    except Exception as e:
        db.session.rollback()
        return False, str(e), []
  
def add_user(data):
    """
    新增用户。密码按登录约定存 MD5 hex（兼容明文或已 MD5 的 hex）。
    """
    data = data or {}
    employee_no = str(data.get('employee_no') or '').strip()
    name = str(data.get('name') or '').strip()
    password = data.get('password')
    role_raw = data.get('role', 1)

    if not employee_no:
        return False, '请填写工号'
    if len(employee_no) > EMPLOYEE_NO_MAX:
        return False, f'工号最长 {EMPLOYEE_NO_MAX} 个字符'
    if not name:
        return False, '请填写姓名'
    if len(name) > NAME_MAX:
        return False, f'姓名最长 {NAME_MAX} 个字符'
    if not normalize_login_password(password):
        return False, '请填写密码'
    try:
        role = int(role_raw)
    except (TypeError, ValueError):
        return False, '角色无效'
    if role not in ALLOWED_ROLES:
        return False, '角色无效，须为超级管理员 / 产品工程师 / 质量部 / 生产'

    try:
        existing_user = User.query.filter_by(EMPLOYEE_NO=employee_no).first()
        if existing_user:
            return False, '工号已存在'

        new_user = User()
        new_user.EMPLOYEE_NO = employee_no
        new_user.NAME = name
        new_user.ROLE = role
        new_user.set_password(password)

        db.session.add(new_user)
        db.session.commit()
        return True, '用户添加成功'
    except Exception as e:
        db.session.rollback()
        return False, str(e)

def remove_user(user_id):
    """
    删除用户逻辑
    """
    try:
        # 1. 查找用户
        user = User.query.get(user_id)
        if not user:
            return False, "用户不存在"
        
        if user.ROLE == 0:
            return False, "禁止删除超级管理员账号（root）"

        # 2. 删除
        db.session.delete(user)
        db.session.commit()
        
        return True, "删除成功"
    except Exception as e:
        db.session.rollback()
        return False, str(e)
    