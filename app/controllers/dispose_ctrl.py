"""
Hold Record 处置流转业务逻辑。

规则来源：dispose_api.md
  - DISPOSE 行为码决定 NEXT_OWNER_ID / DISPOSED_OWNER_ID
  - ~ → PRODUCT_INFO.PRO_ENG_ID（缺省 SYSTEM_USER_ID）
  - 181 → PRODUCTION_OP_ID（生产 OP）
  - 1 → SYSTEM_USER_ID（系统）

同一事务内：插入 CIRCULATION_HISTORY + 回写 LAST_CIRCULATION_ID / STATUS。
可靠性分析(5) 下一节点仍是操作工程师；生产「留样完成」(65) 只写流转、不改当前节点。

DISPOSE_DETAIL 结构化规则（工程师降级/重测由服务端生成，不含备注）：
  降级: DG:HA>F;FB>F
  重测(等级): RT:F,HA
  重测(WLT code): RT:CODE=123
  WLT 按片（直白中文，片间 ;）：#02，降级，降main拆批;#03，重测，整片重测;#04，重测，重测A夹具，@1@361
工程师处置仅为意见，不改写 FT_HOLD_RECORD.GRADE_NUM。
DISPOSE_NOTE：工程师处置时选择的工程备注文本
DISPOSE_MANUAL_NOTE：任意处置可选手输备注；可靠性分析之后的放行/降级必须手输
"""
import json
import logging
import re
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.config import Config
from app.utils.auth_decorators import ROLE_ROOT, ROLE_PRODUCTION
from app.utils.annex_util import hold_code_is_aql, parse_annex_ftp_paths
from app.utils.database_util import (
    _ALLOWED_SEQS,
    expand_display_wafer_ids,
    format_wafer_id_display,
    resolve_circulation_table,
    resolve_hold_record_table,
    seq_for_circulation,
)


# ---------- DISPOSE 行为码 ----------
DISPOSE_CREATE = 0
DISPOSE_RELEASE = 1          # 放行
DISPOSE_DOWNGRADE = 2        # 降级
DISPOSE_RETEST = 3           # 重测
DISPOSE_ANALYZE = 5          # 可靠性分析（下一节点仍是工程师）
DISPOSE_ANALYZE_RETURN = 6   # 分析(返回) — 已废弃，仅历史展示
DISPOSE_TRANSFER = 7         # 转交
DISPOSE_ROLLBACK = 8         # 回退
DISPOSE_SAMPLE_DONE = 65     # 留样完成 — 生产侧，不改当前节点
DISPOSE_PROD_ANALYZE_RETURN = 66  # 分析(返回) — 已废弃，仅历史展示
DISPOSE_CLOSE = 99           # 关闭

DISPOSE_DETAIL_MAX_LEN = 4000
DISPOSE_NOTE_MAX_LEN = 1024
DISPOSE_MANUAL_NOTE_MAX_LEN = 1024
RECORD_TYPE_WLT = 2
ANALYZE_INTERVAL_MINUTES = 30
INTERVAL_CONFIRM_MSG = '距上次可靠性分析不足30分钟，确认后请再次提交'

# WLT 降级 / 重测子类型（API 入参）
DG_MODE_MAIN_SPLIT = 'main_split'
DG_MODE_MAIN_NOSPLIT = 'main_nosplit'
RT_MODE_FULL = 'full'
RT_MODE_FIXTURE_A = 'fixture_a'
RT_MODE_FIXTURE_B = 'fixture_b'

_DG_MODE_LABEL = {
    DG_MODE_MAIN_SPLIT: '降main拆批',
    DG_MODE_MAIN_NOSPLIT: '降main不拆批',
}
_RT_MODE_LABEL = {
    RT_MODE_FULL: '整片重测',
    RT_MODE_FIXTURE_A: '重测A夹具',
    RT_MODE_FIXTURE_B: '重测B夹具',
}
_RETEST_CODES_RE = re.compile(r'^(@\d+)+$')

