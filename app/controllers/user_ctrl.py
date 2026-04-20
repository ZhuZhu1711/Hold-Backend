from app import db
from app.models import User
import hashlib
from werkzeug.security import generate_password_hash

def login(employee_no, password_input):
    """
    登录逻辑
    """
    user = User.query.filter_by(EMPLOYEE_NO=employee_no).first()
    
    if not user:
        return False, "用户不存在", None
        
    if user.check_password(password_input):
        return True, f"欢迎 {user.NAME}", {
            "id": user.ID,
            "name": user.NAME,
            "role": user.ROLE
        }
    else:
        return False, "密码错误", None
    
def login_logic(employee_no, password_input):
    """
    核心登录逻辑
    :return: (bool: 是否成功, str: 消息, dict: 用户信息或None)
    """
    user = User.query.filter_by(EMPLOYEE_NO=employee_no).first()
    
    if not user:
        return False, "用户不存在", None
        
    # 使用 MD5 校验密码
    input_pwd_hash = hashlib.md5(password_input.encode('utf-8')).hexdigest()
    
    if user.PASSWORD == input_pwd_hash:
        # 登录成功
        return True, "登录成功", {
            "id": user.ID,
            "name": user.NAME,
            "role": user.ROLE,
            "employee_no": user.EMPLOYEE_NO
        }
    else:
        return False, "密码错误", None

def create_user(employee_no, name, password, role=1):
    """
    创建用户逻辑
    """
    if User.query.filter_by(EMPLOYEE_NO=employee_no).first():
        return False, "用户已存在"
    
    hashed_pwd = generate_password_hash(password)
    new_user = User(EMPLOYEE_NO=employee_no, NAME=name, PASSWORD=hashed_pwd, ROLE=role)
    
    try:
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
    新增用户逻辑
    """
    try:
        # 1. 检查工号是否已存在
        existing_user = User.query.filter_by(EMPLOYEE_NO=data['employee_no']).first()
        if existing_user:
            return False, "工号已存在"

        # 2. 创建新用户对象
        new_user = User()
        new_user.EMPLOYEE_NO = data['employee_no']
        new_user.NAME = data['name']
        new_user.ROLE = data.get('role', 1) # 默认为普通用户
        
        # 3. 使用模型自带的方法设置加密密码
        new_user.set_password(data['password'])

        # 4. 保存到数据库
        db.session.add(new_user)
        db.session.commit()
        
        return True, "用户添加成功"
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
    