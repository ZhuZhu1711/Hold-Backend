import hashlib
from app.models.user import User

def authenticate(employee_no, password):
    """
    验证用户登录信息
    :param employee_no: 工号
    :param password: 用户输入的明文密码
    :return: 返回 User 对象（如果成功），否则返回 None
    """
    # 1. 根据工号查找用户
    user = User.query.filter_by(EMPLOYEE_NO=employee_no).first()
    
    # 2. 如果用户不存在，直接返回 None
    if not user:
        return None
    
    # 3. 计算输入密码的 MD5 值
    # 注意：MD5 需要传入 bytes，所以用 password.encode('utf-8')
    input_password_hash = hashlib.md5(password.encode('utf-8')).hexdigest()
    
    # 4. 对比数据库里的哈希值和刚才计算的哈希值
    if user.PASSWORD_HASH == input_password_hash:
        return user
    else:
        return None