DISPOSE_LABELS = {
    DISPOSE_CREATE: '创建',
    DISPOSE_RELEASE: '放行',
    DISPOSE_DOWNGRADE: '降级',
    DISPOSE_RETEST: '重测',
    DISPOSE_ANALYZE: '可靠性分析',
    DISPOSE_ANALYZE_RETURN: '分析(返回)',
    DISPOSE_TRANSFER: '转交',
    DISPOSE_ROLLBACK: '回退',
    DISPOSE_SAMPLE_DONE: '留样完成',
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

# 工程师流转码（用于「最新一次工程师处置」；忽略生产侧 8/65/6/66）
ENGINEER_FLOW_DISPOSES = {
    DISPOSE_RELEASE,
    DISPOSE_DOWNGRADE,
    DISPOSE_RETEST,
    DISPOSE_ANALYZE,
    DISPOSE_TRANSFER,
}

# 生产可发起。留样完成(65) 不要求当前节点在生产；回退/关闭仍须在生产节点。
# 6/66 分析(返回)已废弃，不再可发起（标签保留供历史展示）。
PRODUCTION_DISPOSES = {
    DISPOSE_SAMPLE_DONE,
    DISPOSE_ROLLBACK,
    DISPOSE_CLOSE,
}

# 历史「分析返回」视为已留样，避免存量单再进待留样
SAMPLE_DONE_EQUIV_DISPOSES = {
    DISPOSE_SAMPLE_DONE,
    DISPOSE_ANALYZE_RETURN,
    DISPOSE_PROD_ANALYZE_RETURN,
}

AFTER_ANALYZE_ALLOWED = {
    DISPOSE_RELEASE,
    DISPOSE_DOWNGRADE,
}

# 系统/root 可发起
SYSTEM_DISPOSES = {
    DISPOSE_CLOSE,
}

USER_DISPOSES = ENGINEER_DISPOSES | PRODUCTION_DISPOSES | SYSTEM_DISPOSES


def _production_op_id():
    return int(getattr(Config, 'PRODUCTION_OP_ID', 181) or 181)


def _system_user_id():
    return int(getattr(Config, 'SYSTEM_USER_ID', 1) or 1)


def _record_table():
    return resolve_hold_record_table()


def _circ_table():
    return resolve_circulation_table(record_table=_record_table())


def _circ_seq():
    return seq_for_circulation(_circ_table())


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
    if seq_name not in _ALLOWED_SEQS:
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


def _grade_sort_key(it):
    g = it['grade']
    has_f = 0 if 'F' in g.upper() else 1
    return (has_f, g.upper(), g)


def _grades_from_mapping(mapping):
    items = []
    for grade, qty in mapping.items():
        g = str(grade).strip()
        if not g:
            continue
        items.append({'grade': g, 'qty': '' if qty is None else str(qty).strip()})
    return items


def _grades_from_seq(seq):
    items = []
    for item in seq:
        if isinstance(item, dict):
            grade = str(item.get('grade') or item.get('GRADE') or '').strip()
            qty = item.get('qty', item.get('QTY', ''))
            if not grade and len(item) == 1:
                key, val = next(iter(item.items()))
                grade = str(key).strip()
                qty = val
            if not grade:
                continue
            items.append({'grade': grade, 'qty': '' if qty is None else str(qty).strip()})
        elif isinstance(item, (list, tuple)) and item:
            grade = str(item[0]).strip()
            qty = item[1] if len(item) > 1 else ''
            if grade:
                items.append({'grade': grade, 'qty': '' if qty is None else str(qty).strip()})
        elif item is not None and str(item).strip():
            items.append({'grade': str(item).strip(), 'qty': ''})
    return items


def parse_grade_num(raw):
    """
    解析 GRADE_NUM（JSON 或 F:1151,HA:49）为 [{grade, qty}, ...]。
    含字母 F（不区分大小写）的等级排在前面，组内按等级名排序。
    """
    if raw is None:
        return []
    if isinstance(raw, dict):
        items = _grades_from_mapping(raw)
        items.sort(key=_grade_sort_key)
        return items
    if isinstance(raw, list):
        items = _grades_from_seq(raw)
        items.sort(key=_grade_sort_key)
        return items

    text_val = str(raw).strip()
    if not text_val:
        return []

    if text_val[:1] in '{[':
        try:
            parsed = json.loads(text_val)
            if isinstance(parsed, str) and parsed.strip()[:1] in '{[':
                parsed = json.loads(parsed)
            if isinstance(parsed, dict):
                items = _grades_from_mapping(parsed)
                items.sort(key=_grade_sort_key)
                return items
            if isinstance(parsed, list):
                items = _grades_from_seq(parsed)
                items.sort(key=_grade_sort_key)
                return items
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

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

    items.sort(key=_grade_sort_key)
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


def _norm_wafer_display(value):
    """统一为 #后缀 展示键。"""
    if value is None:
        return ''
    text_val = str(value).strip()
    if not text_val:
        return ''
    return format_wafer_id_display(text_val) or text_val


def list_wafers_for_record(record):
    """
    从 hold_record 展开 wafer 展示列表，如 ['#01', '#02']。
    WAFER_ID 可能已是 #01#02 展示串或完整片号。
    """
    if not record:
        return []
    wafer_raw = record.get('WAFER_ID')
    lot_raw = record.get('LOT_ID')
    expanded = expand_display_wafer_ids(wafer_raw, lot_raw)
    if not expanded and wafer_raw is not None and str(wafer_raw).strip():
        raw = str(wafer_raw).strip()
        if raw.startswith('#'):
            parts = re.findall(r'#([^#\s]+)', raw)
            if parts:
                return [f'#{p}' for p in parts]
        expanded = [raw]
    displays = []
    seen = set()
    for wid in expanded:
        disp = _norm_wafer_display(wid)
        # 防御：误把合并串当成一片
        if disp.startswith('#') and disp.count('#') > 1:
            for suffix in re.findall(r'#([^#\s]+)', disp):
                token = f'#{suffix}'
                if token not in seen:
                    seen.add(token)
                    displays.append(token)
            continue
        if not disp or disp in seen:
            continue
        seen.add(disp)
        displays.append(disp)
    return displays


def enrich_record_wafers(record):
    """为 record dict 附加 WAFERS 展示列表。"""
    if not record:
        return record
    record['WAFERS'] = list_wafers_for_record(record)
    return record


def summarize_wafer_disposes(dispose_codes):
    """
    多片 dispose 汇总为记录级 STATUS/DISPOSE：
    任一 5 → 5；否则任一 3 → 3；否则任一 2 → 2；否则 1。
    """
    codes = []
    for c in dispose_codes or []:
        try:
            codes.append(int(c))
        except (TypeError, ValueError):
            continue
    if DISPOSE_ANALYZE in codes:
        return DISPOSE_ANALYZE
    if DISPOSE_RETEST in codes:
        return DISPOSE_RETEST
    if DISPOSE_DOWNGRADE in codes:
        return DISPOSE_DOWNGRADE
    return DISPOSE_RELEASE


def normalize_retest_codes(retest_codes):
    """校验 @1@361 形式；成功 (True, normalized_or_empty)，失败 (False, err)。"""
    if retest_codes is None:
        return True, ''
    raw = str(retest_codes).strip()
    if not raw:
        return True, ''
    if not _RETEST_CODES_RE.fullmatch(raw):
        return False, '重测 code 须为 @数字 形式，如 @1@361'
    return True, raw


def _build_downgrade_pairs(downgrades):
    """成功 (True, pairs_list)；失败 (False, err)。"""
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
            continue
        key = src.upper()
        if key in seen_from:
            return False, f'同一源等级只能降一次: {src}'
        seen_from.add(key)
        pairs.append(f'{src}>{dst}')
    if not pairs:
        return False, '降级须至少变更一个等级（不能全部保持源→源）'
    return True, pairs


def _build_retest_grade_list(retest_grades):
    """成功 (True, uniq_grades)；失败 (False, err)。"""
    uniq = []
    seen = set()
    for g in (retest_grades or []):
        token = _norm_grade_token(g)
        if not token:
            continue
        k = token.upper()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(token)
    if not uniq:
        return False, '重测须至少选择一个等级'
    return True, uniq


def _encode_one_wafer_action(action):
    """
    编码单片直白中文片段，如「#02，降级，降main拆批」。
    成功 (True, fragment, dispose_int)；失败 (False, err, None)。
    """
    if not isinstance(action, dict):
        return False, 'wafer_actions 项格式无效', None

    wafer = _norm_wafer_display(action.get('wafer') or action.get('wafer_id'))
    if not wafer:
        return False, 'wafer 不能为空', None

    try:
        dispose = int(action.get('dispose'))
    except (TypeError, ValueError):
        return False, f'{wafer} dispose 无效', None

    if dispose not in ENGINEER_DISPOSES:
        return False, f'{wafer} 不支持的处置行为: {dispose}', None

    action_label = DISPOSE_LABELS.get(dispose, str(dispose))
    parts = [wafer, action_label]

    if dispose in (DISPOSE_RELEASE, DISPOSE_ANALYZE):
        return True, '，'.join(parts), dispose

    if dispose == DISPOSE_DOWNGRADE:
        mode = str(action.get('downgrade_mode') or '').strip().lower()
        if mode not in _DG_MODE_LABEL:
            return False, f'{wafer} 降级须指定 downgrade_mode（main_split/main_nosplit）', None
        parts.append(_DG_MODE_LABEL[mode])
        return True, '，'.join(parts), dispose

    if dispose == DISPOSE_RETEST:
        mode = str(action.get('retest_mode') or '').strip().lower()
        if mode not in _RT_MODE_LABEL:
            return False, (
                f'{wafer} 重测须指定 retest_mode'
                f'（full/fixture_a/fixture_b）'
            ), None
        ok_codes, codes_or_err = normalize_retest_codes(action.get('retest_codes'))
        if not ok_codes:
            return False, f'{wafer} {codes_or_err}', None
        parts.append(_RT_MODE_LABEL[mode])
        if mode == RT_MODE_FULL:
            if codes_or_err:
                return False, f'{wafer} 整片重测不支持填写 code', None
            return True, '，'.join(parts), dispose
        # 夹具重测须填 code
        if not codes_or_err:
            return False, f'{wafer} 夹具重测须填写 code（如 @1@361）', None
        parts.append(codes_or_err)
        return True, '，'.join(parts), dispose

    return False, f'{wafer} 不支持的处置行为: {dispose}', None


def build_wlt_wafer_dispose_detail(wafer_actions, expected_wafers):
    """
    校验并生成 WLT 按片 DISPOSE_DETAIL（直白中文，片间 ; 分隔）。
    成功 (True, detail, summarized_dispose)；失败 (False, err, None)。
    """
    if not isinstance(wafer_actions, list) or not wafer_actions:
        return False, 'WLT 处置须提供 wafer_actions', None

    expected = [_norm_wafer_display(w) for w in (expected_wafers or [])]
    expected = [w for w in expected if w]
    if not expected:
        return False, '记录无有效 wafer，无法按片处置', None

    expected_set = set(expected)
    fragments = []
    dispose_codes = []
    seen = set()

    for action in wafer_actions:
        ok, frag_or_err, disp = _encode_one_wafer_action(action)
        if not ok:
            return False, frag_or_err, None
        wafer = _norm_wafer_display(action.get('wafer') or action.get('wafer_id'))
        if wafer in seen:
            return False, f'wafer 重复: {wafer}', None
        if wafer not in expected_set:
            return False, f'未知 wafer: {wafer}', None
        seen.add(wafer)
        fragments.append(frag_or_err)
        dispose_codes.append(disp)

    missing = [w for w in expected if w not in seen]
    if missing:
        return False, f'须一次性处置全部 wafer，缺少: {",".join(missing)}', None

    # 按 expected 顺序重排，便于阅读
    by_wafer = {}
    for frag, action in zip(fragments, wafer_actions):
        w = _norm_wafer_display(action.get('wafer') or action.get('wafer_id'))
        by_wafer[w] = frag
    ordered = [by_wafer[w] for w in expected]
    detail = ';'.join(ordered)
    if len(detail) > DISPOSE_DETAIL_MAX_LEN:
        return False, f'dispose_detail 最长 {DISPOSE_DETAIL_MAX_LEN} 字符', None
    return True, detail, summarize_wafer_disposes(dispose_codes)


def build_dispose_detail(
    dispose,
    dispose_detail=None,
    downgrades=None,
    retest_grades=None,
    retest_code=None,
    record_type=None,
    wafer_actions=None,
    expected_wafers=None,
):
    """
    按处置行为生成 DISPOSE_DETAIL（仅规则化文本，不含工程备注/手输备注）。
    降级: DG:HA>F;FB>F
    重测: RT:F,HA 或 RT:CODE=123
    WLT 按片: #01，放行;#02，降级，降main拆批;#03，重测，整片重测;#04，重测，重测A夹具，@1@361
    其它行为：DISPOSE_DETAIL 为空
    成功返回 (True, detail_or_None[, summarized_dispose])；
    无 wafer_actions 时仍为 (True, detail)；失败返回 (False, err_msg)。
    若传入 wafer_actions（WLT），返回 (True, detail, summarized_dispose)。
    """
    try:
        rt = int(record_type) if record_type is not None and str(record_type).strip() != '' else None
    except (TypeError, ValueError):
        return False, 'record_type 无效'

    if wafer_actions is not None:
        if rt is not None and rt != RECORD_TYPE_WLT:
            return False, '仅 WLT 处置单支持按片 wafer_actions'
        ok, detail_or_err, summarized = build_wlt_wafer_dispose_detail(
            wafer_actions, expected_wafers,
        )
        if not ok:
            return False, detail_or_err
        return True, detail_or_err, summarized

    try:
        dispose = int(dispose)
    except (TypeError, ValueError):
        return False, 'dispose 无效'

    if dispose == DISPOSE_DOWNGRADE:
        ok, pairs_or_err = _build_downgrade_pairs(downgrades)
        if not ok:
            return False, pairs_or_err
        detail = 'DG:' + ';'.join(pairs_or_err)
    elif dispose == DISPOSE_RETEST:
        code_raw = None if retest_code is None else str(retest_code).strip()
        grades = []
        for g in (retest_grades or []):
            token = _norm_grade_token(g)
            if token:
                grades.append(token)

        if code_raw and grades:
            return False, 'WLT 重测等级与 code 互斥，只能选一种'
        if code_raw:
            if rt is not None and rt != RECORD_TYPE_WLT:
                return False, '仅 WLT 处置单支持按 code 重测'
            # 兼容旧单数字；也接受 @1@361
            if re.fullmatch(r'\d+', code_raw):
                detail = f'RT:CODE={code_raw}'
            elif _RETEST_CODES_RE.fullmatch(code_raw):
                detail = f'RT:CODE={code_raw}'
            else:
                return False, '重测 code 须为数字或 @数字 形式'
        elif grades:
            ok, uniq_or_err = _build_retest_grade_list(grades)
            if not ok:
                return False, uniq_or_err
            detail = 'RT:' + ','.join(uniq_or_err)
        else:
            return False, '重测须选择等级或填写 code'
    else:
        # 放行/分析/生产等：规则化详情为空；手输备注走 DISPOSE_MANUAL_NOTE
        detail = None
        # 兼容：若直接传入已拼好的 DG:/RT:/W: 或按片直白串
        if dispose_detail is not None:
            raw = str(dispose_detail).strip()
            if _is_structured_dispose_detail(raw):
                detail = raw

    if detail is not None and len(detail) > DISPOSE_DETAIL_MAX_LEN:
        return False, f'dispose_detail 最长 {DISPOSE_DETAIL_MAX_LEN} 字符'
    return True, detail


def normalize_dispose_note(dispose_note):
    """校验并规范化工程备注；成功 (True, note_or_None)，失败 (False, err)。"""
    if dispose_note is None:
        return True, None
    note = str(dispose_note).strip() or None
    if note is not None and len(note) > DISPOSE_NOTE_MAX_LEN:
        return False, f'dispose_note 最长 {DISPOSE_NOTE_MAX_LEN} 字符'
    return True, note


def normalize_dispose_manual_note(dispose_manual_note):
    """校验并规范选手输备注；成功 (True, note_or_None)，失败 (False, err)。"""
    if dispose_manual_note is None:
        return True, None
    note = str(dispose_manual_note).strip() or None
    if note is not None and len(note) > DISPOSE_MANUAL_NOTE_MAX_LEN:
        return False, f'dispose_manual_note 最长 {DISPOSE_MANUAL_NOTE_MAX_LEN} 字符'
    return True, note


def _truthy_flag(value) -> bool:
    if value is True:
        return True
    if isinstance(value, (int, float)) and int(value) == 1:
        return True
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'y')


