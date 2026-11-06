#!/usr/bin/env python
"""把已改绑、仍挂在原工程师名下的未关闭单补转给当前型号工程师。

匹配：STATUS<>99，当前负责人不是生产，且不等于该型号现在的 PRO_ENG_ID
（绑定为空则按系统用户）。已在生产节点的单不抽回。

默认连测试库，且不写库。正式库必须同时带 --release 与 --apply。
两个库的表名相同。

用法（在 Hold-Backend 根目录）:
  python scripts/reassign_rebind_holds.py
  python scripts/reassign_rebind_holds.py --apply
  python scripts/reassign_rebind_holds.py --release
  python scripts/reassign_rebind_holds.py --release --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _parse_args():
    parser = argparse.ArgumentParser(
        description='补转型号改绑后仍挂在原工程师名下的未关闭 Hold',
    )
    parser.add_argument(
        '--apply',
        action='store_true',
        help='写入流转。省略时只列出将补转的记录',
    )
    parser.add_argument(
        '--release',
        action='store_true',
        help='连接正式库。省略时连接测试库。两边表名相同',
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    # 本脚本用自己的开关选表，不跟机器上的 HOLD_ENV 走。
    os.environ['HOLD_ENV'] = 'release' if args.release else 'test'

    from app import create_app
    from app.config import Config
    from app.controllers.dispose_ctrl import (
        find_stale_engineer_holds,
        transfer_open_holds_on_rebind,
    )
    from app import db

    record_table = Config.HOLD_RECORD_TABLE
    circ_table = Config.CIRCULATION_HISTORY_TABLE
    which = '正式库' if args.release else '测试库'
    print(f'{which}: {record_table} / {circ_table}')

    app = create_app()
    with app.app_context():
        try:
            rows = find_stale_engineer_holds()
        except Exception as e:
            db.session.rollback()
            print(f'查询失败: {e}', file=sys.stderr)
            return 1

        print(f'命中 {len(rows)} 条')
        for row in rows[:30]:
            print(
                f"  id={row['record_id']} product={row['product_id']} "
                f"{row['from_owner']} -> {row['to_owner']}"
            )
        if len(rows) > 30:
            print(f'  ... 另有 {len(rows) - 30} 条未列出')

        if not args.apply:
            print('未写库。确认后加 --apply')
            return 0
        if not rows:
            return 0

        groups = {}
        for row in rows:
            key = (row['product_id'], row['from_owner'], row['to_owner'])
            groups.setdefault(key, 0)
            groups[key] += 1

        try:
            moved = 0
            for (product_id, from_owner, to_owner), _count in groups.items():
                note = (
                    f'型号工程师绑定补转：当前负责人 {from_owner} 调整为 {to_owner}'
                )
                moved += transfer_open_holds_on_rebind(
                    product_id, from_owner, to_owner, note,
                )
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f'补转失败: {e}', file=sys.stderr)
            return 1

        print(f'补转完成: {moved} 条')
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
