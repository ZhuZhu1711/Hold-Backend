"""新系统处置成功后，静默写回 AutoHoldSys 旧表。失败只记日志，不回滚新单。"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable, Optional

from app.config import Config

logger = logging.getLogger(__name__)


def _db():
    """延迟导入，避免纯函数单测依赖 Flask/Oracle。"""
    from sqlalchemy import text
    from app import db
    return db, text


def _normalize_lot_id(lot_id) -> str:
    text = str(lot_id).strip() if lot_id is not None else ''
    if not text:
        return ''
    if '-' in text:
        return text.split('-', 1)[0].strip()
    return text


def _wafer_suffix(wafer_id) -> str:
    text = str(wafer_id).strip() if wafer_id is not None else ''
    if not text:
        return ''
    if '-' in text:
        return text.rsplit('-', 1)[-1].strip()
    return text


def expand_display_wafer_ids(wafer_id, lot_id) -> list:
    """#01 + lot → [LOT-01]；完整片号原样返回。"""
    wafer = str(wafer_id).strip() if wafer_id is not None else ''
    lot = _normalize_lot_id(lot_id)
    if not wafer:
        return []
    if wafer.startswith('#'):
        if not lot:
            return []
        return [f'{lot}-{suffix}' for suffix in re.findall(r'#([^#\s]+)', wafer) if suffix]
    return [wafer]

# 新 dispose → 旧 DISPOSE / eng_dispose
NEW_TO_OLD_DISPOSE = {
    1: 0,  # 放行
    2: 1,  # 降级
    3: 2,  # 重测
    5: 4,  # 分析 / RE
}

RECORD_TYPE_WLT = 2
COMMENT_MAX_LEN = 4000

TB_HOLD_INFO = 'HOLD_INFO'
TB_WLT_HOLD_INFO = 'WLT_HOLD_INFO'
TB_HISTORY = 'HISTORY_DISPOSITION'

EXTEND_DOWNGRADE_NOSPLIT = '降级main(不拆批)'
EXTEND_DOWNGRADE_SPLIT = '降级main(拆批)'
EXTEND_REWORK = 'Rework WLT'
EXTEND_FIXTURE_A = 'A夹具重测(备注中填code)'
EXTEND_FIXTURE_B = 'B夹具重测(备注中填code)'

DG_MODE_MAIN_SPLIT = 'main_split'
RT_MODE_FULL = 'full'
RT_MODE_FIXTURE_A = 'fixture_a'
RT_MODE_FIXTURE_B = 'fixture_b'


def map_new_dispose_to_old(dispose) -> Optional[int]:
    try:
        return NEW_TO_OLD_DISPOSE.get(int(dispose))
    except (TypeError, ValueError):
        return None


def writeback_enabled() -> bool:
    return bool(getattr(Config, 'LEGACY_DISPOSE_WRITEBACK_ENABLED', False))


def _sys_comment(*parts: Any) -> str:
    extras = [str(p).strip() for p in parts if p is not None and str(p).strip()]
    if not extras:
        text = 'SYS\n'
    else:
        text = 'SYS\n' + '\n'.join(extras)
    if len(text) > COMMENT_MAX_LEN:
        return text[:COMMENT_MAX_LEN]
    return text


def _first_note(*candidates: Any) -> str:
    for raw in candidates:
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            return text
    return ''


def _strip_detail_prefix(detail: Any, prefixes: tuple[str, ...]) -> str:
    text = str(detail).strip() if detail is not None else ''
    if not text:
        return ''
    upper = text.upper()
    for prefix in prefixes:
        if upper.startswith(prefix.upper()):
            return text[len(prefix):].strip()
    return text