def is_interval_confirm_result(success, result) -> bool:
    return (not success) and isinstance(result, dict) and bool(result.get('need_interval_confirm'))


def _id_chunks(ids, size=400):
    uniq = []
    seen = set()
    for raw in ids or []:
        try:
            val = int(raw)
        except (TypeError, ValueError):
            continue
        if val in seen:
            continue
        seen.add(val)
        uniq.append(val)
    for i in range(0, len(uniq), size):
        yield uniq[i:i + size]


def _in_clause(ids, prefix):
    placeholders = []
    params = {}
    for i, val in enumerate(ids):
        key = f'{prefix}{i}'
        placeholders.append(f':{key}')
        params[key] = int(val)
    return ', '.join(placeholders), params


def _query_latest_engineer_circs(record_ids):
    """最新一次工程师处置（1/2/3/5/7）。返回 {record_id: dict}。"""
    result = {}
    flow_codes = tuple(sorted(ENGINEER_FLOW_DISPOSES))
    for chunk in _id_chunks(record_ids):
        in_sql, params = _in_clause(chunk, 'rid')
        params['analyze'] = DISPOSE_ANALYZE
        params['minutes'] = ANALYZE_INTERVAL_MINUTES
        for i, code in enumerate(flow_codes):
            params[f'fc{i}'] = code
        flow_in = ', '.join(f':fc{i}' for i in range(len(flow_codes)))
        rows = db.session.execute(
            text(f"""
                SELECT HOLD_RECORD_ID, DISPOSE, DISPOSE_DTTM,
                       CASE
                         WHEN DISPOSE = :analyze
                          AND DISPOSE_DTTM >= SYSDATE - (:minutes / 1440)
                         THEN 1 ELSE 0
                       END AS INTERVAL_WARN
                FROM (
                    SELECT
                        HOLD_RECORD_ID, DISPOSE, DISPOSE_DTTM,
                        ROW_NUMBER() OVER (
                            PARTITION BY HOLD_RECORD_ID ORDER BY ID DESC
                        ) AS RN
                    FROM {_circ_table()}
                    WHERE HOLD_RECORD_ID IN ({in_sql})
                      AND DISPOSE IN ({flow_in})
                )
                WHERE RN = 1
            """),
            params,
        ).fetchall()
        for row in rows:
            item = _row_to_dict(row)
            try:
                rid = int(item.get('HOLD_RECORD_ID'))
            except (TypeError, ValueError):
                continue
            result[rid] = item
    return result


