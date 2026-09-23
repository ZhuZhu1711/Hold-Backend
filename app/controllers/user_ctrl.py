from app import db
from app.models import User
from app.models.role import Role
from app.controllers.auth_ctrl import normalize_login_password, password_matches
from app.utils.auth_decorators import ROLE_ENGINEER, ROLE_PRODUCTION, ROLE_QUALITY, ROLE_ROOT
from app.utils.password_policy import user_must_change_password, validate_new_password

# 登录与页面权限仍按这些编号写死；目录里可以改说明，但不能删。
BUILTIN_ROLE_IDS = frozenset({ROLE_ROOT, ROLE_ENGINEER, ROLE_QUALITY, ROLE_PRODUCTION})
ROLE_DESC_MAX_BYTES = 100
ROLE_ID_MAX = 99999999999
EMPLOYEE_NO_MAX = 20
NAME_MAX = 20


def normalize_role_id(raw):
    """解析角色编号。成功返回 (int, '')，失败返回 (None, 原因)。"""
    if isinstance(raw, bool) or raw is None:
        return None, '请填写角色编号'
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None, '请填写角色编号'
        if not text.isdigit():
            return None, '角色编号须为非负整数'
        value = int(text)
    elif isinstance(raw, int):
        value = raw
    else:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None, '角色编号须为非负整数'
        if value != raw:
            return None, '角色编号须为非负整数'
    if value < 0 or value > ROLE_ID_MAX:
        return None, '角色编号超出范围'
    return value, ''


def normalize_role_desc(raw):
    """解析角色说明。成功返回 (str, '')，失败返回 (None, 原因)。"""
    text = str(raw or '').strip()
    if not text:
        return None, '请填写角色说明'
    if len(text.encode('utf-8')) > ROLE_DESC_MAX_BYTES:
        return None, '角色说明过长（最多 100 字节）'
    return text, ''


def role_exists(role_id):
    return Role.query.filter(Role.ROLE_ID == role_id).first() is not None


def _user_counts_by_role():
    rows = db.session.query(User.ROLE, db.func.count(User.ID)).group_by(User.ROLE).all()
    return {int(role): int(count) for role, count in rows}


def list_roles():
    """角色目录，含占用人数。"""
    try:
        rows = Role.query.order_by(Role.ROLE_ID.asc(), Role.ID.asc()).all()
        counts = _user_counts_by_role()
        data = []
        for row in rows:
            role_id = int(row.ROLE_ID)
            data.append({
                'id': int(row.ID),
                'role_id': role_id,
                'role_desc': row.ROLE_DESC,
                'user_count': counts.get(role_id, 0),
                'builtin': role_id in BUILTIN_ROLE_IDS,
            })
        return True, '获取成功', data
    except Exception as e:
        db.session.rollback()
        return False, str(e), []


def add_role(data):
    """新增角色。ROLE_ID 唯一，主键 ID 取当前最大值 + 1。"""
    data = data or {}
    role_id, msg = normalize_role_id(data.get('role_id'))
    if role_id is None:
        return False, msg
    desc, msg = normalize_role_desc(data.get('role_desc'))
    if desc is None:
        return False, msg
    try:
        if Role.query.filter(Role.ROLE_ID == role_id).first():
            return False, '角色编号已存在'
        next_id = db.session.query(db.func.max(Role.ID)).scalar()
        row = Role(ID=int(next_id or 0) + 1, ROLE_ID=role_id, ROLE_DESC=desc)
        db.session.add(row)
        db.session.commit()
        return True, '角色已添加'
    except Exception as e:
        db.session.rollback()
        return False, str(e)


def update_role(role_pk, data):
    """只改说明。编号被用户表引用，不在这里改。"""
    data = data or {}
    desc, msg = normalize_role_desc(data.get('role_desc'))
    if desc is None:
        return False, msg
    try:
        row = Role.query.get(role_pk)
        if not row:
            return False, '角色不存在'
        row.ROLE_DESC = desc
        db.session.commit()
        return True, '角色已更新'
    except Exception as e:
        db.session.rollback()
        return False, str(e)


def remove_role(role_pk):
    """角色目录不允许删除。"""
    return False, '角色不可删除'


def login(employee_no, password_input):
    """
    登录逻辑
    """
    user = User.query.filter_by(EMPLOYEE_NO=employee_no).first()
    
    if not user:
        return False, "用户不存在", None
        
    if password_matches(user.PASSWORD, password_input):
        return True, f"欢迎 {user.NAME}", {
            "id": user.ID,
            "name": user.NAME,
            "role": user.ROLE,
            "must_change_password": user_must_change_password(user),
        }
    else:
        return False, "密码错误", None
    
