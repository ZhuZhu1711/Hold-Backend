"""
Hold Record 处置流转业务逻辑。

规则来源：dispose_api.md
  - DISPOSE 行为码决定 NEXT_OWNER_ID / DISPOSED_OWNER_ID
  - ~ → PRODUCT_INFO.PRO_ENG_ID（缺省 SYSTEM_USER_ID）
  - 181 → PRODUCTION_OP_ID（生产 OP）
  - 1 → SYSTEM_USER_ID（系统）

同一事务内：插入 CIRCULATION_HISTORY + 回写 LAST_CIRCULATION_ID / STATUS。

DISPOSE_DETAIL 结构化规则（工程师降级/重测由服务端生成）：
  降级: DG:HA>F;FB>F
  重测(等级): RT:F,HA
  重测(WLT code): RT:CODE=123
  放行/可靠性分析: 可选自由备注
"""
import re
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.config import Config
from app.utils.auth_decorators import ROLE_ROOT
from app.utils.database_util import format_wafer_id_display


# ---------- DISPOSE 行为码 ----------
DISPOSE_CREATE = 0
DISPOSE_RELEASE = 1          # 放行
DISPOSE_DOWNGRADE = 2        # 降级
DISPOSE_RETEST = 3           # 重测
DISPOSE_ANALYZE = 5          # 可靠性分析
DISPOSE_ANALYZE_RETURN = 6   # 分析(返回) — 生产侧
DISPOSE_TRANSFER = 7         # 转交
DISPOSE_ROLLBACK = 8         # 回退
DISPOSE_PROD_ANALYZE_RETURN = 66  # 分析(返回) — 生产
DISPOSE_CLOSE = 99           # 关闭

DISPOSE_DETAIL_MAX_LEN = 1024
RECORD_TYPE_WLT = 2

DISPOSE_LABELS = {
    DISPOSE_CREATE: '创建',
    DISPOSE_RELEASE: '放行',
    DISPOSE_DOWNGRADE: '降级',
    DISPOSE_RETEST: '重测',
    DISPOSE_ANALYZE: '可靠性分析',
    DISPOSE_ANALYZE_RETURN: '分析(返回)',
    DISPOSE_TRANSFER: '转交',
    DISPOSE_ROLLBACK: '回退',
    DISPOSE_PROD_ANALYZE_RETURN: '分析(返回)',
    DISPOSE_CLOSE: '关闭',
}

# 工程师可发起（当前 owner 应为工程师侧）
# 转交(7) 方案待定，暂时屏蔽，不放入 ENGINEER_DISPOSES
ENGINEER_DISPOSES = {
    DISPOSE_RELEASE,
    DISPOSE_DOWNGRADE,
    DISPOSE_RETEST,
    DISPOSE_ANALYZE,
    # DISPOSE_TRANSFER,  # TODO: 转交方案确定后再开放
}

# 生产 OP 可发起（当前 owner 应为生产 OP）
PRODUCTION_DISPOSES = {
    DISPOSE_ANALYZE_RETURN,
    DISPOSE_PROD_ANALYZE_RETURN,
    DISPOSE_ROLLBACK,
}

# 系统/root 可发起
SYSTEM_DISPOSES = {
    DISPOSE_CLOSE,
}

USER_DISPOSES = ENGINEER_DISPOSES | PRODUCTION_DISPOSES | SYSTEM_DISPOSES

_ALLOWED_HOLD_RECORD_TABLES = {'FT_HOLD_RECORD'}


def _production_op_id():
    return int(getattr(Config, 'PRODUCTION_OP_ID', 181) or 181)


def _system_user_id():
    return int(getattr(Config, 'SYSTEM_USER_ID', 1) or 1)


def _record_table():
    name = (getattr(Config, 'HOLD_RECORD_TABLE', None) or 'FT_HOLD_RECORD').upper()
    if name not in _ALLOWED_HOLD_RECORD_TABLES:
        raise ValueError(f'非法 HOLD_RECORD 表名: {name}')
    return name