def _query_pending_sample_ids(record_ids):
    """有可靠性分析且尚未留样完成（含旧 6/66 视为已留样）的 record id 集合。"""
    pending = set()
    equiv = tuple(sorted(SAMPLE_DONE_EQUIV_DISPOSES))
    for chunk in _id_chunks(record_ids):
        in_sql, params = _in_clause(chunk, 'rid')
        params['analyze'] = DISPOSE_ANALYZE
        params['closed'] = DISPOSE_CLOSE
        for i, code in enumerate(equiv):
            params[f'eq{i}'] = code
        equiv_in = ', '.join(f':eq{i}' for i in range(len(equiv)))
        rows = db.session.execute(
            text(f"""
                SELECT r.ID
                FROM {_record_table()} r
                WHERE r.ID IN ({in_sql})
                  AND NVL(r.STATUS, 0) != :closed
                  AND EXISTS (
                      SELECT 1 FROM {_circ_table()} h
                      WHERE h.HOLD_RECORD_ID = r.ID AND h.DISPOSE = :analyze
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM {_circ_table()} h
                      WHERE h.HOLD_RECORD_ID = r.ID
                        AND h.DISPOSE IN ({equiv_in})
                  )
            """),
            params,
        ).fetchall()
        for row in rows:
            try:
                pending.add(int(row[0]))
            except (TypeError, ValueError):
                continue
    return pending


def pending_sample_where_sql(record_alias='r'):
    """待留样 SQL 片段（需绑定 :analyze :closed :sample_done :analyze_return :prod_analyze_return）。"""
    return f"""
        NVL({record_alias}.STATUS, 0) != :closed
        AND EXISTS (
            SELECT 1 FROM {_circ_table()} h
            WHERE h.HOLD_RECORD_ID = {record_alias}.ID AND h.DISPOSE = :analyze
        )
        AND NOT EXISTS (
            SELECT 1 FROM {_circ_table()} h
            WHERE h.HOLD_RECORD_ID = {record_alias}.ID
              AND h.DISPOSE IN (:sample_done, :analyze_return, :prod_analyze_return)
        )
    """


def pending_sample_bind_params():
    return {
        'analyze': DISPOSE_ANALYZE,
        'closed': DISPOSE_CLOSE,
        'sample_done': DISPOSE_SAMPLE_DONE,
        'analyze_return': DISPOSE_ANALYZE_RETURN,
        'prod_analyze_return': DISPOSE_PROD_ANALYZE_RETURN,
    }


