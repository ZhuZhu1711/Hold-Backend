"""
独立进程：FTP /RAW_DATA/ CSV → Oracle TEST_WAFER / TEST_BINCODE。

不挂 Web 主进程。启动方式：
  python app/backend_schedule/FT_RAW_DATA_sche.py --mode debug    # 跑一轮后退出
  python app/backend_schedule/FT_RAW_DATA_sche.py --mode release  # 常驻，默认每 60 分钟
"""
import sys
import os
import argparse
import csv
import json
import threading
import time
import logging
from datetime import datetime
from ftplib import FTP
from logging.handlers import RotatingFileHandler

import oracledb
import schedule

current_file_path = os.path.abspath(__file__)
project_root_path = os.path.dirname(os.path.dirname(os.path.dirname(current_file_path)))
if project_root_path not in sys.path:
    sys.path.insert(0, project_root_path)

from app.config import Config
from app.utils.database_util import DSN, PWD, USER
from app.utils.mail_alert import install_severe_error_hooks, notify_severe_error
from app.utils.rawdata_parse import (
    TEST_BINCODE,
    TEST_WAFER,
    get_file_type,
    parse_csv_lines,
)

TRANSFER_COMPLETE = '226'

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not os.path.exists('logs'):
    os.makedirs('./logs')
file_handler = RotatingFileHandler(
    'logs/raw_data.log',
    maxBytes=50 * 1024 * 1024,
    backupCount=3,
    encoding='utf-8',
)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)


def ftp_join(*parts: str) -> str:
    """FTP 远程路径一律正斜杠，避免 Windows os.path.join 变成反斜杠。"""
    segs = []
    for part in parts:
        if part is None:
            continue
        cleaned = str(part).replace('\\', '/').strip('/')
        if cleaned:
            segs.append(cleaned)
    return '/' + '/'.join(segs)


def is_unique_constraint_error(exc: Exception) -> bool:
    if exc is None:
        return False

    text = str(exc).lower()
    code = getattr(exc, 'code', None)
    code_text = str(code).lower() if code is not None else ''

    if 'not null' in text or 'cannot insert null' in text:
        return False

    unique_markers = [
        'unique constraint',
        'unique key',
        'ora-00001',
        'ora-1',
        'duplicate key',
        'duplicate entry',
        'violated',
    ]
    if any(marker in text for marker in unique_markers):
        return True

    return code_text in {'1', '00001', 'ora-00001', 'ora-1', '23000'}


def get_csv_fpath(ftp: FTP, remote_dir: str):
    dir_list = []
    csv_files = []
    try:
        ftp.dir(remote_dir, dir_list.append)
    except Exception:
        return []
    for line in dir_list:
        parts = line.split(' ')
        if parts and '.csv' in parts[-1].lower():
            csv_files.append(parts[-1].strip())
    return csv_files


def download_csv(ftp: FTP, remote_fname: str, remote_dir: str, local_dir: str) -> bool:
    local_path = os.path.join(local_dir, remote_fname)
    remote_fpath = ftp_join(remote_dir, remote_fname)
    try:
        with open(local_path, 'wb') as f:
            r_code = ftp.retrbinary(f"RETR {remote_fpath}", f.write)
        return TRANSFER_COMPLETE in str(r_code)
    except Exception as e:
        logger.warning("下载失败，重试一次: %s", e)
        time.sleep(10)
        try:
            with open(local_path, 'wb') as f:
                r_code = ftp.retrbinary(f"RETR {remote_fpath}", f.write)
            return TRANSFER_COMPLETE in str(r_code)
        except Exception:
            return False


def backup_csv(ftp: FTP, remote_fname: str, ftype: str, remote_dir: str, bak_dir: str):
    try:
        src_path = ftp_join(remote_dir, remote_fname)
        dst_path = ftp_join(bak_dir, ftype, remote_fname)
        ftp.rename(src_path, dst_path)
    except Exception as e:
        logger.warning("备份失败 %s -> %s: %s", remote_fname, ftype, e)


def read_csv(path: str):
    try:
        data = []
        with open(path, 'r', encoding='utf-8') as file:
            reader = csv.reader(file, delimiter=',')
            next(reader, None)
            for row in reader:
                data.append([cell.strip() for cell in row])
        return data
    except Exception:
        try:
            data = []
            with open(path, 'r', encoding='gbk') as file:
                reader = csv.reader(file, delimiter=',')
                next(reader, None)
                for row in reader:
                    data.append([cell.strip() for cell in row])
            return data
        except Exception as e:
            logger.error("读取文件失败: %s", e)
            return []


