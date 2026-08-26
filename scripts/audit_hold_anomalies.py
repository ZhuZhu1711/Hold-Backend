#!/usr/bin/env python
"""Hold record / info / circulation 异常排查（只读，不写库）。

检查项（涉及 record 的均忽略 STATUS=99 已关闭）：
  1. 非手提 record（SOURCE<>1）无任何 hold_info 关联
  2. record 在流转表无任何记录
  3. 流转记录指向不存在的 record
  4. LAST_CIRCULATION_ID 为空，但已有流转行
  5. LAST_CIRCULATION_ID 指向不存在的流转
  6. LAST_CIRCULATION_ID 指向别的 record 的流转
  7. LAST_CIRCULATION_ID 不是该单最新流转（按 ID）
  8. STATUS 与最新流转 DISPOSE 不一致
  9. hold_info.HOLD_RECORD_ID>0 但 record 不存在
 10. 手提 record（SOURCE=1）却挂了 hold_info（异常）
 11. RECORD_TYPE 不在 0/1/2
 12. 未关闭 record：关联 info 全是 HOLDING=1（应被自动关闭的候选）
 13. hold_info.HOLD_RECORD_ID=-1 脏数据条数（仅统计）

用法（Hold-Backend 根目录）:
  python scripts/audit_hold_anomalies.py
  python scripts/audit_hold_anomalies.py --test
  python scripts/audit_hold_anomalies.py --limit 20
  python scripts/audit_hold_anomalies.py --only 1,2,3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import oracledb

from app.utils.database_util import DISPOSE_CLOSE, DSN, PWD, USER

TableTriple = tuple[str, str, str]  # info, record, circ

# 已关闭 record 一律不报
_CLOSED_BINDS = {'closed': DISPOSE_CLOSE}
_NOT_CLOSED = 'NVL(r.STATUS, 0) <> :closed'


def _tables(use_test: bool) -> TableTriple:
    if use_test:
        return (
            'FT_HOLD_INFO_TEST',
            'FT_HOLD_RECORD_TEST',
            'CIRCULATION_HISTORY_TEST',
        )
    return 'FT_HOLD_INFO', 'FT_HOLD_RECORD', 'CIRCULATION_HISTORY'


def _fetch(
    conn: oracledb.Connection,
    sql: str,
    binds: dict | None = None,
    limit: int = 20,
) -> tuple[int, list[dict]]:
    """外层包一层计数；明细最多 limit 条。"""
    binds = dict(binds or {})
    count_sql = f'SELECT COUNT(*) AS CNT FROM ({sql})'
    # Oracle 绑定名须以字母开头，不能用 :_lim
    detail_sql = f'SELECT * FROM ({sql}) WHERE ROWNUM <= :row_limit'
    binds_detail = {**binds, 'row_limit': max(1, int(limit))}

    with conn.cursor() as cur:
        cur.execute(count_sql, binds)
        total = int(cur.fetchone()[0] or 0)
        if total <= 0:
            return 0, []
        cur.execute(detail_sql, binds_detail)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    return total, rows


def _print_section(title: str, total: int, rows: list[dict]) -> None:
    print()
    print('=' * 72)
    print(f'{title}')
    print(f'命中: {total}')
    if not rows:
        return
    keys = list(rows[0].keys())
    print('样例字段: ' + ', '.join(keys))
    for r in rows:
        parts = [f'{k}={r[k]!r}' for k in keys]
        print('  - ' + ' | '.join(parts))
    if total > len(rows):
        print(f'  ... 另有 {total - len(rows)} 条未列出')


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------

def check_1_mes_record_no_info(
    conn: oracledb.Connection, tables: TableTriple, limit: int,
) -> tuple[str, int, list[dict]]:
    info, record, _ = tables
    sql = f"""
        SELECT r.ID, r.SOURCE, r.STATUS, r.RECORD_TYPE, r.PRODUCT_ID,
               r.LOT_ID, r.WAFER_ID, r.HOLD_CODE, r.HOLD_DTTM
        FROM {record} r
        WHERE {_NOT_CLOSED}
          AND NVL(r.SOURCE, 0) <> 1
          AND NOT EXISTS (
              SELECT 1 FROM {info} i WHERE i.HOLD_RECORD_ID = r.ID
          )
        ORDER BY r.ID
    """
    total, rows = _fetch(conn, sql, _CLOSED_BINDS, limit=limit)
    return '1. 非手提 record 无 hold_info 关联', total, rows


def check_2_record_no_circulation(
    conn: oracledb.Connection, tables: TableTriple, limit: int,
) -> tuple[str, int, list[dict]]:
    _, record, circ = tables
    sql = f"""
        SELECT r.ID, r.SOURCE, r.STATUS, r.RECORD_TYPE, r.PRODUCT_ID,
               r.LOT_ID, r.LAST_CIRCULATION_ID, r.HOLD_DTTM
        FROM {record} r
        WHERE {_NOT_CLOSED}
          AND NOT EXISTS (
            SELECT 1 FROM {circ} c WHERE c.HOLD_RECORD_ID = r.ID
          )
        ORDER BY r.ID
    """
    total, rows = _fetch(conn, sql, _CLOSED_BINDS, limit=limit)
    return '2. record 无任何流转记录', total, rows


def check_3_circ_orphan_record(
    conn: oracledb.Connection, tables: TableTriple, limit: int,
) -> tuple[str, int, list[dict]]:
    _, record, circ = tables
    sql = f"""
        SELECT c.ID AS CIRC_ID, c.HOLD_RECORD_ID, c.DISPOSE, c.DISPOSE_TYPE,
               c.DISPOSE_DTTM, c.NEXT_OWNER_ID
        FROM {circ} c
        WHERE NOT EXISTS (
            SELECT 1 FROM {record} r WHERE r.ID = c.HOLD_RECORD_ID
        )
        ORDER BY c.ID
    """
    total, rows = _fetch(conn, sql, limit=limit)
    return '3. 流转记录无对应 record', total, rows


def check_4_last_circ_null_but_exists(
    conn: oracledb.Connection, tables: TableTriple, limit: int,
) -> tuple[str, int, list[dict]]:
    _, record, circ = tables
    sql = f"""
        SELECT r.ID, r.STATUS, r.SOURCE, r.PRODUCT_ID, r.LOT_ID,
               (SELECT COUNT(*) FROM {circ} c WHERE c.HOLD_RECORD_ID = r.ID) AS CIRC_CNT
        FROM {record} r
        WHERE {_NOT_CLOSED}
          AND r.LAST_CIRCULATION_ID IS NULL
          AND EXISTS (
              SELECT 1 FROM {circ} c WHERE c.HOLD_RECORD_ID = r.ID
          )
        ORDER BY r.ID
    """
    total, rows = _fetch(conn, sql, _CLOSED_BINDS, limit=limit)
    return '4. LAST_CIRCULATION_ID 为空但已有流转', total, rows


def check_5_last_circ_missing(
    conn: oracledb.Connection, tables: TableTriple, limit: int,
) -> tuple[str, int, list[dict]]:
    _, record, circ = tables
    sql = f"""
        SELECT r.ID, r.STATUS, r.SOURCE, r.PRODUCT_ID, r.LOT_ID,
               r.LAST_CIRCULATION_ID
        FROM {record} r
        WHERE {_NOT_CLOSED}
          AND r.LAST_CIRCULATION_ID IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM {circ} c WHERE c.ID = r.LAST_CIRCULATION_ID
          )
        ORDER BY r.ID
    """
    total, rows = _fetch(conn, sql, _CLOSED_BINDS, limit=limit)
    return '5. LAST_CIRCULATION_ID 指向不存在的流转', total, rows


def check_6_last_circ_wrong_owner(
    conn: oracledb.Connection, tables: TableTriple, limit: int,
) -> tuple[str, int, list[dict]]:
    _, record, circ = tables
    sql = f"""
        SELECT r.ID AS RECORD_ID, r.STATUS, r.LAST_CIRCULATION_ID,
               c.HOLD_RECORD_ID AS CIRC_HOLD_RECORD_ID, c.DISPOSE
        FROM {record} r
        INNER JOIN {circ} c ON c.ID = r.LAST_CIRCULATION_ID
        WHERE {_NOT_CLOSED}
          AND c.HOLD_RECORD_ID <> r.ID
        ORDER BY r.ID
    """
    total, rows = _fetch(conn, sql, _CLOSED_BINDS, limit=limit)
    return '6. LAST_CIRCULATION_ID 指向其他 record 的流转', total, rows


def check_7_last_circ_not_latest(
    conn: oracledb.Connection, tables: TableTriple, limit: int,
) -> tuple[str, int, list[dict]]:
    _, record, circ = tables
    sql = f"""
        SELECT r.ID, r.STATUS, r.LAST_CIRCULATION_ID, m.MAX_CIRC_ID
        FROM {record} r
        INNER JOIN (
            SELECT HOLD_RECORD_ID, MAX(ID) AS MAX_CIRC_ID
            FROM {circ}
            GROUP BY HOLD_RECORD_ID
        ) m ON m.HOLD_RECORD_ID = r.ID
        WHERE {_NOT_CLOSED}
          AND r.LAST_CIRCULATION_ID IS NOT NULL
          AND r.LAST_CIRCULATION_ID <> m.MAX_CIRC_ID
        ORDER BY r.ID
    """
    total, rows = _fetch(conn, sql, _CLOSED_BINDS, limit=limit)
    return '7. LAST_CIRCULATION_ID 不是该单最新流转(按ID)', total, rows


def check_8_status_ne_last_dispose(
    conn: oracledb.Connection, tables: TableTriple, limit: int,
) -> tuple[str, int, list[dict]]:
    _, record, circ = tables
    sql = f"""
        SELECT r.ID, r.STATUS, c.ID AS CIRC_ID, c.DISPOSE AS LAST_DISPOSE,
               r.PRODUCT_ID, r.LOT_ID, r.SOURCE
        FROM {record} r
        INNER JOIN {circ} c ON c.ID = r.LAST_CIRCULATION_ID
        WHERE {_NOT_CLOSED}
          AND NVL(r.STATUS, -1) <> NVL(c.DISPOSE, -2)
        ORDER BY r.ID
    """
    total, rows = _fetch(conn, sql, _CLOSED_BINDS, limit=limit)
    return '8. STATUS 与最新流转 DISPOSE 不一致', total, rows


def check_9_info_points_missing_record(
    conn: oracledb.Connection, tables: TableTriple, limit: int,
) -> tuple[str, int, list[dict]]:
    info, record, _ = tables
    sql = f"""
        SELECT i.ID AS INFO_ID, i.HOLD_RECORD_ID, i.PRODUCT_ID, i.LOT_ID,
               i.WAFER_ID, i.HOLD_CODE, i.HOLDING, i.HOLD_DTTM
        FROM {info} i
        WHERE i.HOLD_RECORD_ID IS NOT NULL
          AND i.HOLD_RECORD_ID > 0
          AND NOT EXISTS (
              SELECT 1 FROM {record} r WHERE r.ID = i.HOLD_RECORD_ID
          )
        ORDER BY i.ID
    """
    total, rows = _fetch(conn, sql, limit=limit)
    return '9. hold_info 指向不存在的 record', total, rows


def check_10_manual_with_info(
    conn: oracledb.Connection, tables: TableTriple, limit: int,
) -> tuple[str, int, list[dict]]:
    info, record, _ = tables
    sql = f"""
        SELECT r.ID, r.STATUS, r.PRODUCT_ID, r.LOT_ID, r.HOLD_CODE,
               (SELECT COUNT(*) FROM {info} i WHERE i.HOLD_RECORD_ID = r.ID) AS INFO_CNT
        FROM {record} r
        WHERE {_NOT_CLOSED}
          AND NVL(r.SOURCE, 0) = 1
          AND EXISTS (
              SELECT 1 FROM {info} i WHERE i.HOLD_RECORD_ID = r.ID
          )
        ORDER BY r.ID
    """
    total, rows = _fetch(conn, sql, _CLOSED_BINDS, limit=limit)
    return '10. 手提 record(SOURCE=1) 却挂了 hold_info', total, rows


def check_11_bad_record_type(
    conn: oracledb.Connection, tables: TableTriple, limit: int,
) -> tuple[str, int, list[dict]]:
    _, record, _ = tables
    sql = f"""
        SELECT r.ID, r.RECORD_TYPE, r.STATUS, r.SOURCE, r.PRODUCT_ID,
               r.LOT_ID, r.HOLD_CODE, r.STATION
        FROM {record} r
        WHERE {_NOT_CLOSED}
          AND (
               r.RECORD_TYPE IS NULL
            OR r.RECORD_TYPE NOT IN (0, 1, 2)
          )
        ORDER BY r.ID
    """
    total, rows = _fetch(conn, sql, _CLOSED_BINDS, limit=limit)
    return '11. RECORD_TYPE 不在 0/1/2', total, rows


def check_12_unclosed_all_released(
    conn: oracledb.Connection, tables: TableTriple, limit: int,
) -> tuple[str, int, list[dict]]:
    info, record, _ = tables
    sql = f"""
        SELECT r.ID, r.STATUS, r.PRODUCT_ID, r.LOT_ID, r.HOLD_CODE,
               r.HOLD_DTTM
        FROM {record} r
        WHERE {_NOT_CLOSED}
          AND EXISTS (
              SELECT 1 FROM {info} i
              WHERE i.HOLD_RECORD_ID = r.ID AND i.HOLDING = 1
          )
          AND NOT EXISTS (
              SELECT 1 FROM {info} i
              WHERE i.HOLD_RECORD_ID = r.ID AND NVL(i.HOLDING, 1) = 0
          )
        ORDER BY r.ID
    """
    total, rows = _fetch(conn, sql, _CLOSED_BINDS, limit=limit)
    return '12. 未关闭但关联 info 已全部解 hold(HOLDING=1)', total, rows


def check_13_dirty_info_count(
    conn: oracledb.Connection, tables: TableTriple, limit: int,
) -> tuple[str, int, list[dict]]:
    info, _, _ = tables
    sql = f"""
        SELECT i.ID AS INFO_ID, i.HOLD_RECORD_ID, i.PRODUCT_ID, i.LOT_ID,
               i.WAFER_ID, i.HOLD_CODE, i.HOLDING, i.REMARK
        FROM {info} i
        WHERE i.HOLD_RECORD_ID = -1
        ORDER BY i.ID
    """
    total, rows = _fetch(conn, sql, limit=limit)
    return '13. hold_info 脏数据(HOLD_RECORD_ID=-1)', total, rows


CHECKS: dict[int, Callable[..., tuple[str, int, list[dict]]]] = {
    1: check_1_mes_record_no_info,
    2: check_2_record_no_circulation,
    3: check_3_circ_orphan_record,
    4: check_4_last_circ_null_but_exists,
    5: check_5_last_circ_missing,
    6: check_6_last_circ_wrong_owner,
    7: check_7_last_circ_not_latest,
    8: check_8_status_ne_last_dispose,
    9: check_9_info_points_missing_record,
    10: check_10_manual_with_info,
    11: check_11_bad_record_type,
    12: check_12_unclosed_all_released,
    13: check_13_dirty_info_count,
}


def main() -> int:
    parser = argparse.ArgumentParser(description='Hold 数据异常排查（只读）')
    parser.add_argument(
        '--test',
        action='store_true',
        help='使用 *_TEST 表',
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=20,
        help='每项样例最多条数（默认 20）',
    )
    parser.add_argument(
        '--only',
        type=str,
        default='',
        help='只跑指定检查项，逗号分隔，如 1,2,3',
    )
    args = parser.parse_args()

    if args.only.strip():
        try:
            selected = sorted({int(x.strip()) for x in args.only.split(',') if x.strip()})
        except ValueError:
            print('无效 --only，应为数字列表，如 1,2,3', file=sys.stderr)
            return 1
        unknown = [n for n in selected if n not in CHECKS]
        if unknown:
            print(f'未知检查项: {unknown}，可选 {sorted(CHECKS)}', file=sys.stderr)
            return 1
    else:
        selected = sorted(CHECKS)

    tables = _tables(args.test)
    info, record, circ = tables
    print(f'表: {record} / {info} / {circ}')
    print(f'检查项: {selected}；每项样例 limit={args.limit}')
    print('模式: 只读，不写库；record 相关检查忽略 STATUS=99')

    summary: list[tuple[str, int]] = []
    conn = oracledb.connect(user=USER, password=PWD, dsn=DSN)
    try:
        for num in selected:
            fn = CHECKS[num]
            try:
                title, total, rows = fn(conn, tables, args.limit)
            except Exception as e:
                print()
                print('=' * 72)
                print(f'{num}. 执行失败: {e}')
                summary.append((f'{num}. (失败)', -1))
                continue
            _print_section(title, total, rows)
            summary.append((title, total))
    finally:
        conn.close()

    print()
    print('=' * 72)
    print('汇总')
    hit = 0
    for title, total in summary:
        flag = '!' if total and total > 0 else ' '
        print(f'  [{flag}] {total:>8}  {title}')
        if total and total > 0:
            hit += 1
    print(f'有命中的检查项: {hit}/{len(summary)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