def attach_reliability_followup_many(records):
    """为 record dict 列表附加分析后限制 / 待留样标志。"""
    if not records:
        return records
    ids = [item.get('ID') for item in records]
    latest_map = _query_latest_engineer_circs(ids)
    pending_set = _query_pending_sample_ids(ids)
    default_allowed = sorted(ENGINEER_DISPOSES)
    after_allowed = sorted(AFTER_ANALYZE_ALLOWED)
    for item in records:
        try:
            rid = int(item.get('ID'))
        except (TypeError, ValueError):
            continue
        circ = latest_map.get(rid) or {}
        try:
            last_eng = int(circ.get('DISPOSE')) if circ.get('DISPOSE') is not None else None
        except (TypeError, ValueError):
            last_eng = None
        after = last_eng == DISPOSE_ANALYZE
        try:
            interval_warn = int(circ.get('INTERVAL_WARN') or 0) == 1
        except (TypeError, ValueError):
            interval_warn = False
        item['AFTER_RELIABILITY_ANALYZE'] = after
        item['ALLOWED_DISPOSES'] = after_allowed if after else default_allowed
        item['REQUIRE_MANUAL_NOTE'] = after
        item['ANALYZE_INTERVAL_WARN'] = bool(after and interval_warn)
        item['LAST_ANALYZE_DTTM'] = circ.get('DISPOSE_DTTM') if after else None
        item['PENDING_SAMPLE_RETAIN'] = rid in pending_set
    return records


def attach_reliability_followup(record):
    if not record:
        return record
    attach_reliability_followup_many([record])
    return record


def _is_structured_dispose_detail(raw):
    """是否为规则化处置详情（非自由备注）。"""
    if raw is None:
        return False
    text_val = str(raw).strip()
    if not text_val:
        return False
    upper = text_val.upper()
    return upper.startswith(('DG:', 'RT:', 'W:')) or text_val.startswith('#')


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
                r.GRADE_NUM, r.RECORD_TYPE, r.STATUS, r.LAST_CIRCULATION_ID, r.HOLD_DTTM,
                r.ANNEX_FTP_PATH
            FROM {record_table} r
            WHERE r.ID = :rid
        """),
        {'rid': record_id},
    ).fetchone()
    if not row:
        return None
    rec = enrich_record_wafers(enrich_record_grades(_row_to_dict(row)))
    rec['IS_AQL_HOLD'] = hold_code_is_aql(rec.get('HOLD_CODE'))
    rec['ANNEX_COUNT'] = len(parse_annex_ftp_paths(rec.get('ANNEX_FTP_PATH')))
    return rec


def _load_circulation(circ_id):
    if not circ_id:
        return None
    row = db.session.execute(
        text(f"""
            SELECT
                ID, HOLD_RECORD_ID, DISPOSED_OWNER_ID, DISPOSE,
                NEXT_OWNER_ID, DISPOSE_SOURCE, DISPOSE_DTTM,
                DISPOSE_TYPE, DISPOSE_DETAIL, DISPOSE_NOTE, DISPOSE_MANUAL_NOTE
            FROM {_circ_table()}
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

    if dispose == DISPOSE_ANALYZE:
        eng = engineer_disposed()
        return eng, eng

    if dispose in (
        DISPOSE_RELEASE, DISPOSE_DOWNGRADE, DISPOSE_RETEST,
    ):
        return prod_op, engineer_disposed()

    if dispose == DISPOSE_TRANSFER:
        return pro_eng_id, engineer_disposed()

    if dispose in (DISPOSE_ANALYZE_RETURN, DISPOSE_PROD_ANALYZE_RETURN, DISPOSE_ROLLBACK):
        return pro_eng_id, prod_op

    if dispose == DISPOSE_CLOSE:
        # 生产关闭：DISPOSED_OWNER_ID=生产 OP；系统/root 关闭：系统用户
        if int(actor_user_id) == prod_op:
            return None, prod_op
        return None, system_id

    raise ValueError(f'不支持的处置行为: {dispose}')


def _last_dispose_was_analyze(last_circ) -> bool:
    """最近一次流转是否为工程师「可靠性分析」(5)。"""
    if not last_circ:
        return False
    try:
        return int(last_circ.get('DISPOSE')) == DISPOSE_ANALYZE
    except (TypeError, ValueError):
        return False


def _actor_may_dispose(dispose: int, actor_user_id: int, actor_role, current_owner_id, last_circ=None):
    """校验操作人是否有权执行该处置。"""
    is_root = actor_role == ROLE_ROOT
    is_prod_role = actor_role == ROLE_PRODUCTION
    prod_op = _production_op_id()

    if dispose not in USER_DISPOSES:
        return False, f'不支持的处置行为: {dispose}'

    # 关闭：root 任意节点可关；生产仅当前节点在生产时可关
    if dispose == DISPOSE_CLOSE and not is_root and not (
        dispose in PRODUCTION_DISPOSES and (is_prod_role or int(actor_user_id) == prod_op)
    ):
        return False, '关闭仅系统/管理员或生产可执行'

    if not is_root:
        if dispose == DISPOSE_SAMPLE_DONE:
            is_prod_actor = is_prod_role or int(actor_user_id) == prod_op
            if not is_prod_actor:
                return False, '仅生产可执行该处置'
            return True, ''

        if current_owner_id is None:
            return False, '记录无当前负责人，无法处置'

        if dispose in PRODUCTION_DISPOSES:
            is_prod_actor = is_prod_role or int(actor_user_id) == prod_op
            if not is_prod_actor:
                return False, '仅生产可执行该处置'
            if int(current_owner_id) != prod_op:
                return False, '仅当前节点在生产时可处置'
            return True, ''

        if int(actor_user_id) != int(current_owner_id):
            return False, '仅当前负责人可处置该记录'

        if dispose in ENGINEER_DISPOSES and int(actor_user_id) == prod_op:
            return False, '生产账号不可执行工程师处置'

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
    dispose_note=None,
    dispose_manual_note=None,
    downgrades=None,
    retest_grades=None,
    retest_code=None,
    wafer_actions=None,
    confirm_interval=False,
):
    """工程师处置：仅允许 ENGINEER_DISPOSES。dispose_note 为工程备注。"""
    # WLT 按片：dispose 可由 wafer_actions 汇总，此处先占位，dispose_record 内再校验
    if dispose is not None and str(dispose).strip() != '':
        try:
            dispose = int(dispose)
        except (TypeError, ValueError):
            return False, 'dispose 无效', None
        if dispose not in ENGINEER_DISPOSES:
            return False, '非工程师处置行为', None
    elif wafer_actions is None:
        return False, 'dispose 无效', None

    return dispose_record(
        hold_record_id=hold_record_id,
        dispose=dispose,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        dispose_detail=dispose_detail,
        dispose_note=dispose_note,
        dispose_manual_note=dispose_manual_note,
        downgrades=downgrades,
        retest_grades=retest_grades,
        retest_code=retest_code,
        wafer_actions=wafer_actions,
        confirm_interval=confirm_interval,
    )