def insert_test_wafer_data(cursor, wafer_data: TEST_WAFER) -> int:
    sql = """
        INSERT INTO TEST_WAFER
        (WAFER_ID, OPERATION_ID, FT_TIME, PRODUCT_ID, SECOND_CODE, TEST_PROGRAM, RCV_TIME,
         LOT_ID, WAFER_NO, LOCATION, GROSS_DIE, WAFER_NUM, ROUTE, EQUIP_ID, ASS_VENDER, PACK_LOTID,
         PASS_DIE, NG_NUM, CP, GRADES_QTY, RECORD_DTTM)
        VALUES
        (:WAFER_ID, :OPERATION_ID, :FT_TIME, :PRODUCT_ID, :SECOND_CODE, :TEST_PROGRAM, :RCV_TIME,
         :LOT_ID, :WAFER_NO, :LOCATION, :GROSS_DIE, :WAFER_NUM, :ROUTE, :EQUIP_ID, :ASS_VENDER, :PACK_LOTID,
         :PASS_DIE, :NG_NUM, :CP, :GRADES_QTY, :RECORD_DTTM)
        RETURNING ID INTO :new_id
    """
    new_id_var = cursor.var(oracledb.DB_TYPE_NUMBER)
    params = {
        "WAFER_ID": wafer_data.wafer_id,
        "OPERATION_ID": wafer_data.operation_id,
        "FT_TIME": wafer_data.ft_time,
        "PRODUCT_ID": wafer_data.product_id,
        "SECOND_CODE": wafer_data.second_code,
        "TEST_PROGRAM": wafer_data.test_program,
        "RCV_TIME": wafer_data.rcv_time,
        "LOT_ID": wafer_data.lot_id,
        "WAFER_NO": wafer_data.wafer_no,
        "LOCATION": wafer_data.location,
        "GROSS_DIE": wafer_data.gross_die,
        "WAFER_NUM": wafer_data.wafer_num,
        "ROUTE": wafer_data.route,
        "EQUIP_ID": wafer_data.equip_id,
        "ASS_VENDER": wafer_data.ass_vender,
        "PACK_LOTID": wafer_data.pack_lotid,
        "PASS_DIE": wafer_data.pass_die,
        "NG_NUM": wafer_data.ng_num,
        "CP": wafer_data.cp,
        "GRADES_QTY": json.dumps(wafer_data.grades_qty),
        "RECORD_DTTM": wafer_data.record_time or datetime.now(),
        "new_id": new_id_var,
    }
    cursor.execute(sql, params)
    new_id = new_id_var.getvalue()[0]
    return int(new_id) if new_id >= 0 else -1


def insert_bincode(cursor, bincode: TEST_BINCODE) -> bool:
    sql = """
    INSERT INTO TEST_BINCODE
    (WAFER_ID, BIN_CODE, BIN_CODE_QTY, TEST_WAFER_SEQ)
    VALUES (:WAFER_ID, :BIN_CODE, :BIN_CODE_QTY, :TEST_WAFER_SEQ)
    """
    params = {
        "WAFER_ID": bincode.wafer_id,
        "BIN_CODE": bincode.bin_code,
        "BIN_CODE_QTY": bincode.bin_code_qty,
        "TEST_WAFER_SEQ": bincode.test_wafer_seq,
    }
    cursor.execute(sql, params)
    return cursor.rowcount > 0


def dump_test_wafers(test_wafers: dict[str, TEST_WAFER]) -> bool:
    try:
        with oracledb.connect(user=USER, password=PWD, dsn=DSN) as conn:
            no_error = True
            for wid, wafer in test_wafers.items():
                try:
                    with conn.cursor() as cur:
                        try:
                            seq = insert_test_wafer_data(cur, wafer)
                        except Exception as e:
                            if is_unique_constraint_error(e):
                                logger.info(
                                    "跳过重复 wafer %s: %s",
                                    getattr(wafer, 'wafer_id', wid),
                                    e,
                                )
                                continue
                            raise

                        if seq == -1:
                            conn.rollback()
                            no_error = False
                            continue

                        for bc in wafer.bincodes.values():
                            bc.test_wafer_seq = seq
                            try:
                                if not insert_bincode(cur, bc):
                                    conn.rollback()
                                    no_error = False
                                    break
                            except Exception as e:
                                if is_unique_constraint_error(e):
                                    logger.info(
                                        "跳过重复 bin code %s for wafer %s: %s",
                                        bc.bin_code,
                                        wafer.wafer_id,
                                        e,
                                    )
                                    continue
                                raise
                        else:
                            conn.commit()
                            continue

                        conn.rollback()
                        no_error = False
                except Exception as e:
                    conn.rollback()
                    logger.error("wafer 处理失败: %s", e)
                    no_error = False
            return no_error
    except Exception as e:
        logger.error("数据库异常: %s", e)
        return False