def build_ate_comment(
    dispose: int,
    *,
    dispose_detail: Any = None,
    dispose_note: Any = None,
    dispose_manual_note: Any = None,
    downgrades: Any = None,
    retest_grades: Any = None,
) -> str:
    """还原旧 ATE comment：SYS\\n{等级行}\\n{备注}。"""
    manual = _first_note(dispose_manual_note, dispose_note)
    try:
        code = int(dispose)
    except (TypeError, ValueError):
        return _sys_comment(manual)

    if code == 2:
        pairs: list[str] = []
        if isinstance(downgrades, list):
            for item in downgrades:
                if not isinstance(item, dict):
                    continue
                src = str(item.get('from') or '').strip()
                dst = str(item.get('to') or '').strip()
                if src and dst:
                    pairs.append(f'{src}>{dst}')
                elif src:
                    pairs.append(src)
        grade_line = ';'.join(pairs) if pairs else _strip_detail_prefix(
            dispose_detail, ('DG:',)
        )
        return _sys_comment(grade_line, manual)

    if code == 3:
        grades: list[str] = []
        if isinstance(retest_grades, list):
            for g in retest_grades:
                token = str(g).strip() if g is not None else ''
                if token and token not in grades:
                    grades.append(token)
        if grades:
            grade_line = ', '.join(grades)
        else:
            grade_line = _strip_detail_prefix(dispose_detail, ('RT:', 'RT:CODE='))
        return _sys_comment(grade_line, manual)

    return _sys_comment(manual)


def build_wlt_comment(
    dispose: int,
    *,
    downgrade_mode: Any = None,
    retest_mode: Any = None,
    retest_codes: Any = None,
    dispose_note: Any = None,
    dispose_manual_note: Any = None,
) -> str:
    """还原旧 WLT 单片 comment。"""
    manual = _first_note(dispose_manual_note, dispose_note)
    codes = str(retest_codes).strip() if retest_codes is not None else ''
    try:
        code = int(dispose)
    except (TypeError, ValueError):
        return _sys_comment(manual)

    if code == 2:
        mode = str(downgrade_mode or '').strip().lower()
        extend = (
            EXTEND_DOWNGRADE_SPLIT
            if mode == DG_MODE_MAIN_SPLIT
            else EXTEND_DOWNGRADE_NOSPLIT
        )
        return _sys_comment(extend, manual)

    if code == 3:
        mode = str(retest_mode or '').strip().lower()
        if mode == RT_MODE_FIXTURE_A:
            return _sys_comment(EXTEND_FIXTURE_A, codes or manual)
        if mode == RT_MODE_FIXTURE_B:
            return _sys_comment(EXTEND_FIXTURE_B, codes or manual)
        return _sys_comment(EXTEND_REWORK, codes or manual)

    return _sys_comment(manual)


def _wafer_num_suffix(value: Any) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    if text.startswith('#'):
        m = re.search(r'#([^#\s]+)', text)
        return m.group(1).strip() if m else text[1:].strip()
    return _wafer_suffix(text)


def match_wlt_physical_wafer(
    display: Any,
    lot_id: Any,
    open_wafer_ids: Iterable[Any],
) -> Optional[str]:
    """把新系统展示片号对齐到旧 WLT_HOLD_INFO.WAFER_ID。"""
    rows = [str(w).strip() for w in (open_wafer_ids or []) if str(w or '').strip()]
    if not rows:
        return None
    lot = str(lot_id or '').strip()
    disp = str(display or '').strip()
    expanded = expand_display_wafer_ids(disp, lot)
    full_id = expanded[0] if expanded else disp
    suffix = _wafer_num_suffix(full_id) or _wafer_num_suffix(disp)

    exact: list[str] = []
    suffix_hits: list[str] = []
    full_u = full_id.upper()
    disp_u = disp.upper()
    suffix_u = suffix.upper()
    for wid in rows:
        wu = wid.upper()
        if wu == full_u or wu == disp_u:
            exact.append(wid)
            continue
        if suffix_u and _wafer_num_suffix(wid).upper() == suffix_u:
            suffix_hits.append(wid)

    if exact:
        return exact[0]
    if not suffix_hits:
        return None
    if lot and suffix:
        prefer = f'{lot}-{suffix}'.upper()
        for wid in suffix_hits:
            if wid.upper() == prefer:
                return wid
    return suffix_hits[0]