def _row_to_dict(row):
    raw = dict(row._mapping)
    data = {}
    for key, value in raw.items():
        out_key = str(key).upper()
        if isinstance(value, datetime):
            data[out_key] = value.strftime('%Y-%m-%d %H:%M:%S')
        elif isinstance(value, date):
            data[out_key] = value.strftime('%Y-%m-%d')
        else:
            data[out_key] = value
    if 'WAFER_ID' in data:
        data['WAFER_ID'] = format_wafer_id_display(data['WAFER_ID'])
    return data


def _next_positive_seq(seq_name: str) -> int:
    allowed = {'SEQ_CIRCULATION'}
    if seq_name not in allowed:
        raise ValueError(f'非法序列名: {seq_name}')
    for _ in range(5):
        val = db.session.execute(text(f'SELECT {seq_name}.NEXTVAL FROM DUAL')).scalar()
        if val is not None and int(val) > 0:
            return int(val)
    raise RuntimeError(f'序列 {seq_name} 连续返回非法 ID(<=0)')


def _lookup_pro_eng_id(product_id: str) -> int:
    row = db.session.execute(
        text("""
            SELECT PRO_ENG_ID
            FROM PRODUCT_INFO
            WHERE PRODUCT_ID = :product_id
              AND ROWNUM = 1
        """),
        {'product_id': product_id},
    ).fetchone()
    if row and row[0] is not None:
        return int(row[0])
    return _system_user_id()


def parse_grade_num(raw):
    """
    解析 GRADE_NUM 文本（如 F:1151,HA:49）为 [{grade, qty}, ...]。
    含字母 F（不区分大小写）的等级排在前面，组内按等级名排序。
    """
    if raw is None:
        return []
    text_val = str(raw).strip()
    if not text_val:
        return []

    items = []
    for part in re.split(r'[,，;；]+', text_val):
        part = part.strip()
        if not part:
            continue
        if ':' in part:
            grade, qty = part.split(':', 1)
        elif '：' in part:
            grade, qty = part.split('：', 1)
        else:
            grade, qty = part, ''
        grade = grade.strip()
        qty = qty.strip()
        if not grade:
            continue
        items.append({'grade': grade, 'qty': qty})

    def sort_key(it):
        g = it['grade']
        has_f = 0 if 'F' in g.upper() else 1
        return (has_f, g.upper(), g)

    items.sort(key=sort_key)
    return items


def format_grade_num_display(raw):
    """将 GRADE_NUM 格式化为展示文本（F 优先）。"""
    items = parse_grade_num(raw)
    if not items:
        return ''
    parts = []
    for it in items:
        if it['qty'] != '':
            parts.append(f"{it['grade']}:{it['qty']}")
        else:
            parts.append(it['grade'])
    return ', '.join(parts)


def _norm_grade_token(value):
    if value is None:
        return ''
    return str(value).strip()