class RawDataScheduler(threading.Thread):
    """FTP raw CSV → TEST_WAFER / TEST_BINCODE。独立进程运行，不随 Web 启动。"""

    def __init__(self):
        super().__init__(daemon=True)
        self._stop_event = threading.Event()
        self.logger = logger
        self.config = Config()
        self.interval_minutes = getattr(self.config, 'RAW_DATA_INTERVAL_MINUTES', 60)
        self.ftp_host = getattr(self.config, 'RAW_DATA_FTP_HOST', '172.18.107.206')
        self.ftp_user = getattr(self.config, 'RAW_DATA_FTP_USER', 'ft')
        self.ftp_passwd = getattr(self.config, 'RAW_DATA_FTP_PASSWD', '')
        self.ftp_timeout = getattr(self.config, 'RAW_DATA_FTP_TIMEOUT', 60)
        self.remote_dir = getattr(self.config, 'RAW_DATA_REMOTE_DIR', '/RAW_DATA/')
        self.bak_dir = getattr(self.config, 'RAW_DATA_BAK_DIR', '/RAW_DATA_BAK')
        self.local_dir = getattr(self.config, 'RAW_DATA_LOCAL_DIR', 'RAW_DATA')

    def stop(self):
        self.logger.info("正在停止 Raw Data 调度器...")
        self._stop_event.set()

    def _connect_ftp(self) -> FTP:
        ftp = FTP(
            host=self.ftp_host,
            user=self.ftp_user,
            passwd=self.ftp_passwd,
            timeout=self.ftp_timeout,
        )
        if ftp.sock is not None:
            ftp.sock.settimeout(self.ftp_timeout)
        return ftp

    def _run_job(self):
        ftp = None
        try:
            self.logger.info(">>> Raw Data 定时任务开始执行...")
            os.makedirs(self.local_dir, exist_ok=True)

            try:
                ftp = self._connect_ftp()
                self.logger.info("FTP 连接成功 %s", self.ftp_host)
            except Exception as e:
                self.logger.error("FTP 连接失败: %s", e)
                return

            csv_files = get_csv_fpath(ftp, self.remote_dir)
            self.logger.info("找到 %s 个 CSV 文件", len(csv_files))
            if not csv_files:
                return

            for fname in csv_files:
                self.logger.info("处理: %s", fname)
                if not download_csv(ftp, fname, self.remote_dir, self.local_dir):
                    self.logger.error("%s 下载失败", fname)
                    continue

                ftype = get_file_type(fname)
                csv_path = os.path.join(self.local_dir, fname)
                lines = read_csv(csv_path)
                wafers = parse_csv_lines(ftype, lines)
                ok = dump_test_wafers(wafers)

                if ok:
                    self.logger.info("%s 入库成功", fname)
                    backup_csv(ftp, fname, ftype, self.remote_dir, self.bak_dir)
                else:
                    self.logger.error("%s 入库失败", fname)
        except Exception as e:
            self.logger.error("Raw Data 定时任务执行出错: %s", e, exc_info=True)
            notify_severe_error('Raw Data 定时任务整轮失败', str(e), exc=e)
        finally:
            if ftp:
                try:
                    ftp.quit()
                except Exception:
                    pass
            self.logger.info("<<< Raw Data 定时任务执行完毕")

    def run(self):
        self.logger.info(
            "Raw Data 调度器已启动（独立进程），间隔 %s 分钟，FTP=%s %s",
            self.interval_minutes,
            self.ftp_host,
            self.remote_dir,
        )
        self._run_job()
        if self._stop_event.is_set():
            self.logger.info("Raw Data 调度器线程已退出")
            return

        sch = schedule.Scheduler()
        sch.every(self.interval_minutes).minutes.do(self._run_job)

        while not self._stop_event.is_set():
            sch.run_pending()
            time.sleep(1)

        self.logger.info("Raw Data 调度器线程已退出")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Raw Data FTP→Oracle 定时任务。独立进程，不挂 Web。默认 debug。'
    )
    parser.add_argument(
        '--mode',
        choices=['debug', 'release'],
        default='debug',
        help='debug：立即执行一次后退出；release：按配置间隔循环调度。',
    )
    args = parser.parse_args()

    print(f"运行模式: {args.mode}")
    install_severe_error_hooks()
    scheduler = RawDataScheduler()

    if args.mode == 'debug':
        scheduler._run_job()
    else:
        scheduler.start()
        try:
            while scheduler.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            scheduler.stop()
            scheduler.join(timeout=5)