def writeback_after_dispose(
    record: Any,
    *,
    dispose: Any,
    actor_user_id: Any,
    dispose_detail: Any = None,
    dispose_note: Any = None,
    dispose_manual_note: Any = None,
    wafer_actions: Any = None,
    downgrades: Any = None,
    retest_grades: Any = None,
) -> None:
    """新表 commit 之后调用。任何异常都吞掉。"""
    try:
        if not writeback_enabled():
            logger.info('legacy_writeback: skipped (disabled)')
            return
        if not isinstance(record, dict):
            logger.warning('legacy_writeback: skip, record is not a dict')
            return
        old_code = map_new_dispose_to_old(dispose)
        if old_code is None:
            logger.info('legacy_writeback: skip unmapped dispose=%s', dispose)
            return
        try:
            eng_id = int(actor_user_id)
        except (TypeError, ValueError):
            logger.warning('legacy_writeback: skip invalid actor_user_id=%s', actor_user_id)
            return

        try:
            record_type = (
                int(record.get('RECORD_TYPE'))
                if record.get('RECORD_TYPE') is not None
                else None
            )
        except (TypeError, ValueError):
            record_type = None

        lot_id = str(record.get('LOT_ID') or '').strip()
        if not lot_id:
            logger.info(
                'legacy_writeback: skip empty LOT_ID hold_record_id=%s',
                record.get('ID'),
            )
            return

        if record_type == RECORD_TYPE_WLT:
            _writeback_wlt(
                lot_id=lot_id,
                eng_id=eng_id,
                wafer_actions=wafer_actions,
                dispose_note=dispose_note,
                dispose_manual_note=dispose_manual_note,
            )
        else:
            _writeback_ate(
                lot_id=lot_id,
                eng_id=eng_id,
                old_code=old_code,
                comment=build_ate_comment(
                    int(dispose),
                    dispose_detail=dispose_detail,
                    dispose_note=dispose_note,
                    dispose_manual_note=dispose_manual_note,
                    downgrades=downgrades,
                    retest_grades=retest_grades,
                ),
            )
    except Exception as exc:
        logger.warning('legacy_writeback: unexpected error: %s', exc, exc_info=True)
        try:
            db, _text = _db()
            db.session.rollback()
        except Exception:
            pass


def _writeback_ate(lot_id: str, eng_id: int, old_code: int, comment: str) -> None:
    db, text = _db()
    try:
        upd = db.session.execute(
            text(
                f"""
                UPDATE {TB_HOLD_INFO}
                SET status_code = 1, DISPOSE = :code, DISPOSE_COMMENT = :comment
                WHERE wafer_id = :lot_id AND status_code = 0
                """
            ),
            {'code': old_code, 'comment': comment, 'lot_id': lot_id},
        )
        if not upd.rowcount:
            logger.info('legacy_writeback: ATE no open HOLD_INFO row lot=%s', lot_id)
            db.session.rollback()
            return
        db.session.execute(
            text(
                f"""
                INSERT INTO {TB_HISTORY}
                    (pro_eng_id, wafer_id, dispose_time, eng_dispose, DISPOSE_COMMENT, DISPOSE_START)
                VALUES
                    (:eng_id, :lot_id, SYSDATE, :code, :comment, NULL)
                """
            ),
            {
                'eng_id': eng_id,
                'lot_id': lot_id,
                'code': old_code,
                'comment': comment,
            },
        )
        db.session.commit()
        logger.info('legacy_writeback: ATE ok lot=%s old_dispose=%s', lot_id, old_code)
    except Exception as exc:
        logger.warning('legacy_writeback: ATE sql failed lot=%s: %s', lot_id, exc)
        try:
            db.session.rollback()
        except Exception:
            pass


def _load_open_wlt_wafers(lot_id: str) -> list[str]:
    db, text = _db()
    result = db.session.execute(
        text(
            f"""
            SELECT wafer_id FROM {TB_WLT_HOLD_INFO}
            WHERE status_code = 0 AND lot_id = :lot_id
            """
        ),
        {'lot_id': lot_id},
    )
    rows = []
    for row in result:
        wid = row[0] if not isinstance(row, dict) else row.get('wafer_id') or row.get('WAFER_ID')
        if wid is not None and str(wid).strip():
            rows.append(str(wid).strip())
    return rows


def _load_open_wlt_wafer_fallback(physical_hint: str, suffix: str) -> list[str]:
    params = {'key': physical_hint}
    like_sql = ''
    if suffix:
        like_sql = ' OR UPPER(wafer_id) LIKE :like_pat'
        params['like_pat'] = f'%-{suffix}'.upper()
    db, text = _db()
    result = db.session.execute(
        text(
            f"""
            SELECT wafer_id FROM {TB_WLT_HOLD_INFO}
            WHERE status_code = 0
              AND (
                    UPPER(wafer_id) = UPPER(:key)
                    {like_sql}
                  )
            """
        ),
        params,
    )
    rows = []
    for row in result:
        wid = row[0] if not isinstance(row, dict) else row.get('wafer_id') or row.get('WAFER_ID')
        if wid is not None and str(wid).strip():
            rows.append(str(wid).strip())
    return rows


