from app.models.product import ProductInfo
from app import db
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import text

from app.utils.git_changelog import (
    COMMENT_MAX_BYTES,
    VERSION_MAX_LEN,
    clip_oracle_varchar,
)


def get_latest_software_info():
    """
    读取 SOFTWARE_INFO 单行配置（客户端版本卡控）。
    表可能有多行，只取第一行；列名 "comment" 为 Oracle 带引号标识符。

    Returns:
        (True, msg, {version, comment}) 或 (False, msg, None)
    """
    try:
        row = db.session.execute(
            text(
                'SELECT LATEST_VERSION, "comment" '
                'FROM SOFTWARE_INFO WHERE ROWNUM = 1'
            )
        ).first()
    except SQLAlchemyError as e:
        return False, f'查询失败: {e}', None
    except Exception as e:
        return False, f'查询失败: {e}', None

    if not row:
        return True, 'success', {'version': '', 'comment': ''}

    version = str(row[0] or '').strip()
    comment = ''
    if len(row) > 1 and row[1] is not None:
        comment = str(row[1]).strip()
    return True, 'success', {'version': version, 'comment': comment}


def _utf8_len(text: str) -> int:
    return len((text or '').encode('utf-8'))


def update_latest_software_info(version, comment):
    """
    更新 SOFTWARE_INFO（按单行使用）。无行则插入。
    version / comment 会截断到列长。
    """
    version = str(version or '').strip()
    comment = str(comment or '').strip()
    if not version:
        return False, '请填写版本号', None
    if len(version) > VERSION_MAX_LEN:
        return False, f'版本号不能超过 {VERSION_MAX_LEN} 个字符', None
    if _utf8_len(comment) > COMMENT_MAX_BYTES:
        return False, f'发布说明过长（最多 {COMMENT_MAX_BYTES} 字节）', None
    comment = clip_oracle_varchar(comment)

    params = {'version': version, 'comment': comment}
    try:
        result = db.session.execute(
            text(
                'UPDATE SOFTWARE_INFO '
                'SET LATEST_VERSION = :version, "comment" = :comment'
            ),
            params,
        )
        if int(result.rowcount or 0) <= 0:
            db.session.execute(
                text(
                    'INSERT INTO SOFTWARE_INFO (LATEST_VERSION, "comment") '
                    'VALUES (:version, :comment)'
                ),
                params,
            )
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        return False, f'保存失败: {e}', None
    except Exception as e:
        db.session.rollback()
        return False, f'保存失败: {e}', None
    return True, '保存成功', {'version': version, 'comment': comment}


def get_gross_die_value(product_id: str):
    """
    获取 Gross Die 信息
    优化逻辑：允许模糊匹配到多条记录，只要提取出的 gross_die 值唯一即可
    """
    if not product_id or not str(product_id).strip():
        return False, "Product ID cannot be empty"

    product_id = str(product_id).strip()
    try:
        # 1. 查出所有匹配的记录
        products = ProductInfo.query.filter(
            ProductInfo.PRODUCT_ID.contains(product_id)
        ).all()

        # 2. 没查到任何数据
        if not products:
            return False, "No product ID matched"

        # 3. 提取所有记录的 GROSS_DIE 并去重
        # 使用 set 去除重复项，比如 [10, 10, 10] -> {10}
        unique_gross_dies = {p.GROSS_DIE for p in products if p.GROSS_DIE is not None}

        # 4. 核心判断：如果去重后只有 1 个值，说明业务上是等价的，直接返回
        if len(unique_gross_dies) == 1:
            return True, unique_gross_dies.pop()
        
        # 5. 如果去重后有多个不同的值，说明数据存在真正的业务冲突
        return False, f"Found multiple different gross_die values: {list(unique_gross_dies)}. Please be more specific."

    except SQLAlchemyError as e:
        return False, f"Database error: {str(e)}"
    except Exception as e:
        return False, f"Unknown Error: {e}"


def get_ftp_status():
    """探活数据分析用 testlog FTP。成功返回 (True, msg, payload)。"""
    from app.utils.FtpPool import FTP_DOWN_IMPACT, testlog_ftp_pool

    try:
        data = testlog_ftp_pool.check_status()
    except Exception as e:
        data = {
            'available': False,
            'host': getattr(testlog_ftp_pool, 'host', ''),
            'latency_ms': None,
            'impact': FTP_DOWN_IMPACT,
        }
        return True, f'FTP 探活异常: {e}', data
    msg = 'FTP 可用' if data.get('available') else 'FTP 不可用'
    return True, msg, data