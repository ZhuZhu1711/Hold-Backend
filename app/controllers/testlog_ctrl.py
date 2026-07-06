from app import db
from app.models.testlog import FtWltTestlog
from sqlalchemy import desc
from app.utils.FtpPool import testlog_ftp_pool
from lxml import etree
import re
import os
import csv
import traceback


def get_ftp_path_by_wafer(wafer_id: str, step: str):
    """
    根据 Wafer ID 和 工步 查询 FTP 路径
    
    Args:
        wafer_id (str): 晶圆 ID
        step: 工步
    Returns:
        路径列表
    """
    if not wafer_id or not isinstance(wafer_id, str):
        return None

    try:
        records = FtWltTestlog.query.filter_by(WAFER_ID=wafer_id).all()
        return records
    except Exception as e:
        print(f"Database Error: {e}")
        return None
    
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
    根据 Wafer ID 查询最新testlog并解析
    
    Args:
        wafer_id (str): 晶圆 ID
        step: FA or WLTA or WLTB
        
    Returns:
        缺陷的Bysite——json字符串
    """
    if not wafer_id or not isinstance(wafer_id, str):
        return None

    try:
        record = FtWltTestlog.query.filter(FtWltTestlog.WAFER_ID==wafer_id, FtWltTestlog.STEP.in_(step_list)) \
        .order_by(desc(FtWltTestlog.TEST_DATE)).first()
        
        if record:
            # 1.下载文件
            try:
                ftp_path = record.FTP_PATH
                ftp_conn = testlog_ftp_pool.get_conn()
                local_dir = "./testlog_temp_dir/"
                if os.path.exists(local_dir) is False:
                    os.makedirs(local_dir)
                local_path = f"{local_dir}{os.path.basename(ftp_path)}"
                with open(local_path, 'wb') as local_file:
                    ftp_conn.retrbinary(f"RETR {ftp_path}", local_file.write)
            except Exception as e:
                print(e)
            finally:
                ftp_conn.close()
            # 2.解析数据
            if local_path.lower().endswith('csv'):
                return parse_CSV(local_path)
            elif local_path.lower().endswith('xml'):
                return parse_XML(local_path)
    except Exception as e:
        print(f"Database Error: {e}")
        return e


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
        
        
def parse_XML(self, log_fpath):
        tree = etree.parse(log_fpath)
        ### 解析基本信息
        fname_parts = os.path.basename(log_fpath).split('_')
        for part in fname_parts:
            if part.startswith('GC'):
                self.product_id = part
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
        fail_die = self.test_die - self.pass_die
        
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

   