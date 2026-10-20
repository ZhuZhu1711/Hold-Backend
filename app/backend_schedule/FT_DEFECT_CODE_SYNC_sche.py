"""
独立进程：把 RPTDB 缺陷配置全量同步到 FT_OWEN.DEFECT_CODE，跑完一轮即退出。

由管理页 root 手动触发，Web 启动时不再挂每日调度。
RPTDB 性能弱：每一轮只发一条查询，放大拉取批量后立刻断开，
不再按型号对源库发请求。后续写入只打 FT_OWEN。

RPTDB 版本低于 12.1，本进程使用 python-oracledb thick 模式。
不要在 Web 进程里调用 init_oracle_client。

启动方式：
  python app/backend_schedule/FT_DEFECT_CODE_SYNC_sche.py --mode release
debug 模式直接退出，不写正式表 DEFECT_CODE。
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler

import oracledb

current_file_path = os.path.abspath(__file__)
project_root_path = os.path.dirname(os.path.dirname(os.path.dirname(current_file_path)))
if project_root_path not in sys.path:
    sys.path.insert(0, project_root_path)

from app.backend_schedule.defect_code_sync import (  # noqa: E402
    ExistingDefect,
    SourceRow,
    group_source,
    run_product_sync,
)
from app.config import Config  # noqa: E402

logger = logging.getLogger('defect_code_sync')
logger.setLevel(logging.INFO)
if not os.path.exists('logs'):
    os.makedirs('./logs')
_fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
_fh = RotatingFileHandler(
    'logs/defect_code_sync.log',
    maxBytes=50 * 1024 * 1024,
    backupCount=3,
    encoding='utf-8',
)
_fh.setFormatter(_fmt)
logger.addHandler(_fh)
_ch = logging.StreamHandler()
_ch.setFormatter(_fmt)
logger.addHandler(_ch)

# 默认 arraysize=100 时，约 3 万行会变成几百次往返。放到一批取回。
_SOURCE_ARRAYSIZE = 50000

_SOURCE_SQL = """
    SELECT CONFIG_CLASSIFY, CONFIG_KEY1, CONFIG_KEY2, CONFIG_KEY3,
           RESULT_VALUE1, UPDATE_TIME
    FROM CACHE.RPT_W_FT_PROD_CONFIG
    WHERE CONFIG_CLASSIFY IN (:ft, :wlt)
"""

_LOG_SQL = """
    INSERT INTO DEFECT_CODE_SYNC_LOG (
        RUN_AT, PRODUCT_CODE, PROD_LINE, PRODUCT_INFO_ID, STATUS,
        SRC_ROWS, INSERTED, UPDATED, DELETED, CONFLICTS, MESSAGE,
        STARTED_AT, FINISHED_AT
    ) VALUES (
        :run_at, :product_code, :prod_line, :product_info_id, :status,
        :src_rows, :inserted, :updated, :deleted, :conflicts, :message,
        :started_at, :finished_at
    )
"""


def init_thick_client(lib_dir: str) -> None:
    if not oracledb.is_thin_mode():
        return
    lib_dir = (lib_dir or '').strip()
    if not lib_dir or not os.path.isdir(lib_dir):
        raise RuntimeError(f'Instant Client 目录不存在: {lib_dir or "(空)"}')
    oracledb.init_oracle_client(lib_dir=lib_dir)


def _line_of(classify: str) -> str:
    if classify == Config.DEFECT_SYNC_FT_CLASSIFY:
        return 'FT'
    if classify == Config.DEFECT_SYNC_WLT_CLASSIFY:
        return 'WLT'
    return ''


def fetch_source_rows(conn) -> list[SourceRow]:
    """对 RPTDB 只执行这一条语句，并一次取回结果。"""
    cursor = conn.cursor()
    cursor.arraysize = _SOURCE_ARRAYSIZE
    cursor.prefetchrows = _SOURCE_ARRAYSIZE
    cursor.execute(_SOURCE_SQL, {
        'ft': Config.DEFECT_SYNC_FT_CLASSIFY,
        'wlt': Config.DEFECT_SYNC_WLT_CLASSIFY,
    })
    rows = []
    for classify, product_code, grade, code, name, update_time in cursor.fetchall():
        rows.append(SourceRow(
            classify='' if classify is None else str(classify),
            product_code='' if product_code is None else str(product_code),
            grade='' if grade is None else str(grade),
            code=code,
            name='' if name is None else str(name),
            update_time=update_time if isinstance(update_time, datetime) else None,
        ))
    return rows


def find_product(conn, product_code: str):
    cursor = conn.cursor()
    cursor.execute(
        'SELECT ID FROM PRODUCT_INFO WHERE PRODUCT_ID = :code',
        {'code': product_code},
    )
    row = cursor.fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def load_defects(conn, product_info_id: int) -> list[ExistingDefect]:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT ID, CODE, GRADE, NAME, BSL
        FROM DEFECT_CODE
        WHERE PRODUCT_ID = :pid
        """,
        {'pid': product_info_id},
    )
    found = []
    for row_id, code, grade, name, bsl in cursor:
        found.append(ExistingDefect(
            id=int(row_id),
            code=int(code),
            grade='' if grade is None else str(grade).strip(),
            name='' if name is None else str(name).strip(),
            bsl=None if bsl is None else float(bsl),
        ))
    return found


