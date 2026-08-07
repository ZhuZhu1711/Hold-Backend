from app import db
from app.models.testlog import FtWltTestlog
from sqlalchemy import desc
from app.utils.FtpPool import testlog_ftp_pool
from lxml import etree
import re
import os
import csv
import traceback


def get_ftp_paths(product_id: str, wafer_id: str, step: str):
    """
    根据 product_id + wafer_id + step 查询 FTP_PATH 列表（含 test_date，按日期倒序）

    Args:
        product_id: 产品 ID
        wafer_id: 晶圆 ID
        step: 工步类型，仅允许 ATE | WLT
              ATE → STEP = 'FA'
              WLT → STEP LIKE 'WLT_'
    Returns:
        (success, msg, data)
    """
    if not product_id or not isinstance(product_id, str):
        return False, "product_id 无效", []
    if not wafer_id or not isinstance(wafer_id, str):
        return False, "wafer_id 无效", []
    if step not in ('ATE', 'WLT'):
        return False, "invalid step param. Must in ATE | WLT", []

    try:
        query = FtWltTestlog.query.filter(
            FtWltTestlog.PRODUCT_ID == product_id,
            FtWltTestlog.WAFER_ID == wafer_id,
        )
        if step == 'ATE':
            query = query.filter(FtWltTestlog.STEP == 'FA')
        else:
            query = query.filter(FtWltTestlog.STEP.like('WLT_'))

        records = query.order_by(desc(FtWltTestlog.TEST_DATE)).all()
        data = [
            {
                "ftp_path": r.FTP_PATH,
                "test_date": r.TEST_DATE.strftime("%Y-%m-%d") if r.TEST_DATE else None,
                "step": r.STEP
            }
            for r in records
        ]
        return True, "查询成功", data
    except Exception as e:
        print(f"Database Error: {e}")
        return False, str(e), []



def get_test_data(wafer_id: str, step: str):
    """
    根据Wafer ID 查询最新测试数据:良率+缺陷率
    
    Args:
        wafer_id (str): 晶圆 ID
        step: FA or WLTA or WLTB
    Returns:
        缺陷的test data——json字符串
    """
    if not wafer_id or not isinstance(wafer_id, str):
        return None
    if not step or not isinstance(step, str):
        return None
    
    
def get_testlog_bysite_str(wafer_id: str, step_list: list):
    """
    根据 Wafer ID 查询最新 testlog 并解析。
    对 step_list 中每个 step 分别取 TEST_DATE 最新的一条记录。

    Args:
        wafer_id: 晶圆 ID
        step_list: 工步列表，如 ['FA'] 或 ['WLTA', 'WLTB']

    Returns:
        bysite 结果列表；无数据时返回 None
    """
    if not wafer_id or not isinstance(wafer_id, str):
        return None
    if not step_list:
        return None

    try:
        results = []
        for step in step_list:
            record = (
                FtWltTestlog.query.filter(
                    FtWltTestlog.WAFER_ID == wafer_id,
                    FtWltTestlog.STEP == step,
                )
                .order_by(desc(FtWltTestlog.TEST_DATE))
                .first()
            )
            if not record:
                continue

            parsed = _download_and_parse_testlog(record.FTP_PATH)
            if parsed is not None:
                results.append(parsed)

        return results if results else None
    except Exception as e:
        print(f"Database Error: {e}")
        return e


def _download_and_parse_testlog(ftp_path: str):
    """从 FTP 下载 testlog 并按扩展名解析 bysite。"""
    ftp_conn = None
    local_path = None
    try:
        ftp_conn = testlog_ftp_pool.get_conn()
        local_dir = "./testlog_temp_dir/"
        if not os.path.exists(local_dir):
            os.makedirs(local_dir)
        local_path = f"{local_dir}{os.path.basename(ftp_path)}"
        with open(local_path, 'wb') as local_file:
            ftp_conn.retrbinary(f"RETR {ftp_path}", local_file.write)

        if local_path.lower().endswith('csv'):
            return parse_CSV(local_path)
        if local_path.lower().endswith('xml'):
            return parse_XML(local_path)
        return None
    except Exception as e:
        print(e)
        return None
    finally:
        if ftp_conn is not None:
            ftp_conn.close()


