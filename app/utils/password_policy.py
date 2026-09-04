"""新密码规则：字母+数字、至少 6 位，禁止工号与常见弱密。"""
from __future__ import annotations

import re

MIN_PASSWORD_LENGTH = 6
_MD5_HEX_RE = re.compile(r'^[0-9a-fA-F]{32}$')

WEAK_PASSWORDS = frozenset({
    '123456',
    '1234567',
    '12345678',
    '123456789',
    '1234567890',
    '111111',
    '000000',
    '666666',
    '888888',
    '999999',
    'password',
    'password1',
    'admin',
    'admin123',
    'qwerty',
    'qwerty1',
    'abc123',
    '123123',
    '123abc',
    'a12345',
    'aaaaaa',
    'abcdef',
    '1qaz2wsx',
    'hold123',
    '1234ab',
    'qwe123',
    'abc1234',
})

_HAS_LETTER = re.compile(r'[A-Za-z]')
_HAS_DIGIT = re.compile(r'[0-9]')


def user_must_change_password(user) -> bool:
    """MUST_CHANGE_PWD 为 1/非 0，或列尚未赋值（None）时视为必须改密。"""
    if user is None:
        return True
    raw = getattr(user, 'MUST_CHANGE_PWD', None)
    if raw is None:
        return True
    try:
        return int(raw) != 0
    except (TypeError, ValueError):
        return True


def validate_new_password(employee_no, password) -> tuple[bool, str]:
    if password is None:
        return False, '请填写密码'
    raw = str(password)
    if not raw:
        return False, '请填写密码'
    if _MD5_HEX_RE.fullmatch(raw.strip()):
        return False, '请提交明文密码，不要传 MD5'

    if len(raw) < MIN_PASSWORD_LENGTH:
        return False, f'密码至少 {MIN_PASSWORD_LENGTH} 位'
    if not _HAS_LETTER.search(raw) or not _HAS_DIGIT.search(raw):
        return False, '密码须同时包含字母和数字'

    emp = str(employee_no or '').strip()
    if emp and raw.strip().lower() == emp.lower():
        return False, '密码不能与工号相同'

    if raw.strip().lower() in WEAK_PASSWORDS:
        return False, '密码过于简单，请更换'

    return True, ''