def dispose_sample_done(
    hold_record_id,
    actor_user_id,
    actor_role,
    dispose_manual_note=None,
):
    """
    生产「留样完成」(65)：写入流转审计，不回写 LAST_CIRCULATION_ID / STATUS / 当前负责人。
    """
    try:
        rid = int(hold_record_id)
        actor_user_id = int(actor_user_id)
    except (TypeError, ValueError):
        return False, '参数无效', None

    try:
        record = _load_record(rid)
        if not record:
            return False, 'hold_record 不存在', None
        if int(record.get('STATUS') or 0) == DISPOSE_CLOSE:
            return False, '记录已关闭，无法继续处置', None

        pending = _query_pending_sample_ids([rid])
        if rid not in pending:
            return False, '当前无需留样完成', None

        last_circ = _load_circulation(record.get('LAST_CIRCULATION_ID'))
        current_owner_id = last_circ.get('NEXT_OWNER_ID') if last_circ else None
        ok, err = _actor_may_dispose(
            DISPOSE_SAMPLE_DONE, actor_user_id, actor_role, current_owner_id,
            last_circ=last_circ,
        )
        if not ok:
            return False, err, None

        ok_manual, manual_or_err = normalize_dispose_manual_note(dispose_manual_note)
        if not ok_manual:
            return False, manual_or_err, None
        manual_note = manual_or_err

        prod_op = _production_op_id()
        circ_id = _next_positive_seq(_circ_seq())
        db.session.execute(
            text(f"""
                INSERT INTO {_circ_table()} (
                    ID,
                    HOLD_RECORD_ID,
                    DISPOSED_OWNER_ID,
                    DISPOSE,
                    NEXT_OWNER_ID,
                    DISPOSE_SOURCE,
                    DISPOSE_DTTM,
                    DISPOSE_TYPE,
                    DISPOSE_DETAIL,
                    DISPOSE_NOTE,
                    DISPOSE_MANUAL_NOTE
                ) VALUES (
                    :circ_id,
                    :hold_record_id,
                    :disposed_owner_id,
                    :dispose,
                    :next_owner_id,
                    :dispose_source,
                    SYSDATE,
                    :dispose_type,
                    :dispose_detail,
                    :dispose_note,
                    :dispose_manual_note
                )
            """),
            {
                'circ_id': circ_id,
                'hold_record_id': rid,
                'disposed_owner_id': prod_op,
                'dispose': DISPOSE_SAMPLE_DONE,
                'next_owner_id': current_owner_id,
                'dispose_source': 'SYS',
                'dispose_type': DISPOSE_SAMPLE_DONE,
                'dispose_detail': None,
                'dispose_note': None,
                'dispose_manual_note': manual_note,
            },
        )
        db.session.commit()
        return True, '留样完成', {
            'hold_record_id': rid,
            'circulation_id': circ_id,
            'dispose': DISPOSE_SAMPLE_DONE,
            'dispose_label': DISPOSE_LABELS.get(DISPOSE_SAMPLE_DONE),
            'disposed_owner_id': prod_op,
            'next_owner_id': current_owner_id,
            'dispose_detail': None,
            'dispose_note': None,
            'dispose_manual_note': manual_note,
            'status': record.get('STATUS'),
        }
    except SQLAlchemyError as e:
        db.session.rollback()
        return False, f'数据库写入异常: {e}', None
    except Exception as e:
        db.session.rollback()
        return False, f'处置失败: {e}', None