def build_dispose_detail(
    dispose,
    dispose_detail=None,
    downgrades=None,
    retest_grades=None,
    retest_code=None,
    record_type=None,
):
    """
    按处置行为生成 DISPOSE_DETAIL。
    结构化字段优先；放行可用工程备注文本；可靠性分析可用自由备注。
    降级格式: DG:HA>F;FB>F 或附工程备注 DG:HA>F||备注文本
    成功返回 (True, detail_or_None)；失败返回 (False, err_msg)。
    """
    try:
        dispose = int(dispose)
    except (TypeError, ValueError):
        return False, 'dispose 无效'

    free = None
    if dispose_detail is not None:
        free = str(dispose_detail).strip() or None

    if dispose == DISPOSE_DOWNGRADE:
        pairs = []
        seen_from = set()
        for item in (downgrades or []):
            if not isinstance(item, dict):
                return False, 'downgrades 格式无效'
            src = _norm_grade_token(item.get('from') if 'from' in item else item.get('from_grade'))
            dst = _norm_grade_token(item.get('to') if 'to' in item else item.get('to_grade'))
            if not src or not dst:
                return False, '降级映射源/目标等级不能为空'
            if src == dst:
                # 未变更的等级跳过（默认源→源）
                continue
            key = src.upper()
            if key in seen_from:
                return False, f'同一源等级只能降一次: {src}'
            seen_from.add(key)
            pairs.append(f'{src}>{dst}')
        if not pairs:
            return False, '降级须至少变更一个等级（不能全部保持源→源）'
        detail = 'DG:' + ';'.join(pairs)
        if free:
            detail = f'{detail}||{free}'
    elif dispose == DISPOSE_RETEST:
        code_raw = None if retest_code is None else str(retest_code).strip()
        grades = []
        for g in (retest_grades or []):
            token = _norm_grade_token(g)
            if token:
                grades.append(token)

        try:
            rt = int(record_type) if record_type is not None and str(record_type).strip() != '' else None
        except (TypeError, ValueError):
            return False, 'record_type 无效'

        if code_raw and grades:
            return False, 'WLT 重测等级与 code 互斥，只能选一种'
        if code_raw:
            if rt is not None and rt != RECORD_TYPE_WLT:
                return False, '仅 WLT 处置单支持按 code 重测'
            if not re.fullmatch(r'\d+', code_raw):
                return False, '重测 code 须为数字'
            detail = f'RT:CODE={code_raw}'
        elif grades:
            # 去重保序
            uniq = []
            seen = set()
            for g in grades:
                k = g.upper()
                if k in seen:
                    continue
                seen.add(k)
                uniq.append(g)
            detail = 'RT:' + ','.join(uniq)
        else:
            return False, '重测须选择等级或填写 code'
    elif dispose in (DISPOSE_RELEASE, DISPOSE_ANALYZE):
        detail = free
    else:
        # 其它行为（生产/系统）仍允许自由备注
        detail = free

    if detail is not None and len(detail) > DISPOSE_DETAIL_MAX_LEN:
        return False, f'dispose_detail 最长 {DISPOSE_DETAIL_MAX_LEN} 字符'
    return True, detail


def enrich_record_grades(record):
    """为 record dict 附加 GRADE_NUM_DISPLAY / GRADES。"""
    if not record:
        return record
    raw = record.get('GRADE_NUM')
    record['GRADE_NUM_DISPLAY'] = format_grade_num_display(raw) or (str(raw).strip() if raw else '')
    record['GRADES'] = parse_grade_num(raw)
    return record


def _load_record(record_id: int):
    record_table = _record_table()
    row = db.session.execute(
        text(f"""
            SELECT
                r.ID, r.PRODUCT_ID, r.STATION, r.EQUIP_ID, r.LOT_ID, r.WAFER_ID,
                r.HOLD_CODE, r.HOLD_REASON, r.SOURCE, r.SECOND_CODE, r.ROUTE_ID,
                r.GRADE_NUM, r.RECORD_TYPE, r.STATUS, r.LAST_CIRCULATION_ID, r.HOLD_DTTM
            FROM {record_table} r
            WHERE r.ID = :rid
        """),
        {'rid': record_id},
    ).fetchone()
    if not row:
        return None
    return enrich_record_grades(_row_to_dict(row))


def _load_circulation(circ_id):
    if not circ_id:
        return None
    row = db.session.execute(
        text("""
            SELECT
                ID, HOLD_RECORD_ID, DISPOSED_OWNER_ID, DISPOSE,
                NEXT_OWNER_ID, DISPOSE_SOURCE, DISPOSE_DTTM,
                DISPOSE_TYPE, DISPOSE_DETAIL
            FROM CIRCULATION_HISTORY
            WHERE ID = :cid
        """),
        {'cid': int(circ_id)},
    ).fetchone()
    return _row_to_dict(row) if row else None


