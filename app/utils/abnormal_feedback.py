"""向上游提交异常反馈。字段填充后续再接，当前只发骨架。"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from app.config import abnormal_feedback_profile

logger = logging.getLogger(__name__)

SUPPLIER_NAME = 'GCZJ'
REQUEST_TIMEOUT = 15
API_KEY_HEADER = 'X-Api-Key'
_BRIEF_LIMIT = 500


def build_abnormal_feedback_body(source=None) -> dict:
    """上游 body 骨架。source 预留，当前不参与填充。"""
    return {
        'supplierName': SUPPLIER_NAME,
        'processName': '',
        'priority': '',
        'reporterName': '',
        'gcContact': '',
        'productName': '',
        'abnormals': [
            {
                'abnormalType': '',
                'lots': [
                    {
                        'lotId': '',
                        'waferIds': [],
                        'device': '',
                        'lotType': '',
                        'holdReason': '',
                        'startTime': '',
                        'endTime': '',
                        'holdStage': '',
                        'holdType': '',
                        'holdTime': '',
                        'route': '',
                        'ruleCode': '',
                    }
                ],
            }
        ],
    }


def post_abnormal_feedback(source=None, environ=None):
    """POST 骨架到上游。返回 (ok, http_status, msg, data)。"""
    profile = abnormal_feedback_profile(environ)
    url = profile['url']
    api_key = profile['api_key']
    if not url or not api_key:
        return False, 503, '未配置异常反馈上游地址或密钥', None

    body = build_abnormal_feedback_body(source)
    payload = json.dumps(body, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            'Content-Type': 'application/json; charset=utf-8',
            API_KEY_HEADER: api_key,
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            raw = response.read().decode('utf-8', errors='replace')
            status = getattr(response, 'status', None) or response.getcode()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        msg = f'上游 HTTP {exc.code}: {_brief(detail)}'
        logger.warning('异常反馈上游失败 url=%s %s', url, msg)
        return False, 502, msg, {'upstream_status': exc.code}
    except TimeoutError:
        logger.warning('异常反馈上游超时 url=%s', url)
        return False, 502, '上游连接超时', None
    except urllib.error.URLError as exc:
        msg = f'上游连接失败: {exc.reason}'
        logger.warning('异常反馈上游失败 url=%s %s', url, msg)
        return False, 502, msg, None
    except Exception as exc:
        logger.warning('异常反馈上游失败 url=%s %s', url, type(exc).__name__)
        return False, 502, f'上游请求失败: {type(exc).__name__}', None

    parsed = _parse_body(raw)
    ncmr_no = feedback_no_from_response(parsed)
    if not ncmr_no:
        msg = _upstream_reject_msg(parsed)
        logger.warning('异常反馈上游未返回单号 url=%s %s', url, msg)
        return False, 502, msg, {
            'upstream_status': status,
            'upstream_body': parsed,
        }

    logger.info('异常反馈已提交 url=%s status=%s ncmr_no=%s', url, status, ncmr_no)
    return True, 200, '已提交上游', {
        'upstream_status': status,
        'upstream_body': parsed,
        'ncmr_no': ncmr_no,
    }


def feedback_no_from_response(parsed) -> str:
    """上游 JSON code=200 且 data 为非空字符串时返回反馈单号。"""
    if not isinstance(parsed, dict) or parsed.get('code') != 200:
        return ''
    data = parsed.get('data')
    if isinstance(data, str) and data.strip():
        return data.strip()
    return ''


def _upstream_reject_msg(parsed) -> str:
    if isinstance(parsed, dict) and parsed.get('code') not in (None, 200):
        return f'上游返回 code={parsed.get("code")}: {_brief(parsed)}'
    return '上游未返回反馈单号'


def _parse_body(raw: str):
    text = (raw or '').strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return _brief(text)


def _brief(payload) -> str:
    if isinstance(payload, str):
        text = payload
    else:
        try:
            text = json.dumps(payload, ensure_ascii=False)
        except TypeError:
            text = str(payload)
    text = text.replace('\n', ' ')
    if len(text) > _BRIEF_LIMIT:
        return text[:_BRIEF_LIMIT] + '...'
    return text