def apply_changes(conn, product_info_id: int, plan) -> None:
    cursor = conn.cursor()
    try:
        if plan.updates:
            cursor.executemany(
                """
                UPDATE DEFECT_CODE
                SET GRADE = :grade, NAME = :name
                WHERE ID = :id
                """,
                [
                    {'id': row_id, 'grade': grade, 'name': name}
                    for row_id, grade, name in plan.updates
                ],
            )
        if plan.delete_ids:
            cursor.executemany(
                'DELETE FROM DEFECT_CODE WHERE ID = :id',
                [{'id': row_id} for row_id in plan.delete_ids],
            )
        if plan.inserts:
            cursor.executemany(
                """
                INSERT INTO DEFECT_CODE (PRODUCT_ID, GRADE, CODE, NAME)
                VALUES (:pid, :grade, :code, :name)
                """,
                [
                    {
                        'pid': product_info_id,
                        'grade': item.grade,
                        'code': item.code,
                        'name': item.name,
                    }
                    for item in plan.inserts
                ],
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def insert_log(conn, record: dict) -> None:
    cursor = conn.cursor()
    cursor.execute(_LOG_SQL, record)
    conn.commit()


def _connect_owen():
    from app.utils.database_util import DSN, PWD, USER
    return oracledb.connect(user=USER, password=PWD, dsn=DSN)


def _connect_rpt():
    return oracledb.connect(
        user=Config.RPTDB_USER,
        password=Config.RPTDB_PASSWORD,
        dsn=Config.RPTDB_DSN,
    )


def _read_source_once() -> list[SourceRow]:
    rpt = _connect_rpt()
    try:
        return fetch_source_rows(rpt)
    finally:
        rpt.close()


def run_once() -> dict:
    source_rows = _read_source_once()
    batches, ignored = group_source(
        source_rows,
        set(Config.DEFECT_SYNC_IGNORE),
        _line_of,
    )
    run_at = datetime.now()
    logger.info(
        '源库已断开。读到源配置 %s 行，型号 %s 个，忽略 WLT/GC3003-3.5 %s 行',
        len(source_rows), len(batches), ignored,
    )
    owen = _connect_owen()
    try:
        run_product_sync(
            batches,
            ignored,
            find_product=lambda code: find_product(owen, code),
            load_defects=lambda product_id: load_defects(owen, product_id),
            apply_changes=lambda product_id, plan: apply_changes(owen, product_id, plan),
            save_log=lambda record: insert_log(owen, record),
            run_at=run_at,
        )
        return {'models': len(batches), 'ignored': ignored, 'source_rows': len(source_rows)}
    finally:
        owen.close()


def run_job():
    try:
        logger.info('>>> 缺陷代码同步开始')
        stats = run_once()
        logger.info('<<< 缺陷代码同步结束 %s', stats)
    except Exception as exc:
        logger.error('缺陷代码同步整轮失败: %s', exc, exc_info=True)
        try:
            from app.utils.mail_alert import notify_severe_error
            notify_severe_error('缺陷代码同步整轮失败', str(exc), exc=exc)
        except Exception:
            logger.error('发送同步失败告警时出错', exc_info=True)


def main():
    parser = argparse.ArgumentParser(description='缺陷代码全量同步（跑完一轮即退出）')
    parser.add_argument(
        '--mode',
        choices=['debug', 'release'],
        default='debug',
        help='debug 不写库；release 立即同步一轮后退出。',
    )
    args = parser.parse_args()
    if args.mode != 'release':
        logger.info('debug 模式不执行缺陷代码同步，避免写入正式表 DEFECT_CODE')
        return

    try:
        init_thick_client(getattr(Config, 'ORACLE_CLIENT_LIB_DIR', ''))
    except Exception as exc:
        logger.error('无法加载 Instant Client，同步进程退出: %s', exc)
        raise SystemExit(1) from exc

    logger.info('缺陷代码同步开始（手动触发，跑完即退出）')
    run_job()


if __name__ == '__main__':
    main()