def _resolve_owners(dispose: int, product_id: str, actor_user_id: int):
    """
    按 dispose_api.md 计算 NEXT_OWNER_ID / DISPOSED_OWNER_ID。
    ~ → PRO_ENG_ID；固定 ID 按表写入。
    工程师侧 DISPOSED_OWNER 优先记实际操作人（便于审计），
    若操作人为 root 代操作则回退为 PRO_ENG_ID。
    """
    prod_op = _production_op_id()
    system_id = _system_user_id()
    pro_eng_id = _lookup_pro_eng_id(product_id)

    def engineer_disposed():
        if int(actor_user_id) == system_id:
            return pro_eng_id
        return int(actor_user_id)

    if dispose in (
        DISPOSE_RELEASE, DISPOSE_DOWNGRADE, DISPOSE_RETEST, DISPOSE_ANALYZE,
    ):
        return prod_op, engineer_disposed()

    if dispose == DISPOSE_TRANSFER:
        return pro_eng_id, engineer_disposed()

    if dispose in (DISPOSE_ANALYZE_RETURN, DISPOSE_PROD_ANALYZE_RETURN, DISPOSE_ROLLBACK):
        return pro_eng_id, prod_op

    if dispose == DISPOSE_CLOSE:
        return None, system_id

    raise ValueError(f'不支持的处置行为: {dispose}')


def _actor_may_dispose(dispose: int, actor_user_id: int, actor_role, current_owner_id):
    """校验操作人是否有权执行该处置。"""
    is_root = actor_role == ROLE_ROOT
    prod_op = _production_op_id()

    if dispose not in USER_DISPOSES:
        return False, f'不支持的处置行为: {dispose}'

    if dispose in SYSTEM_DISPOSES and not is_root:
        return False, '关闭仅系统/管理员可执行'

    if not is_root:
        if current_owner_id is None:
            return False, '记录无当前负责人，无法处置'
        if int(actor_user_id) != int(current_owner_id):
            return False, '仅当前负责人可处置该记录'

        if dispose in ENGINEER_DISPOSES and int(actor_user_id) == prod_op:
            return False, '生产账号不可执行工程师处置'

        if dispose in PRODUCTION_DISPOSES and int(actor_user_id) != prod_op:
            return False, '仅生产 OP 可执行该处置'

    return True, ''


def list_dispose_actions(group=None):
    """
    返回可发起的处置行为说明。
    group: engineer | production | system | None(全部用户可发起)
    """
    if group == 'engineer':
        codes = ENGINEER_DISPOSES
    elif group == 'production':
        codes = PRODUCTION_DISPOSES
    elif group == 'system':
        codes = SYSTEM_DISPOSES
    else:
        codes = USER_DISPOSES

    actions = []
    for code in sorted(codes):
        g = (
            'engineer' if code in ENGINEER_DISPOSES
            else 'production' if code in PRODUCTION_DISPOSES
            else 'system'
        )
        actions.append({
            'dispose': code,
            'label': DISPOSE_LABELS.get(code, str(code)),
            'group': g,
        })
    return True, '获取成功', actions


def dispose_engineer_record(
    hold_record_id,
    dispose,
    actor_user_id,
    actor_role,
    dispose_detail=None,
    downgrades=None,
    retest_grades=None,
    retest_code=None,
):
    """工程师处置：仅允许 ENGINEER_DISPOSES。"""
    try:
        dispose = int(dispose)
    except (TypeError, ValueError):
        return False, 'dispose 无效', None
    if dispose not in ENGINEER_DISPOSES:
        return False, '非工程师处置行为', None
    return dispose_record(
        hold_record_id=hold_record_id,
        dispose=dispose,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        dispose_detail=dispose_detail,
        downgrades=downgrades,
        retest_grades=retest_grades,
        retest_code=retest_code,
    )


def dispose_production_record(hold_record_id, dispose, actor_user_id, actor_role, dispose_detail=None):
    """
    生产处置：仅允许 PRODUCTION_DISPOSES。
    供外部生产系统联动调用（本后台不实现生产处置 UI）。
    """
    try:
        dispose = int(dispose)
    except (TypeError, ValueError):
        return False, 'dispose 无效', None
    if dispose not in PRODUCTION_DISPOSES:
        return False, '非生产处置行为', None
    return dispose_record(
        hold_record_id=hold_record_id,
        dispose=dispose,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        dispose_detail=dispose_detail,
    )