def dispose_production_record(
    hold_record_id,
    dispose,
    actor_user_id,
    actor_role,
    dispose_detail=None,
    dispose_manual_note=None,
):
    """
    生产处置：仅允许 PRODUCTION_DISPOSES（65 留样完成 / 8 回退 / 99 关闭）。
    供生产工作台与外部生产系统调用。
    生产角色代操作时，流转表仍记 DISPOSED_OWNER_ID=生产 OP。
    """
    try:
        dispose = int(dispose)
        actor_user_id = int(actor_user_id)
    except (TypeError, ValueError):
        return False, 'dispose 无效', None
    if dispose not in PRODUCTION_DISPOSES:
        return False, '非生产处置行为', None

    effective_actor = actor_user_id
    effective_role = actor_role
    if actor_role == ROLE_PRODUCTION:
        effective_actor = _production_op_id()
        effective_role = ROLE_PRODUCTION

    # 兼容旧客户端：生产自由备注曾走 dispose_detail
    manual = dispose_manual_note
    if manual is None and dispose_detail is not None:
        raw = str(dispose_detail).strip()
        if raw and not _is_structured_dispose_detail(raw):
            manual = dispose_detail
            dispose_detail = None

    if dispose == DISPOSE_SAMPLE_DONE:
        return dispose_sample_done(
            hold_record_id=hold_record_id,
            actor_user_id=effective_actor,
            actor_role=effective_role,
            dispose_manual_note=manual,
        )

    return dispose_record(
        hold_record_id=hold_record_id,
        dispose=dispose,
        actor_user_id=effective_actor,
        actor_role=effective_role,
        dispose_detail=dispose_detail,
        dispose_manual_note=manual,
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
            text(f"""
                SELECT
                    c.ID, c.HOLD_RECORD_ID, c.DISPOSED_OWNER_ID, c.DISPOSE,
                    c.NEXT_OWNER_ID, c.DISPOSE_SOURCE, c.DISPOSE_DTTM,
                    c.DISPOSE_TYPE, c.DISPOSE_DETAIL, c.DISPOSE_NOTE, c.DISPOSE_MANUAL_NOTE,
                    u1.NAME AS DISPOSED_OWNER_NAME,
                    u2.NAME AS NEXT_OWNER_NAME
                FROM {_circ_table()} c
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
    related_user_id=None,
    page=1,
    page_size=20,
    limit=None,
    max_page_size=200,
):
    """
    流转记录查询（默认全量可读，不按角色/型号归属过滤，分页）。
    可按 hold_record_id / product_id / wafer_id / lot_id / dispose / keyword 筛选。
    related_user_id：仅本人相关（经办人 / 下一 owner / 所属型号）。
    成功返回 (True, msg, page_payload)。
    """
    try:
        from app.controllers.hold_report_ctrl import _parse_page, _page_payload

        record_table = _record_table()
        if limit is not None and (page is None or str(page) in ('', '1')):
            page, page_size, offset = _parse_page(1, limit, max_page_size=max_page_size)
        else:
            page, page_size, offset = _parse_page(page, page_size, max_page_size=max_page_size)

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
                    OR UPPER(NVL(c.DISPOSE_NOTE, '')) LIKE UPPER(:keyword)
                    OR UPPER(NVL(c.DISPOSE_MANUAL_NOTE, '')) LIKE UPPER(:keyword)
                )
            """
            params['keyword'] = f"%{str(keyword).strip()}%"

        if related_user_id is not None and str(related_user_id).strip() != '':
            try:
                params['related_user_id'] = int(related_user_id)
            except (TypeError, ValueError):
                return False, 'related_user_id 无效', _page_payload([], 0, page, page_size)
            where_sql += """
                AND (
                    c.DISPOSED_OWNER_ID = :related_user_id
                    OR c.NEXT_OWNER_ID = :related_user_id
                    OR r.PRODUCT_ID IN (
                        SELECT p.PRODUCT_ID
                        FROM PRODUCT_INFO p
                        WHERE p.PRO_ENG_ID = :related_user_id
                    )
                )
            """

        from_sql = f"""
            FROM {_circ_table()} c
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
                c.DISPOSE_TYPE, c.DISPOSE_DETAIL, c.DISPOSE_NOTE, c.DISPOSE_MANUAL_NOTE,
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


CIRCULATION_EXPORT_HEADERS = [
    '流转ID',
    'Record ID',
    '型号',
    'Lot',
    'Wafer',
    '站点',
    '行为',
    '经办人',
    '当前owner',
    '处置时间',
    '处置详情',
    '工程备注',
    '手输备注',
]


def circulation_export_row(item):
    return [
        item.get('ID'),
        item.get('HOLD_RECORD_ID'),
        item.get('PRODUCT_ID') or '',
        item.get('LOT_ID') or '',
        item.get('WAFER_ID') or '',
        item.get('STATION') or '',
        item.get('DISPOSE_LABEL') or item.get('DISPOSE') or '',
        item.get('DISPOSED_OWNER_NAME') or item.get('DISPOSED_OWNER_ID') or '',
        item.get('NEXT_OWNER_NAME') or item.get('NEXT_OWNER_ID') or '',
        item.get('DISPOSE_DTTM') or '',
        item.get('DISPOSE_DETAIL') or '',
        item.get('DISPOSE_NOTE') or '',
        item.get('DISPOSE_MANUAL_NOTE') or '',
    ]


def export_circulations_xlsx(
    hold_record_id=None,
    product_id='',
    wafer_id='',
    lot_id='',
    dispose=None,
    keyword='',
    related_user_id=None,
):
    """导出流转记录为 xlsx（筛选条件与列表一致，最多 5000 行）。"""
    from app.utils.excel_export import EXPORT_MAX_ROWS, from_page_payload

    success, msg, payload = query_circulations(
        hold_record_id=hold_record_id,
        product_id=product_id,
        wafer_id=wafer_id,
        lot_id=lot_id,
        dispose=dispose,
        keyword=keyword,
        related_user_id=related_user_id,
        page=1,
        page_size=EXPORT_MAX_ROWS,
        max_page_size=EXPORT_MAX_ROWS,
    )
    return from_page_payload(
        success, msg, payload,
        CIRCULATION_EXPORT_HEADERS, circulation_export_row, 'Hold流转',
    )


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
        from app.controllers.hold_report_ctrl import RECORD_TYPE_LABELS, _parse_page, _page_payload

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
            INNER JOIN {_circ_table()} c
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
                r.GRADE_NUM, r.RECORD_TYPE, r.STATUS, r.LAST_CIRCULATION_ID, r.HOLD_DTTM,
                r.ANNEX_FTP_PATH,
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
            item = enrich_record_grades(_row_to_dict(r))
            last_dispose = item.get('LAST_DISPOSE')
            item['LAST_DISPOSE_LABEL'] = DISPOSE_LABELS.get(last_dispose, str(last_dispose))
            try:
                rt_key = int(item.get('RECORD_TYPE')) if item.get('RECORD_TYPE') is not None else None
            except (TypeError, ValueError):
                rt_key = None
            item['RECORD_TYPE_NAME'] = RECORD_TYPE_LABELS.get(rt_key, '-')
            item['IS_AQL_HOLD'] = hold_code_is_aql(item.get('HOLD_CODE'))
            item['ANNEX_COUNT'] = len(parse_annex_ftp_paths(item.get('ANNEX_FTP_PATH')))
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
    dispose_note=None,
    dispose_manual_note=None,
    downgrades=None,
    retest_grades=None,
    retest_code=None,
    wafer_actions=None,
    confirm_interval=False,
):
    """
    对 hold_record 执行一次处置流转。
    成功返回 (True, msg, {circulation_id, next_owner_id, ...})
    DISPOSE_DETAIL：规则化详情（降级/重测等）。
    DISPOSE_NOTE：工程师工程备注。
    DISPOSE_MANUAL_NOTE：手输备注（任意处置可选；可靠性分析之后的放行/降级必填）。
    工程师处置仅为意见：仅回写 LAST_CIRCULATION_ID / STATUS，不改 GRADE_NUM。
    WLT 可传 wafer_actions；此时 dispose 可省略，由各片汇总。
    confirm_interval：距可靠性分析不足 30 分钟时须为 true 才能提交后续处置。
    """
    try:
        rid = int(hold_record_id)
        actor_user_id = int(actor_user_id)
    except (TypeError, ValueError):
        return False, '参数无效', None

    dispose_given = dispose is not None and str(dispose).strip() != ''
    if dispose_given:
        try:
            dispose = int(dispose)
        except (TypeError, ValueError):
            return False, '参数无效', None
    else:
        dispose = None

    if dispose == DISPOSE_SAMPLE_DONE:
        return dispose_sample_done(
            hold_record_id=hold_record_id,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            dispose_manual_note=dispose_manual_note,
        )

    confirm_interval = _truthy_flag(confirm_interval)

    try:
        record = _load_record(rid)
        if not record:
            return False, 'hold_record 不存在', None

        if int(record.get('STATUS') or 0) == DISPOSE_CLOSE:
            return False, '记录已关闭，无法继续处置', None

        attach_reliability_followup(record)
        after_analyze = bool(record.get('AFTER_RELIABILITY_ANALYZE'))
        if after_analyze and wafer_actions is not None:
            for action in wafer_actions:
                if not isinstance(action, dict):
                    continue
                try:
                    wafer_disp = int(action.get('dispose'))
                except (TypeError, ValueError):
                    continue
                if wafer_disp not in AFTER_ANALYZE_ALLOWED:
                    return False, '可靠性分析之后仅允许放行或降级', None
        if (
            after_analyze
            and dispose is not None
            and dispose in ENGINEER_DISPOSES
            and dispose not in AFTER_ANALYZE_ALLOWED
        ):
            return False, '可靠性分析之后仅允许放行或降级', None

        try:
            record_type = (
                int(record.get('RECORD_TYPE'))
                if record.get('RECORD_TYPE') is not None
                else None
            )
        except (TypeError, ValueError):
            return False, 'record_type 无效', None

        is_wlt = record_type == RECORD_TYPE_WLT
        engineer_side = dispose in ENGINEER_DISPOSES if dispose is not None else (
            wafer_actions is not None
        )
        if is_wlt and engineer_side and wafer_actions is None:
            return False, 'WLT 处置须提供 wafer_actions', None
        if (not is_wlt) and wafer_actions is not None:
            return False, '仅 WLT 处置单支持按片 wafer_actions', None
        if dispose is None and wafer_actions is None:
            return False, '参数无效', None

        ok_note, note_or_err = normalize_dispose_note(dispose_note)
        if not ok_note:
            return False, note_or_err, None
        note = note_or_err

        ok_manual, manual_or_err = normalize_dispose_manual_note(dispose_manual_note)
        if not ok_manual:
            return False, manual_or_err, None
        manual_note = manual_or_err

        detail = None
        if is_wlt and wafer_actions is not None:
            built = build_dispose_detail(
                dispose=dispose,
                dispose_detail=dispose_detail,
                record_type=record_type,
                wafer_actions=wafer_actions,
                expected_wafers=record.get('WAFERS') or list_wafers_for_record(record),
            )
            if not built[0]:
                return False, built[1], None
            detail = built[1]
            summarized = built[2]
            if dispose is not None and int(dispose) != int(summarized):
                return False, (
                    f'dispose 与按片汇总不一致（汇总为 {summarized}，'
                    f'传入 {dispose}）'
                ), None
            dispose = int(summarized)
            if dispose not in ENGINEER_DISPOSES and actor_role != ROLE_ROOT:
                return False, '非工程师处置行为', None
        else:
            has_structured = (
                downgrades is not None
                or retest_grades is not None
                or retest_code is not None
            )

            # 兼容旧客户端：放行/降级把工程备注塞在 dispose_detail
            if (
                dispose == DISPOSE_RELEASE
                and note is None
                and dispose_detail is not None
                and not has_structured
            ):
                raw = str(dispose_detail).strip()
                if raw and not _is_structured_dispose_detail(raw):
                    ok_note, note_or_err = normalize_dispose_note(dispose_detail)
                    if not ok_note:
                        return False, note_or_err, None
                    note = note_or_err
                    dispose_detail = None
            elif (
                dispose == DISPOSE_DOWNGRADE
                and note is None
                and has_structured
                and dispose_detail is not None
                and not str(dispose_detail).strip().upper().startswith('DG:')
            ):
                ok_note, note_or_err = normalize_dispose_note(dispose_detail)
                if not ok_note:
                    return False, note_or_err, None
                note = note_or_err
                dispose_detail = None

            # 兼容：分析/其它行为曾把手输备注放在 dispose_detail
            if (
                manual_note is None
                and dispose_detail is not None
                and dispose not in (DISPOSE_DOWNGRADE, DISPOSE_RETEST)
                and not has_structured
            ):
                raw = str(dispose_detail).strip()
                if raw and not _is_structured_dispose_detail(raw):
                    ok_manual, manual_or_err = normalize_dispose_manual_note(dispose_detail)
                    if not ok_manual:
                        return False, manual_or_err, None
                    manual_note = manual_or_err
                    dispose_detail = None

            if dispose in (DISPOSE_DOWNGRADE, DISPOSE_RETEST) and not has_structured and dispose_detail:
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
                    raw = str(dispose_detail).strip()
                    if _is_structured_dispose_detail(raw):
                        detail = raw
                        if len(detail) > DISPOSE_DETAIL_MAX_LEN:
                            return False, f'dispose_detail 最长 {DISPOSE_DETAIL_MAX_LEN} 字符', None

        if after_analyze and dispose in ENGINEER_DISPOSES:
            if dispose not in AFTER_ANALYZE_ALLOWED:
                return False, '可靠性分析之后仅允许放行或降级', None
            if not manual_note:
                return False, '可靠性分析之后须手输备注', None
            if record.get('ANALYZE_INTERVAL_WARN') and not confirm_interval:
                return False, INTERVAL_CONFIRM_MSG, {'need_interval_confirm': True}

        last_circ = _load_circulation(record.get('LAST_CIRCULATION_ID'))
        current_owner_id = last_circ.get('NEXT_OWNER_ID') if last_circ else None

        ok, err = _actor_may_dispose(
            dispose, actor_user_id, actor_role, current_owner_id, last_circ=last_circ,
        )
        if not ok:
            return False, err, None

        next_owner_id, disposed_owner_id = _resolve_owners(
            dispose, record['PRODUCT_ID'], actor_user_id,
        )

        record_table = _record_table()
        circ_id = _next_positive_seq(_circ_seq())

        db.session.execute(
            text(f"""
                INSERT INTO {_circ_table()} (
                    ID,
                    HOLD_RECORD_ID,
                    DISPOSED_OWNER_ID,
                    DISPOSE,
                    NEXT_OWNER_ID,
                    DISPOSE_SOURCE,
                    DISPOSE_DTTM,
                    DISPOSE_TYPE,
                    DISPOSE_DETAIL,
                    DISPOSE_NOTE,
                    DISPOSE_MANUAL_NOTE
                ) VALUES (
                    :circ_id,
                    :hold_record_id,
                    :disposed_owner_id,
                    :dispose,
                    :next_owner_id,
                    :dispose_source,
                    SYSDATE,
                    :dispose_type,
                    :dispose_detail,
                    :dispose_note,
                    :dispose_manual_note
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
                'dispose_note': note,
                'dispose_manual_note': manual_note,
            },
        )

        # 仅回写流转指针与 STATUS；不改 GRADE_NUM（工程师意见未落地）
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
        try:
            from app.utils.legacy_dispose_writeback import writeback_after_dispose
            writeback_after_dispose(
                record,
                dispose=dispose,
                actor_user_id=actor_user_id,
                dispose_detail=detail,
                dispose_note=note,
                dispose_manual_note=manual_note,
                wafer_actions=wafer_actions,
                downgrades=downgrades,
                retest_grades=retest_grades,
            )
        except Exception:
            logging.getLogger(__name__).warning(
                'legacy_writeback: hook error', exc_info=True
            )
        return True, '处置成功', {
            'hold_record_id': rid,
            'circulation_id': circ_id,
            'dispose': dispose,
            'dispose_label': DISPOSE_LABELS.get(dispose, str(dispose)),
            'disposed_owner_id': disposed_owner_id,
            'next_owner_id': next_owner_id,
            'dispose_detail': detail,
            'dispose_note': note,
            'dispose_manual_note': manual_note,
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
