"""预处置人：lot → OA 单号 → 工号 → 姓名。HTTP 全部 mock。"""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch
from urllib.error import URLError

from app.utils.pre_handler_lookup import (
    extract_document_number,
    extract_user_name,
    extract_work_code,
    format_pre_handler,
    lookup_lot_oa,
    lookup_pre_handler,
    work_code_for_query,
)


class _Body:
    def __init__(self, payload):
        self._raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FormatPreHandlerTest(unittest.TestCase):
    def test_name_and_short_code(self):
        self.assertEqual(format_pre_handler('周星辰', '4655'), '周星辰（A0004655）')

    def test_already_prefixed_code(self):
        self.assertEqual(format_pre_handler('周星辰', 'A0004655'), '周星辰（A0004655）')
        self.assertEqual(format_pre_handler('张三', '不是工号'), '张三')

    def test_query_code_strips_prefix(self):
        self.assertEqual(work_code_for_query('A0004655'), '4655')
        self.assertEqual(work_code_for_query('4655'), '4655')


LOT_ORDER = {
    'code': 0,
    'message': 'ok',
    'request_id': '51167440-ba96-49c9-a1a1-7d8020b8a668',
    'elapsed_ms': 10,
    'category': ['ft'],
    'operation': 'get_lot_order',
    'data': {
        'OA': 'ET.202610218',
        'WO': 'ZJSH05B261027036',
    },
}
ENGINEERING = {
    'code': 0,
    'message': 'ok',
    'request_id': '399d2038-9776-438e-a859-da3fa7d9a085',
    'elapsed_ms': 323,
    'category': ['common'],
    'operation': 'engineering_query',
    'data': {
        'applicant': None,
        'applicant_id': 'A0004655',
    },
}
USER_NAME = {
    'code': 0,
    'message': 'ok',
    'request_id': '28802525-ad65-413a-be3b-1289df8fa640',
    'elapsed_ms': 48,
    'category': ['common'],
    'operation': 'get_user_name',
    'data': {
        'SHOW_NAME': '周星辰',
    },
}


class ExtractFieldTest(unittest.TestCase):
    def test_live_response_fields(self):
        self.assertEqual(extract_document_number(LOT_ORDER), 'ET.202610218')
        self.assertEqual(extract_work_code(ENGINEERING), 'A0004655')
        self.assertEqual(extract_user_name(USER_NAME), '周星辰')

    def test_nonzero_code_is_ignored(self):
        failed = {'code': 1, 'message': 'fail', 'data': {'OA': 'ET.202610218'}}
        self.assertEqual(extract_document_number(failed), '')


class LookupChainTest(unittest.TestCase):
    def _urlopen(self, responses):
        calls = []

        def fake_urlopen(request, timeout=None):
            body = json.loads(request.data.decode('utf-8'))
            path = request.full_url.split('6969', 1)[-1]
            calls.append((path, body, timeout))
            if path not in responses:
                raise AssertionError(path)
            return _Body(responses[path])

        return calls, fake_urlopen

    def test_three_steps_fill_display_name(self):
        responses = {
            '/api/v1/query': LOT_ORDER,
            '/api/v1/jdy/query': ENGINEERING,
            '/api/v1/bpm/query': USER_NAME,
        }
        calls, fake_urlopen = self._urlopen(responses)
        with patch('app.utils.pre_handler_lookup.urllib.request.urlopen', fake_urlopen):
            text = lookup_pre_handler('C147238-01', {})
        self.assertEqual(text, '周星辰（A0004655）')
        self.assertEqual([item[0] for item in calls], [
            '/api/v1/query',
            '/api/v1/jdy/query',
            '/api/v1/bpm/query',
        ])
        self.assertEqual(calls[0][1], {
            'category': ['ft'],
            'operation': 'get_lot_order',
            'params': {'lot_id': 'C147238-01'},
        })
        self.assertEqual(calls[1][1]['category'], ['common'])
        self.assertEqual(calls[2][1]['category'], ['common'])
        self.assertEqual(calls[1][1]['params'], {
            'document_number': 'ET.202610218',
            'category': 'FT',
        })
        self.assertEqual(calls[2][1]['params'], {'work_code': '4655'})

    def test_same_lot_is_cached(self):
        responses = {
            '/api/v1/query': LOT_ORDER,
            '/api/v1/jdy/query': ENGINEERING,
            '/api/v1/bpm/query': USER_NAME,
        }
        calls, fake_urlopen = self._urlopen(responses)
        cache = {}
        with patch('app.utils.pre_handler_lookup.urllib.request.urlopen', fake_urlopen):
            first = lookup_pre_handler('C147238-01', cache)
            second = lookup_pre_handler('C147238-01', cache)
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 3)

    def test_connection_failure_returns_blank(self):
        def fake_urlopen(request, timeout=None):
            raise URLError('refused')

        with patch('app.utils.pre_handler_lookup.urllib.request.urlopen', fake_urlopen):
            self.assertEqual(lookup_pre_handler('C147238-01', {}), '')

    def test_pre_handler_reuses_oa_cache(self):
        calls = []

        def fake_urlopen(request, timeout=None):
            body = json.loads(request.data.decode('utf-8'))
            path = request.full_url.split('6969', 1)[-1]
            calls.append(path)
            if path == '/api/v1/query':
                return _Body(LOT_ORDER)
            if path == '/api/v1/jdy/query':
                return _Body(ENGINEERING)
            return _Body(USER_NAME)

        oa_cache = {}
        with patch('app.utils.pre_handler_lookup.urllib.request.urlopen', fake_urlopen):
            oa = lookup_lot_oa('C147238-01', oa_cache)
            text = lookup_pre_handler('C147238-01', {}, oa_cache=oa_cache)
        self.assertEqual(oa, 'ET.202610218')
        self.assertEqual(text, '周星辰（A0004655）')
        self.assertEqual(calls.count('/api/v1/query'), 1)

    def test_null_oa_stops_chain(self):
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append(request.full_url)
            return _Body({'code': 0, 'data': {'OA': None, 'WO': None}})

        with patch('app.utils.pre_handler_lookup.urllib.request.urlopen', fake_urlopen):
            self.assertEqual(lookup_pre_handler('C147238-01', {}), '')
        self.assertEqual(len(calls), 1)

    def test_missing_document_stops_chain(self):
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append(request.full_url)
            return _Body({'data': {}})

        with patch('app.utils.pre_handler_lookup.urllib.request.urlopen', fake_urlopen):
            self.assertEqual(lookup_pre_handler('C147238-01', {}), '')
        self.assertEqual(len(calls), 1)