def _enrich_circulation_rows(rows):
    data = []
    for r in rows:
        item = _row_to_dict(r)
        dispose = item.get('DISPOSE')
        item['DISPOSE_LABEL'] = DISPOSE_LABELS.get(dispose, str(dispose))
        data.append(item)
    return data


def get_circulations(hold_record_id):
    """
    查询某 hold_record 的全部流转记录（时间正序）。
    不分权限：任意登录角色均可查，不校验型号归属。
    """
    try:
        rid = int(hold_record_id)
    except (TypeError, ValueError):
        return False, 'hold_record_id 无效', []

    try:
        record = _load_record(rid)
        if not record:
            return False, 'hold_record 不存在', []

        rows = db.session.execute(
            text("""
                SELECT
                    c.ID, c.HOLD_RECORD_ID, c.DISPOSED_OWNER_ID, c.DISPOSE,
                    c.NEXT_OWNER_ID, c.DISPOSE_SOURCE, c.DISPOSE_DTTM,
                    c.DISPOSE_TYPE, c.DISPOSE_DETAIL,
                    u1.NAME AS DISPOSED_OWNER_NAME,
                    u2.NAME AS NEXT_OWNER_NAME
                FROM CIRCULATION_HISTORY c
                LEFT JOIN USERS u1 ON u1.ID = c.DISPOSED_OWNER_ID
                LEFT JOIN USERS u2 ON u2.ID = c.NEXT_OWNER_ID
                WHERE c.HOLD_RECORD_ID = :rid
                ORDER BY c.ID ASC
            """),
            {'rid': rid},
        ).fetchall()

        return True, '获取成功', {
            'record': record,
            'circulations': _enrich_circulation_rows(rows),
        }
    except SQLAlchemyError as e:
        db.session.rollback()
        return False, f'数据库查询异常: {e}', None
    except Exception as e:
        db.session.rollback()
        return False, f'查询失败: {e}', None


