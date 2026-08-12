import hashlib
import re

from app.models.user import User

_MD5_HEX_RE = re.compile(r'^[0-9a-fA-F]{32}$')


def normalize_login_password(password_input):
    """
    登录口令规范化为库中存储的 MD5 hex（小写）。

    客户端应传 MD5(明文) 的 32 位 hex，避免明文上送。
    兼容历史：若传入非 MD5 hex 的字符串，按明文再做一次 MD5。
    """
    if password_input is None:
        return None
    raw = str(password_input).strip()
    if not raw:
        return None
    if _MD5_HEX_RE.fullmatch(raw):
        return raw.lower()
    return hashlib.md5(raw.encode('utf-8')).hexdigest()


def password_matches(stored_password, password_input):
    """比对库中 PASSWORD 与登录入参（MD5 hex 或兼容明文）。"""
    if not stored_password:
        return False
    normalized = normalize_login_password(password_input)
    if not normalized:
        return False
    return str(stored_password).strip().lower() == normalized


def authenticate(employee_no, password):
    """
    验证用户登录信息
    :param employee_no: 工号
    :param password: 客户端应传 MD5(明文) hex；兼容明文
    :return: 返回 User 对象（如果成功），否则返回 None
    """
    user = User.query.filter_by(EMPLOYEE_NO=employee_no).first()
    if not user:
        return None

    if password_matches(user.PASSWORD, password):
        return user
    return None