# 辅助函数
def parse_CSV(log_fpath):
    try:
        with open(log_fpath, mode='r', encoding='utf-8') as file:
            fname = os.path.basename(log_fpath)
            fname_without_ext = fname.rsplit('.', 1)[0]
            reader = csv.reader(file)
            # 去掉标题行
            _ = next(reader)
            data_list = list(reader)
            if len(data_list) <= 0:
                return
            start_dttm = '/'
            test_die = len(data_list)
            fail_die = None
            pass_die = None
            end_dttm = fname_without_ext.split('_')[-1]
            product_id = fname_without_ext.split('_')[2]
            test_program = data_list[0][5]
            wafer_id = data_list[0][6]
            lot_id = wafer_id.split('-')[0]
            equip_id = data_list[0][7]
            step = data_list[0][8]
            max_site = 0
            
            bysite = {}
            for row in data_list:
                site = int(row[1])
                max_site = max(max_site, site)
                code = int(row[2])

                code_dict = bysite.get(site, {})
                exist_num = code_dict.get(code, 0)
                code_dict[code] = exist_num + 1
                bysite[site] = code_dict
                
            data = {
                "test_die" : test_die,
                "end_dttm" : end_dttm,
                "product_id": product_id,
                "test_program": test_program,
                "wafer_id": wafer_id,
                "lot_id": lot_id,
                "equip_id": equip_id,
                "step": step,
                "bysite": bysite
            }
            return data
        
    except Exception as e:
        traceback.print_exc()
        print(f"解析文件出错: {log_fpath}, 错误: {e}")
        return None
    finally:
        os.remove(log_fpath)
        
        
def parse_XML(log_fpath):
        tree = etree.parse(log_fpath)
        ### 解析基本信息
        fname_parts = os.path.basename(log_fpath).split('_')
        for part in fname_parts:
            if part.startswith('GC'):
                product_id = part
                break
        lot_id = tree.find('.//LOT_ID').text
        wafer_id:str = tree.find('.//WAFER_ID').text
        # if not self.wafer_id.startswith(self.lot_id):
        #     self.wafer_id = self.lot_id + '-' + self.wafer_id
        step = tree.find('.//OP_NAME').text
        test_program = tree.find('.//TEST_PG').text
        equip_id = tree.find('.//EQP_ID').text
        start_dttm = tree.find('.//ST_TIME').text
        end_dttm = tree.find('.//END_TIME').text
        test_die = int(tree.findtext('.//TEST_DIE')) if tree.findtext('.//TEST_DIE') is not None else 0
        pass_die = int(tree.findtext('.//PASS_CNT')) if tree.findtext('.//PASS_CNT') is not None else 0
        fail_die = test_die - pass_die
        
        ### 解析binmap data
        binmap_node = tree.find('.//BINMAP')
        node_text: str = ""
        pattern = r'^(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)$'
        if binmap_node is not None:
            node_text = binmap_node.text
        else:
            return None
        
        bysite = {}
        for line in node_text.split('\n'):
            line_text = line.strip()
            
            match_result = re.match(pattern, line_text)
            if match_result is None:
                continue
            bin_obj = [int(num) for num in match_result.groups()]
            
            code_dict = bysite.get(bin_obj[4], {})
            exist_num = code_dict.get(bin_obj[3], 0)
            code_dict[bin_obj[3]] = exist_num + 1
            bysite[bin_obj[4]] = code_dict
            
        return {
                "test_die" : test_die,
                "end_dttm" : end_dttm,
                "test_program": test_program,
                "wafer_id": wafer_id,
                "lot_id": lot_id,
                "equip_id": equip_id,
                "step": step,
                "bysite": bysite
            }

   