def query_circulations(
    hold_record_id=None,
    product_id='',
    wafer_id='',
    lot_id='',
    dispose=None,
    keyword='',
    page=1,
    page_size=20,
    limit=None,
):
    """
    流转记录查询（全量可读，不按角色/型号归属过滤，分页）。
    可按 hold_record_id / product_id / wafer_id / lot_id / dispose / keyword 筛选。
    成功返回 (True, msg, page_payload)。
    """
    try:
        from app.controllers.hold_report_ctrl import _parse_page, _page_payload

        record_table = _record_table()
        if limit is not None and (page is None or str(page) in ('', '1')):
            page, page_size, offset = _parse_page(1, limit)
        else:
            page, page_size, offset = _parse_page(page, page_size)

        where_sql = " WHERE 1 = 1"
        params = {'offset': offset, 'page_size': page_size}

        if hold_record_id is not None and str(hold_record_id).strip() != '':
            try:
                params['hold_record_id'] = int(hold_record_id)
            except (TypeError, ValueError):
                return False, 'hold_record_id 无效', _page_payload([], 0, page, page_size)
            where_sql += " AND c.HOLD_RECORD_ID = :hold_record_id"

        if product_id:
            where_sql += " AND UPPER(r.PRODUCT_ID) LIKE UPPER(:product_id)"
            params['product_id'] = f"%{str(product_id).strip()}%"

        if wafer_id:
            where_sql += " AND UPPER(r.WAFER_ID) LIKE UPPER(:wafer_id)"
            params['wafer_id'] = f"%{str(wafer_id).strip()}%"

        if lot_id:
            where_sql += " AND UPPER(r.LOT_ID) LIKE UPPER(:lot_id)"
            params['lot_id'] = f"%{str(lot_id).strip()}%"

        if dispose is not None and str(dispose).strip() != '':
            try:
                params['dispose'] = int(dispose)
            except (TypeError, ValueError):
                return False, 'dispose 无效', _page_payload([], 0, page, page_size)
            where_sql += " AND c.DISPOSE = :dispose"

        if keyword:
            where_sql += """
                AND (
                    UPPER(r.WAFER_ID) LIKE UPPER(:keyword)
                    OR UPPER(r.LOT_ID) LIKE UPPER(:keyword)
                    OR UPPER(r.PRODUCT_ID) LIKE UPPER(:keyword)
                    OR UPPER(r.HOLD_CODE) LIKE UPPER(:keyword)
                    OR UPPER(NVL(c.DISPOSE_DETAIL, '')) LIKE UPPER(:keyword)
                )
            """
            params['keyword'] = f"%{str(keyword).strip()}%"

        from_sql = f"""
            FROM CIRCULATION_HISTORY c
            INNER JOIN {record_table} r
                ON r.ID = c.HOLD_RECORD_ID
            LEFT JOIN USERS u1 ON u1.ID = c.DISPOSED_OWNER_ID
            LEFT JOIN USERS u2 ON u2.ID = c.NEXT_OWNER_ID
        """

        count_sql = f"""
            SELECT COUNT(*) AS CNT
            {from_sql}
            {where_sql}
        """
        total = int(db.session.execute(text(count_sql), params).scalar() or 0)

        # 指定单条 record 时按流转正序；否则按时间倒序便于浏览
        if 'hold_record_id' in params:
            order_sql = " ORDER BY c.ID ASC"
        else:
            order_sql = " ORDER BY c.DISPOSE_DTTM DESC NULLS LAST, c.ID DESC"

        data_sql = f"""
            SELECT
                c.ID, c.HOLD_RECORD_ID, c.DISPOSED_OWNER_ID, c.DISPOSE,
                c.NEXT_OWNER_ID, c.DISPOSE_SOURCE, c.DISPOSE_DTTM,
                c.DISPOSE_TYPE, c.DISPOSE_DETAIL,
                u1.NAME AS DISPOSED_OWNER_NAME,
                u2.NAME AS NEXT_OWNER_NAME,
                r.PRODUCT_ID, r.STATION, r.EQUIP_ID, r.LOT_ID, r.WAFER_ID,
                r.HOLD_CODE, r.HOLD_REASON, r.SOURCE, r.STATUS,
                r.HOLD_DTTM
            {from_sql}
            {where_sql}
            {order_sql}
            OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY
        """

        rows = db.session.execute(text(data_sql), params).fetchall()
        return True, '获取成功', _page_payload(
            _enrich_circulation_rows(rows), total, page, page_size
        )
    except ValueError as e:
        from app.controllers.hold_report_ctrl import _page_payload
        return False, str(e), _page_payload([], 0, 1, 20)
    except SQLAlchemyError as e:
        from app.controllers.hold_report_ctrl import _page_payload
        db.session.rollback()
        return False, f'数据库查询异常: {e}', _page_payload([], 0, 1, 20)
    except Exception as e:
        from app.controllers.hold_report_ctrl import _page_payload
        db.session.rollback()
        return False, f'查询失败: {e}', _page_payload([], 0, 1, 20)


