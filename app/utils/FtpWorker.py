import os
import re
import time
import threading
from queue import Queue
from datetime import date, timedelta
from ftplib import FTP
from app.utils.datetime_util import format_ftp_date


MAX_FTP_IN_POOL = 5 

def get_ftp():
    host = "172.18.200.250"
    username = "share"
    password = "abc@123"
    ftp = FTP(
        host=host,
        user=username,
        passwd=password,
        timeout=3600,
        encoding='utf-8'
    )
    return ftp

class LogFile:
    def __init__(self, ftp_resp_line):
        self._valid = False
        pattern = r'^([d-])([rwx-]{9})\s+(\d+)\s+(\w+)\s+(\w+)\s+(\d+)\s+(\w{3}\s+\d{1,2}\s+[\d:]+)\s+(.+)$'
        match = re.match(pattern, ftp_resp_line)
        self.file_size = int(match.group(6))//1024
        self.update_dttm = format_ftp_date(match.group(7))
        self.date_folder = match.group(7)
        self.fname = match.group(8)
        
        suffix = self.fname.rsplit('.', 1)[-1].lower()          # 从右边开始截取一个后缀
        segs = self.fname.split('_')
        
        if 'xml' in suffix:
            self.equip_id = segs[1]
            self.product_id = segs[2]
            self.wafer_id = segs[3] + '-' + segs[4].zfill(2)
            self.step = segs[5]
            self._valid = True
        elif 'csv' in suffix:
            self.equip_id = segs[0]
            self.wafer_id = segs[1]
            self.product_id = segs[2]
            self.step = segs[3]
            self._valid = True
        else:
            pass
        
    @property
    def valid(self):
        return self._valid and self.file_size > 0

class FtpListWorker(threading.Thread):
    _ftp_pool: Queue = None
    _dir = None
    
    def __init__(self, remot_path_prefix: str, start_date: date, end_date: date, special: str, result_queue: Queue):
        super().__init__()
        self._remote_path_prefix = remot_path_prefix
        self._start_date = start_date
        self._end_date = end_date
        self._special = special
        self._result_queue = result_queue
        
        # 初始化 FTP 连接池
        self._ftp_pool = Queue(maxsize=MAX_FTP_IN_POOL)
        
    def get_target_files(self, directory: str = "."):
        filtered_files = []
        
        def callback(line: str):
            """dir的回调函数，处理每一行输出"""
            # 从行中提取文件名
            filename = self._extract_filename(line)
            if filename and filename not in ['.', '..']:
                if self._special and self._special not in filename:
                    return
                filtered_files.append(filename)
        
        # 从池中获取一个 FTP 连接
        ftp = self._ftp_pool.get()
        try:
            # 获取目录列表
            ftp.dir(directory, callback)
            os.system(f'title FT Backend - LISTing {directory}')
        finally:
            # 使用完后归还连接
            os.system(f'title FT Backend')
            self._ftp_pool.put(ftp)
            
        return filtered_files
    
    def _extract_filename(self, dir_line: str):
        """从dir输出行中提取文件名"""
        parts = dir_line.split()
        if len(parts) >= 9:
            # 文件名可能是最后一部分，也可能是空格分隔的多部分
            filename = ' '.join(parts[8:])
            return filename
        return None   
    
    def run(self):
        # 计算天数差
        delta_days = (self._end_date - self._start_date).days
        days_count = delta_days + 1
        
        # 清空并初始化 FTP 池
        while not self._ftp_pool.empty():
            try:
                self._ftp_pool.get_nowait()
            except:
                break
                
        for _ in range(MAX_FTP_IN_POOL):
            self._ftp_pool.put(get_ftp())
            
        for i in range(days_count):
            c_date = self._start_date + timedelta(days=i)
            f_str_date = c_date.strftime("Data[%Y-%m-%d]").replace('-0', '-')
            date_log_path = os.path.join(self._remote_path_prefix, f_str_date)
            
            files = self.get_target_files(date_log_path)
            d = {
                c_date:files
            }
            # 这里可以添加处理 files 的逻辑，或者发送信号（如果是 GUI 程序）
            self._result_queue.put(d)

            time.sleep(0.01)
        
        time.sleep(0.5)
        for _ in range(MAX_FTP_IN_POOL):
            try:
                ftp = self._ftp_pool.get(timeout=1)
                ftp.close()
            except:
                pass

# 示例调用
if __name__ == "__main__":
    # 创建并启动任务
    start = date(2025, 10, 1)
    end = date(2025, 10, 2)
    result_queue = Queue()
    worker = FtpListWorker("/FT_TESTLOG/", start, end, "FA", result_queue)
    worker.start()
    worker.join()