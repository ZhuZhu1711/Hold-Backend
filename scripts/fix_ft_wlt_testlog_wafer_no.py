#!/usr/bin/env python
"""修复 FT_WLT_TESTLOG.WAFER_ID 中 '-' 后 wafer no 丢失前导 0 的历史数据。

规则：仅处理 2026-07-01 及之后；后缀为纯数字且长度=1 时补成 2 位（如 LOT-6 → LOT-06）。
合批片号（后缀位数 > 2）及已是两位的数据不动。

用法（在 Hold-Backend 根目录）:
  python scripts/fix_ft_wlt_testlog_wafer_no.py           # 实际更新
  python scripts/fix_ft_wlt_testlog_wafer_no.py --dry-run # 只预览
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import oracledb

from app.utils.database_util import DSN, PWD, USER

CUTOFF = date(2026, 7, 1)
BATCH_COMMIT = 200


def normalize_wafer_id(wafer_id: str) -> str | None:
    """需要补零则返回新值，否则返回 None。"""
    text = (wafer_id or '').strip()
    if '-' not in text:
        return None
    prefix, suffix = text.rsplit('-', 1)
    if suffix.isdigit() and len(suffix) == 1:
        return f'{prefix}-{suffix.zfill(2)}'
    return None


def _print_progress(done: int, total: int, width: int = 40) -> None:
    if total <= 0:
        return
    ratio = min(done / total, 1.0)
    filled = int(width * ratio)
    bar = '#' * filled + '-' * (width - filled)
    pct = ratio * 100
    sys.stdout.write(f'\r[{bar}] {done}/{total} ({pct:5.1f}%)')
    sys.stdout.flush()
    if done >= total:
        sys.stdout.write('\n')


def main() -> int:
    parser = argparse.ArgumentParser(description='修复 FT_WLT_TESTLOG wafer no 前导 0')
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='只打印将要修改的记录，不写库',
    )
    args = parser.parse_args()

    conn = oracledb.connect(user=USER, password=PWD, dsn=DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ID, WAFER_ID
                FROM FT_WLT_TESTLOG
                WHERE TEST_DATE >= :cutoff
                  AND WAFER_ID LIKE '%-%'
                ORDER BY ID
                """,
                {'cutoff': CUTOFF},
            )
            rows = cur.fetchall()

        print(
            f'扫描范围: TEST_DATE >= {CUTOFF.isoformat()}, '
            f'含 "-" 的行数={len(rows)}, dry_run={args.dry_run}'
        )

        fixes: list[tuple] = []
        for row_id, wafer_id in rows:
            fixed = normalize_wafer_id(str(wafer_id) if wafer_id is not None else '')
            if fixed:
                fixes.append((row_id, wafer_id, fixed))

        total = len(fixes)
        print(f'需修复条数: {total}（已跳过无需改的 {len(rows) - total} 条）')
        if total == 0:
            print('无需处理。')
            return 0

        preview_n = min(20, total)
        print(f'样例（前 {preview_n} 条）:')
        for row_id, old, new in fixes[:preview_n]:
            print(f'  ID={row_id}: {old!r} -> {new!r}')

        if args.dry_run:
            print('dry-run 结束，未写库。')
            return 0

        pending = 0
        applied = 0
        with conn.cursor() as cur:
            for i, (row_id, old, new) in enumerate(fixes, start=1):
                cur.execute(
                    """
                    UPDATE FT_WLT_TESTLOG
                    SET WAFER_ID = :wafer_id
                    WHERE ID = :id
                      AND WAFER_ID = :old_wafer_id
                    """,
                    {'wafer_id': new, 'id': row_id, 'old_wafer_id': old},
                )
                applied += cur.rowcount or 0
                pending += 1
                if pending >= BATCH_COMMIT:
                    conn.commit()
                    pending = 0
                _print_progress(i, total)
            if pending:
                conn.commit()

        print(f'完成: 已更新 {applied}/{total} 条并提交。')
        return 0
    except Exception as e:
        conn.rollback()
        print(f'\n失败并已回滚: {e}', file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == '__main__':
    raise SystemExit(main())