def get_pending_records(
    owner_id=None,
    product_id='',
    keyword='',
    page=1,
    page_size=20,
    limit=None,
):
    """
    待办：最新流转 NEXT_OWNER_ID = owner_id，且 STATUS != 关闭（分页）。
    root 传 owner_id=None 时查全部未关闭。
    成功返回 (True, msg, page_payload)。
    """
    try:
        from app.controllers.hold_report_ctrl import _parse_page, _page_payload

        record_table = _record_table()
        if limit is not None and (page is None or str(page) in ('', '1')):
            page, page_size, offset = _parse_page(1, limit)
        else:
            page, page_size, offset = _parse_page(page, page_size)

        where_sql = " WHERE NVL(r.STATUS, 0) <> :closed"
        params = {
            'closed': DISPOSE_CLOSE,
            'offset': offset,
            'page_size': page_size,
        }

        if owner_id is not None:
            where_sql += " AND c.NEXT_OWNER_ID = :owner_id"
            params['owner_id'] = int(owner_id)

        if product_id:
            where_sql += " AND UPPER(r.PRODUCT_ID) LIKE UPPER(:product_id)"
            params['product_id'] = f"%{str(product_id).strip()}%"

        if keyword:
            where_sql += """
                AND (
                    UPPER(r.WAFER_ID) LIKE UPPER(:keyword)
                    OR UPPER(r.LOT_ID) LIKE UPPER(:keyword)
                    OR UPPER(r.HOLD_CODE) LIKE UPPER(:keyword)
                    OR UPPER(r.HOLD_REASON) LIKE UPPER(:keyword)
                )
            """
            params['keyword'] = f"%{str(keyword).strip()}%"

        from_sql = f"""
            FROM {record_table} r
            INNER JOIN CIRCULATION_HISTORY c
                ON c.ID = r.LAST_CIRCULATION_ID
            LEFT JOIN USERS u ON u.ID = c.NEXT_OWNER_ID
        """

        count_sql = f"""
            SELECT COUNT(*) AS CNT
            {from_sql}
            {where_sql}
        """
        total = int(db.session.execute(text(count_sql), params).scalar() or 0)

        data_sql = f"""
            SELECT
                r.ID, r.PRODUCT_ID, r.STATION, r.EQUIP_ID, r.LOT_ID, r.WAFER_ID,
                r.HOLD_CODE, r.HOLD_REASON, r.SOURCE, r.SECOND_CODE, r.ROUTE_ID,
                r.RECORD_TYPE, r.STATUS, r.LAST_CIRCULATION_ID, r.HOLD_DTTM,
                c.DISPOSE AS LAST_DISPOSE,
                c.NEXT_OWNER_ID,
                c.DISPOSED_OWNER_ID,
                c.DISPOSE_DTTM AS LAST_DISPOSE_DTTM,
                u.NAME AS NEXT_OWNER_NAME
            {from_sql}
            {where_sql}
            ORDER BY c.DISPOSE_DTTM DESC NULLS LAST, r.ID DESC
            OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY
        """

        rows = db.session.execute(text(data_sql), params).fetchall()
        data = []
        for r in rows:
            item = _row_to_dict(r)
            last_dispose = item.get('LAST_DISPOSE')
            item['LAST_DISPOSE_LABEL'] = DISPOSE_LABELS.get(last_dispose, str(last_dispose))
            data.append(item)
        return True, '获取成功', _page_payload(data, total, page, page_size)
    except ValueError as e:
        from app.controllers.hold_report_ctrl import _page_payload
        return False, str(e), _page_payload([], 0, 1, 20)
    except SQLAlchemyError as e:
        from app.controllers.hold_report_ctrl import _page_payload
        db.session.rollback()
        return False, f'数据库查询异常: {e}', _page_payload([], 0, 1, 20)
    except Exception as e:
        from app.controllers.hold_report_ctrl import _page_payload
        db.session.rollback()
        return False, f'查询失败: {e}', _page_payload([], 0, 1, 20)


