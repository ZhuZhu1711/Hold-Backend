#!/usr/bin/env python
"""关闭无 hold_info 关联的残留 hold_record（MES 合批）。

判定：FT_HOLD_RECORD 上不存在任何 FT_HOLD_INFO.HOLD_RECORD_ID = record.ID，
且尚未关闭（STATUS <> 99）。

手提 Hold（SOURCE=1）创建时就不写 hold_info，一律跳过，避免误关。

关闭方式与定时自动关闭一致：插入 CIRCULATION_HISTORY(DISPOSE=99)，
回写 STATUS=99 / LAST_CIRCULATION_ID。

用法（在 Hold-Backend 根目录）:
  python scripts/close_orphan_hold_records.py --dry-run
  python scripts/close_orphan_hold_records.py
  python scripts/close_orphan_hold_records.py --test          # 测试表
  python scripts/close_orphan_hold_records.py --dry-run --limit 50
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import oracledb

from app.utils.database_util import (
    DISPOSE_CLOSE,
    DSN,
    PWD,
    USER,
    auto_close_hold_records,
)

_ALLOWED = {
    'FT_HOLD_INFO': 'FT_HOLD_RECORD',
    'FT_HOLD_INFO_TEST': 'FT_HOLD_RECORD_TEST',
}


def _tables(use_test: bool) -> tuple[str, str]:
    if use_test:
        return 'FT_HOLD_INFO_TEST', 'FT_HOLD_RECORD_TEST'
    return 'FT_HOLD_INFO', 'FT_HOLD_RECORD'


def query_orphan_record_ids(
    info_table: str,
    record_table: str,
    limit: int | None = None,
) -> list[dict]:
    """返回无 hold_info 关联、未关闭、非手提 的 hold_record 行。"""
    info_tbl = info_table.upper()
    record_tbl = record_table.upper()
    if info_tbl not in _ALLOWED or _ALLOWED[info_tbl] != record_tbl:
        raise ValueError(f'非法表组合: info={info_tbl}, record={record_tbl}')

    binds: dict = {'closed': DISPOSE_CLOSE}
    inner = f"""
        SELECT
            r.ID,
            r.PRODUCT_ID,
            r.LOT_ID,
            r.WAFER_ID,
            r.HOLD_CODE,
            r.STATION,
            r.SOURCE,
            r.STATUS,
            r.RECORD_TYPE,
            r.HOLD_DTTM
        FROM {record_tbl} r
        WHERE NVL(r.STATUS, 0) <> :closed
          AND NVL(r.SOURCE, 0) <> 1
          AND NOT EXISTS (
              SELECT 1
              FROM {info_tbl} i
              WHERE i.HOLD_RECORD_ID = r.ID
          )
        ORDER BY r.ID
    """
    if limit is not None:
        binds['lim'] = max(1, int(limit))
        sql = f"SELECT * FROM ({inner}) WHERE ROWNUM <= :lim"
    else:
        sql = inner

    conn = oracledb.connect(user=USER, password=PWD, dsn=DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, binds)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description='关闭无 hold_info 关联的残留 hold_record（排除手提 SOURCE=1）',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='只列出将关闭的记录，不写库',
    )
    parser.add_argument(
        '--test',
        action='store_true',
        help='使用 FT_HOLD_INFO_TEST / FT_HOLD_RECORD_TEST',
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=None,
        help='最多处理条数（默认不限制）',
    )
    args = parser.parse_args()

    info_table, record_table = _tables(args.test)
    print(f'表: {record_table} ← {info_table}')
    print('条件: STATUS<>99, SOURCE<>1, 无 HOLD_RECORD_ID 关联的 hold_info')

    try:
        rows = query_orphan_record_ids(
            info_table=info_table,
            record_table=record_table,
            limit=args.limit,
        )
    except Exception as e:
        print(f'查询失败: {e}', file=sys.stderr)
        return 1

    print(f'命中 {len(rows)} 条')
    if not rows:
        return 0

    preview = rows[:30]
    for r in preview:
        print(
            f"  id={r['ID']} status={r['STATUS']} type={r['RECORD_TYPE']} "
            f"product={r['PRODUCT_ID']} lot={r['LOT_ID']} "
            f"wafer={r['WAFER_ID']} code={r['HOLD_CODE']} station={r['STATION']}"
        )
    if len(rows) > len(preview):
        print(f'  ... 另有 {len(rows) - len(preview)} 条未列出')

    if args.dry_run:
        print('dry-run：未写库')
        return 0

    ids = [int(r['ID']) for r in rows]
    ok, fail = auto_close_hold_records(
        ids,
        record_table=record_table,
        actor_user_id=1,
        dispose_detail='无关联hold_info，清理残留record并关闭',
    )
    print(f'关闭完成: ok={ok}, fail={fail}')
    return 0 if fail == 0 else 2


if __name__ == '__main__':
    raise SystemExit(main())
