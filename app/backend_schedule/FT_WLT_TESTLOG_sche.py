from app.utils.FtpWorker import FtpListWorker
from app.utils.database_util import insert_FT_WLT_TESTLOG, query_testlog_history
from app.utils.mail_alert import notify_severe_error
from app.config import Config
from datetime import date, timedelta
from queue import Queue
import threading
import time
import schedule
import logging
import os

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if os.path.exists('logs') is False:
    os.makedirs('./logs')
file_handler = logging.FileHandler('logs/test_log.log', encoding='utf-8')
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)


class TaskQueueConsumer(threading.Thread):
    def __init__(self, config: Config, q: Queue):
        super().__init__(daemon=True)
        self._q = q
        self._config = config
        self.logger = logger
        self._init_history()

    def _init_history(self):
        """初始化或重置历史记录"""
        today = date.today()
        yesterday = today - timedelta(days=1)
        self.history = {
            yesterday: set(), 
            today: set()
        }
        
        today_history = query_testlog_history(today)
        yesterday_history = query_testlog_history(yesterday)
        
        for td_item in today_history:
            self.history[today].add(os.path.basename(td_item[0]))
        for yd_item in yesterday_history:
            self.history[yesterday].add(os.path.basename(yd_item[0]))
        
        self.logger.info(f"历史记录已初始化/重置: {today}")

    def run(self):
        self.logger.info("Consumer start working")
        
        while True:
            try:
                today_date = date.today()
                if today_date not in self.history:
                    self.logger.info("检测到跨天，重构历史记录")
                    self._init_history()

                try:
                    task = self._q.get(timeout=1)
                except:
                    continue

                if isinstance(task, dict):
                    task_date, items = list(task.items())[0]
                else:
                    continue

                self.logger.info(f"正在处理日期 {task_date} 的 {len(items)} 个文件")

                if task_date not in self.history:
                    self.history[task_date] = set()

                for item in items:
                    if item in self.history[task_date]:
                        continue
                    
                    try:
                        self.process_item(task_date, item)
                        self.history[task_date].add(item)
                    except Exception as e:
                        self.logger.error(f"处理文件 {item} 失败: {e}")
                        # 这里可以选择是否重试，或者记录到死信队列

            except Exception as e:
                self.logger.error(f"Consumer 发生未知错误: {e}")
                notify_severe_error('Testlog Consumer 未知错误', str(e), exc=e)
                time.sleep(10)
            finally:
                time.sleep(5)

    def process_item(self, task_date: date, item: str):
        """具体的业务逻辑"""
        # 1. 通过后缀判断类型,xml or csv
        if item.lower().endswith('csv'):
            year = str(task_date.year)
            month = str(task_date.month)
            day = str(task_date.day)
            ftp_path = f"{self._config.FT_TEST_DATA_REMOTE_PATH}Data[{year}-{month}-{day}]/{item}"
            parts = item.split('_')
            insert_FT_WLT_TESTLOG(parts[1], parts[0], parts[2], ftp_path, parts[3], task_date)
            
        elif item.lower().endswith('xml'):
            year = str(task_date.year)
            month = str(task_date.month)
            day = str(task_date.day)
            ftp_path = f"{self._config.WLT_TEST_DATA_REMOTE_PATH}Data[{year}-{month}-{day}]/{item}"
            parts = item.split('_')
            wafer_id = f"{parts[3]}-{parts[4]}"
            step = 'WLT' + parts[5]
            insert_FT_WLT_TESTLOG(wafer_id, parts[1], parts[2], ftp_path, step, task_date)
        
        
class FlaskTaskScheduler(threading.Thread):
    def __init__(self):
        """
        初始化调度器线程
        """
        super().__init__(daemon=True) # 设置为守护线程，主程序退出时自动退出
        self._stop_event = threading.Event()
        self.logger = logger
        
        self.config = Config()
        
        self.result_queue = Queue()
        self.consumer = TaskQueueConsumer(self.config, self.result_queue)
        self.consumer.start()

    def stop(self):
        """停止线程的方法"""
        self.logger.info("正在停止调度器...")
        self._stop_event.set()

    def _run_job(self):
        """
        实际执行的任务逻辑
        """
        try:
            self.logger.info(">>> 定时任务开始执行...")
            
            cur_date = date.today()
            yesterday_date = cur_date - timedelta(days=1)

            ft_list_worker = FtpListWorker(self.config.FT_TEST_DATA_REMOTE_PATH, yesterday_date, cur_date, "FA", self.result_queue)
            wlt_list_worker = FtpListWorker(self.config.WLT_TEST_DATA_REMOTE_PATH, yesterday_date, cur_date, None, self.result_queue)

            ft_list_worker.start()
            wlt_list_worker.start()
            
            self.logger.info("<<< 定时任务执行完毕")
        except Exception as e:
            self.logger.error(f"定时任务执行出错: {e}", exc_info=True)
            notify_severe_error('Testlog 定时任务整轮失败', str(e), exc=e)

    def run(self):
        """线程启动后的入口"""
        self.logger.info("调度器线程已启动")
        
        # 注册任务：每 10 分钟执行一次
        schedule.every(10).minutes.do(self._run_job)
        
        # 立即执行一次（可选，用于测试）
        # self._run_job() 

        while not self._stop_event.is_set():
            schedule.run_pending()
            time.sleep(1)

        self.logger.info("调度器线程已退出")
        