def dispose_record(
    hold_record_id,
    dispose,
    actor_user_id,
    actor_role,
    dispose_detail=None,
    downgrades=None,
    retest_grades=None,
    retest_code=None,
):
    """
    对 hold_record 执行一次处置流转。
    成功返回 (True, msg, {circulation_id, next_owner_id, ...})
    工程师降级/重测优先用结构化参数生成 DISPOSE_DETAIL。
    """
    try:
        rid = int(hold_record_id)
        dispose = int(dispose)
        actor_user_id = int(actor_user_id)
    except (TypeError, ValueError):
        return False, '参数无效', None

    try:
        record = _load_record(rid)
        if not record:
            return False, 'hold_record 不存在', None

        if int(record.get('STATUS') or 0) == DISPOSE_CLOSE:
            return False, '记录已关闭，无法继续处置', None

        has_structured = (
            downgrades is not None
            or retest_grades is not None
            or retest_code is not None
        )
        if dispose in (DISPOSE_DOWNGRADE, DISPOSE_RETEST) and not has_structured and dispose_detail:
            # 兼容旧客户端：直接传入已拼好的详情
            detail = str(dispose_detail).strip() or None
            if detail and len(detail) > DISPOSE_DETAIL_MAX_LEN:
                return False, f'dispose_detail 最长 {DISPOSE_DETAIL_MAX_LEN} 字符', None
        elif dispose in (
            DISPOSE_DOWNGRADE, DISPOSE_RETEST, DISPOSE_RELEASE, DISPOSE_ANALYZE,
        ) or has_structured:
            ok_detail, built = build_dispose_detail(
                dispose=dispose,
                dispose_detail=dispose_detail,
                downgrades=downgrades,
                retest_grades=retest_grades,
                retest_code=retest_code,
                record_type=record.get('RECORD_TYPE'),
            )
            if not ok_detail:
                return False, built, None
            detail = built
        else:
            detail = None
            if dispose_detail is not None:
                detail = str(dispose_detail).strip() or None
                if detail and len(detail) > DISPOSE_DETAIL_MAX_LEN:
                    return False, f'dispose_detail 最长 {DISPOSE_DETAIL_MAX_LEN} 字符', None

        last_circ = _load_circulation(record.get('LAST_CIRCULATION_ID'))
        current_owner_id = last_circ.get('NEXT_OWNER_ID') if last_circ else None

        ok, err = _actor_may_dispose(dispose, actor_user_id, actor_role, current_owner_id)
        if not ok:
            return False, err, None

        next_owner_id, disposed_owner_id = _resolve_owners(
            dispose, record['PRODUCT_ID'], actor_user_id,
        )

        record_table = _record_table()
        circ_id = _next_positive_seq('SEQ_CIRCULATION')

        db.session.execute(
            text("""
                INSERT INTO CIRCULATION_HISTORY (
                    ID,
                    HOLD_RECORD_ID,
                    DISPOSED_OWNER_ID,
                    DISPOSE,
                    NEXT_OWNER_ID,
                    DISPOSE_SOURCE,
                    DISPOSE_DTTM,
                    DISPOSE_TYPE,
                    DISPOSE_DETAIL
                ) VALUES (
                    :circ_id,
                    :hold_record_id,
                    :disposed_owner_id,
                    :dispose,
                    :next_owner_id,
                    :dispose_source,
                    SYSDATE,
                    :dispose_type,
                    :dispose_detail
                )
            """),
            {
                'circ_id': circ_id,
                'hold_record_id': rid,
                'disposed_owner_id': disposed_owner_id,
                'dispose': dispose,
                'next_owner_id': next_owner_id,
                'dispose_source': 'SYS',
                'dispose_type': dispose,
                'dispose_detail': detail,
            },
        )

        db.session.execute(
            text(f"""
                UPDATE {record_table}
                SET LAST_CIRCULATION_ID = :circ_id,
                    STATUS = :status
                WHERE ID = :rid
            """),
            {'circ_id': circ_id, 'status': dispose, 'rid': rid},
        )

        db.session.commit()
        return True, '处置成功', {
            'hold_record_id': rid,
            'circulation_id': circ_id,
            'dispose': dispose,
            'dispose_label': DISPOSE_LABELS.get(dispose, str(dispose)),
            'disposed_owner_id': disposed_owner_id,
            'next_owner_id': next_owner_id,
            'status': dispose,
        }
    except ValueError as e:
        db.session.rollback()
        return False, str(e), None
    except SQLAlchemyError as e:
        db.session.rollback()
        return False, f'数据库写入异常: {e}', None
    except Exception as e:
        db.session.rollback()
        return False, f'处置失败: {e}', None