def login_logic(employee_no, password_input):
    """
    核心登录逻辑。
    password_input：客户端应传 MD5(明文) 的 32 位 hex，避免明文上送。
    :return: (bool: 是否成功, str: 消息, dict: 用户信息或None)
    """
    user = User.query.filter_by(EMPLOYEE_NO=employee_no).first()

    if not user:
        return False, "用户不存在", None

    if password_matches(user.PASSWORD, password_input):
        return True, "登录成功", {
            "id": user.ID,
            "name": user.NAME,
            "role": user.ROLE,
            "employee_no": user.EMPLOYEE_NO,
            "must_change_password": user_must_change_password(user),
        }
    return False, "密码错误", None

def create_user(employee_no, name, password, role=1):
    """
    创建用户逻辑。密码须明文，校验通过后存 MD5 hex。
    """
    ok, msg = validate_new_password(employee_no, password)
    if not ok:
        return False, msg
    if User.query.filter_by(EMPLOYEE_NO=employee_no).first():
        return False, "用户已存在"

    new_user = User(EMPLOYEE_NO=employee_no, NAME=name, ROLE=role)
    try:
        new_user.set_password(password)
        new_user.MUST_CHANGE_PWD = 0
        db.session.add(new_user)
        db.session.commit()
        return True, "创建成功"
    except Exception as e:
        db.session.rollback()
        return False, str(e)
    
def get_all_users(search="", sort_by="employee_no", order="asc", role=""):
    """
    从数据库获取用户列表（支持搜索、按角色筛选和排序）
    :param search: 搜索关键词（工号或姓名）
    :param sort_by: 排序字段 ('employee_no' 或 'name')
    :param order: 排序方向 ('asc' 或 'desc')
    :param role: 角色编号；空字符串表示不限
    """
    role_id = None
    if role is not None and str(role).strip() != '':
        role_id, msg = normalize_role_id(role)
        if role_id is None:
            return False, '角色无效', []
    try:
        # 1. 构建基础查询
        query = User.query
        if role_id is not None:
            query = query.filter(User.ROLE == role_id)

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
  
def list_engineers():
    """产品工程师名单（id / 工号 / 姓名），供 root 按待办筛选。"""
    try:
        users = (
            User.query.filter(User.ROLE == ROLE_ENGINEER)
            .order_by(User.EMPLOYEE_NO.asc(), User.ID.asc())
            .all()
        )
        data = [
            {
                'id': user.ID,
                'employee_no': user.EMPLOYEE_NO,
                'name': user.NAME,
            }
            for user in users
        ]
        return True, '获取成功', data
    except Exception as e:
        db.session.rollback()
        return False, str(e), []


def add_user(data):
    """
    新增用户。密码须明文，校验字母+数字且至少 6 位后存 MD5 hex。
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
    role, _role_msg = normalize_role_id(role_raw)
    if role is None:
        return False, '角色无效'
    ok, policy_msg = validate_new_password(employee_no, password)
    if not ok:
        return False, policy_msg
    if not role_exists(role):
        return False, '角色无效，请先在角色列表中维护'

    try:
        existing_user = User.query.filter_by(EMPLOYEE_NO=employee_no).first()
        if existing_user:
            return False, '工号已存在'

        new_user = User()
        new_user.EMPLOYEE_NO = employee_no
        new_user.NAME = name
        new_user.ROLE = role
        new_user.MUST_CHANGE_PWD = 0
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


def change_password(user_id, old_password, new_password):
    """
    已登录用户修改自己的密码。新密码须明文。
    """
    if not user_id:
        return False, '请先登录'
    if new_password is None or str(new_password) == '':
        return False, '请填写新密码'

    try:
        user = User.query.get(user_id)
        if not user:
            return False, '用户不存在'
        if not password_matches(user.PASSWORD, old_password):
            return False, '原密码错误'
        ok, policy_msg = validate_new_password(user.EMPLOYEE_NO, new_password)
        if not ok:
            return False, policy_msg
        if password_matches(user.PASSWORD, new_password):
            return False, '新密码不能与原密码相同'
        user.set_password(new_password)
        user.MUST_CHANGE_PWD = 0
        db.session.commit()
        return True, '密码已修改'
    except Exception as e:
        db.session.rollback()
        return False, str(e)
    