def _writeback_wlt(
    lot_id: str,
    eng_id: int,
    wafer_actions: Any,
    dispose_note: Any,
    dispose_manual_note: Any,
) -> None:
    if not isinstance(wafer_actions, list) or not wafer_actions:
        logger.info('legacy_writeback: WLT skip, empty wafer_actions lot=%s', lot_id)
        return

    db, text = _db()
    try:
        open_wafers = _load_open_wlt_wafers(lot_id)
    except Exception as exc:
        logger.warning('legacy_writeback: WLT load open rows failed lot=%s: %s', lot_id, exc)
        try:
            db.session.rollback()
        except Exception:
            pass
        return

    wrote = 0
    try:
        for action in wafer_actions:
            if not isinstance(action, dict):
                continue
            try:
                new_code = int(action.get('dispose'))
            except (TypeError, ValueError):
                logger.warning(
                    'legacy_writeback: WLT skip invalid dispose wafer=%s',
                    action.get('wafer'),
                )
                continue
            old_code = map_new_dispose_to_old(new_code)
            if old_code is None:
                logger.info(
                    'legacy_writeback: WLT skip unmapped dispose=%s wafer=%s',
                    new_code,
                    action.get('wafer'),
                )
                continue

            display = action.get('wafer')
            physical = match_wlt_physical_wafer(display, lot_id, open_wafers)
            if physical is None:
                expanded = expand_display_wafer_ids(str(display or ''), lot_id)
                hint = expanded[0] if expanded else str(display or '')
                suffix = _wafer_num_suffix(hint) or _wafer_num_suffix(display)
                try:
                    extra = _load_open_wlt_wafer_fallback(hint, suffix)
                except Exception as exc:
                    logger.warning(
                        'legacy_writeback: WLT fallback query failed wafer=%s: %s',
                        display,
                        exc,
                    )
                    extra = []
                physical = match_wlt_physical_wafer(display, lot_id, extra)
            if not physical:
                logger.info(
                    'legacy_writeback: WLT no open wafer match display=%s lot=%s',
                    display,
                    lot_id,
                )
                continue

            comment = build_wlt_comment(
                new_code,
                downgrade_mode=action.get('downgrade_mode'),
                retest_mode=action.get('retest_mode'),
                retest_codes=action.get('retest_codes'),
                dispose_note=dispose_note,
                dispose_manual_note=dispose_manual_note,
            )
            upd = db.session.execute(
                text(
                    f"""
                    UPDATE {TB_WLT_HOLD_INFO}
                    SET status_code = 1, DISPOSE = :code, DISPOSE_COMMENT = :comment
                    WHERE wafer_id = :wafer_id AND status_code = 0
                    """
                ),
                {'code': old_code, 'comment': comment, 'wafer_id': physical},
            )
            if not upd.rowcount:
                logger.info(
                    'legacy_writeback: WLT UPDATE 0 rows wafer=%s lot=%s',
                    physical,
                    lot_id,
                )
                continue
            db.session.execute(
                text(
                    f"""
                    INSERT INTO {TB_HISTORY}
                        (pro_eng_id, wafer_id, dispose_time, eng_dispose, DISPOSE_COMMENT, DISPOSE_START)
                    VALUES
                        (:eng_id, :wafer_id, SYSDATE, :code, :comment, NULL)
                    """
                ),
                {
                    'eng_id': eng_id,
                    'wafer_id': physical,
                    'code': old_code,
                    'comment': comment,
                },
            )
            wrote += 1
            if physical in open_wafers:
                open_wafers.remove(physical)

        if wrote:
            db.session.commit()
            logger.info('legacy_writeback: WLT ok lot=%s wafers=%s', lot_id, wrote)
        else:
            db.session.rollback()
            logger.info('legacy_writeback: WLT wrote 0 rows lot=%s', lot_id)
    except Exception as exc:
        logger.warning('legacy_writeback: WLT sql failed lot=%s: %s', lot_id, exc)
        try:
            db.session.rollback()
        except Exception:
            pass
