import oracledb
from datetime import date
import logging
import os
from logging.handlers import RotatingFileHandler

USER = "FT_OWEN"
PWD = "Mee0MvpgXU!Lcp"
DSN = "172.18.202.5:1521/jsqy"

# 1. 获取 logger 实例
logger = logging.getLogger(__name__)
# 设置日志级别，否则默认只有 WARNING 及以上才会输出
logger.setLevel(logging.INFO)

# 2. 确保日志目录存在
if not os.path.exists('logs'):
    os.makedirs('./logs')
    
handler = RotatingFileHandler(
    'logs/test_log.log', 
    maxBytes=50 * 1024 * 1024,  # 50MB
    backupCount=3, 
    encoding='utf-8'
)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)


def insert_FT_WLT_TESTLOG(wafer_id: str, equip_id: str, product_id:str, ftp_path: str, step: str, test_date: date)->int:
    connection = oracledb.connect(
        user=USER,
        password=PWD,
        dsn=DSN 
    )
    sql = """
        INSERT INTO FT_WLT_TESTLOG (WAFER_ID, EQUIP_ID, PRODUCT_ID, FTP_PATH, STEP, TEST_DATE)
        VALUES (:1, :2, :3, :4, :5, :6)
    """
    
    try:
        with connection.cursor() as cursor:
            params = [wafer_id, equip_id, product_id, ftp_path, step, test_date]
            cursor.execute(sql, params)
            
            affect_row_count = cursor.rowcount
            if affect_row_count > 0:
                connection.commit()
                return affect_row_count
    except Exception as e:
        logger.info(e)
        connection.rollback()
        return -1
    finally:
        connection.close()
        
def query_testlog_history(test_date: date):
    connection = oracledb.connect(
        user=USER,
        password=PWD,
        dsn=DSN 
    )
    sql = """
        SELECT 
            FTP_PATH
        FROM 
            FT_WLT_TESTLOG
        WHERE 
            TEST_DATE = :1
        ORDER BY CREATE_TIME DESC
    """
    
    try:
        with connection.cursor() as cursor:
            params = [test_date]
            cursor.execute(sql, params)
            return cursor.fetchall()
            
    except Exception as e:
        logger.info(e)
        return None
    finally:
        connection.close()
    
    