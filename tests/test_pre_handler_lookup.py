"""预处置人：lot → OA 单号 → 工号 → 姓名。HTTP 全部 mock。"""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch
from urllib.error import URLError

from app.utils.pre_handler_lookup import (
    LotOrder,
    extract_document_number,
    extract_ncmr_owner,
    extract_user_name,
    extract_work_code,
    format_pre_handler,
    lookup_lot_oa,
    lookup_lot_order,
    lookup_ncmr_owner,
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


class LookupLotOrderTest(unittest.TestCase):
    def test_reads_oa_and_wo_from_localhost(self):
        def fake_urlopen(request, timeout=None):
            self.url = request.full_url
            self.body = json.loads(request.data.decode('utf-8'))
            return _Body(LOT_ORDER)

        with patch.dict(os.environ, {'HOLD_OA_API_BASE': ''}), patch(
            'app.utils.pre_handler_lookup.urllib.request.urlopen',
            fake_urlopen,
        ):
            order = lookup_lot_order('3AL585')
        self.assertEqual(order, LotOrder(True, 'ET.202610218', 'ZJSH05B261027036', ''))
        self.assertEqual(self.url, 'http://127.0.0.1:6969/api/v1/query')
        self.assertEqual(self.body, {
            'category': ['ft'],
            'operation': 'get_lot_order',
            'params': {'lot_id': '3AL585'},
        })

    def test_null_oa_is_empty_not_failure(self):
        def fake_urlopen(request, timeout=None):
            return _Body({'code': 0, 'data': {'OA': None, 'WO': None}})

        with patch('app.utils.pre_handler_lookup.urllib.request.urlopen', fake_urlopen):
            order = lookup_lot_order('3AL585')
        self.assertTrue(order.ok)
        self.assertEqual(order.oa, '')
        self.assertEqual(order.wo, '')

    def test_nonzero_code_is_failure(self):
        def fake_urlopen(request, timeout=None):
            return _Body({
                'code': 1,
                'message': 'fail',
                'data': {'OA': 'ET.1', 'WO': 'W'},
            })

        with patch('app.utils.pre_handler_lookup.urllib.request.urlopen', fake_urlopen):
            order = lookup_lot_order('3AL585')
        self.assertFalse(order.ok)
        self.assertEqual(order.error, 'fail')

    def test_connection_failure(self):
        def fake_urlopen(request, timeout=None):
            raise URLError('refused')

        with patch('app.utils.pre_handler_lookup.urllib.request.urlopen', fake_urlopen):
            order = lookup_lot_order('3AL585')
        self.assertFalse(order.ok)
        self.assertIn('连接失败', order.error)


class LookupNcmrOwnerTest(unittest.TestCase):
    def _urlopen(self, payload):
        def fake_urlopen(request, timeout=None):
            self.url = request.full_url
            self.body = json.loads(request.data.decode('utf-8'))
            self.timeout = timeout
            return _Body(payload)

        return fake_urlopen

    def test_posts_product_to_rpt_query(self):
        payload = {
            'code': 0,
            'data': {'OWNER': '张三（A0004655）'},
        }
        with patch.dict(os.environ, {'HOLD_OA_API_BASE': ''}), patch(
            'app.utils.pre_handler_lookup.urllib.request.urlopen',
            self._urlopen(payload),
        ):
            text = lookup_ncmr_owner('GC10V0-3.5')
        self.assertEqual(text, '张三（A0004655）')
        self.assertEqual(self.url, 'http://127.0.0.1:6969/api/v1/rpt/query')
        self.assertEqual(self.body, {
            'category': ['common'],
            'operation': 'get_ncmr_owner',
            'params': {
                'line_type': 'FT',
                'product_id': 'GC10V0-3.5',
            },
        })

    def test_null_owner_is_empty(self):
        cases = (
            {'code': 0, 'data': {'OWNER': None}},
            {'code': 200, 'data': {'OWNER': None}},
            {'code': 0, 'data': None},
            {'code': 200, 'data': []},
            {'code': 0, 'data': [{'OWNER': None}, {'OWNER': ''}]},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                self.assertEqual(extract_ncmr_owner(payload), '')
                with patch(
                    'app.utils.pre_handler_lookup.urllib.request.urlopen',
                    self._urlopen(payload),
                ):
                    self.assertEqual(lookup_ncmr_owner('GC10V0-3.5'), '')

    def test_owner_from_row_list(self):
        payload = {'code': 0, 'data': [{'OWNER': None}, {'OWNER': ' 李四 '}]}
        self.assertEqual(extract_ncmr_owner(payload), '李四')

    def test_nonzero_code_is_blank(self):
        payload = {'code': 1, 'message': 'fail', 'data': {'OWNER': '张三'}}
        with patch(
            'app.utils.pre_handler_lookup.urllib.request.urlopen',
            self._urlopen(payload),
        ):
            self.assertEqual(lookup_ncmr_owner('GC10V0-3.5'), '')

    def test_connection_failure_is_blank(self):
        def fake_urlopen(request, timeout=None):
            raise URLError('refused')

        with patch('app.utils.pre_handler_lookup.urllib.request.urlopen', fake_urlopen):
            self.assertEqual(lookup_ncmr_owner('GC10V0-3.5'), '')

    def test_same_product_is_cached(self):
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append(request.full_url)
            return _Body({'code': 0, 'data': {'OWNER': '张三'}})

        cache = {}
        with patch('app.utils.pre_handler_lookup.urllib.request.urlopen', fake_urlopen):
            first = lookup_ncmr_owner('GC10V0-3.5', cache)
            second = lookup_ncmr_owner('GC10V0-3.5', cache)
        self.assertEqual(first, '张三')
        self.assertEqual(second, '张三')
        self.assertEqual(len(calls), 1)

    def test_blank_product_skips_request(self):
        def fake_urlopen(request, timeout=None):
            raise AssertionError('should not request')

        with patch('app.utils.pre_handler_lookup.urllib.request.urlopen', fake_urlopen):
            self.assertEqual(lookup_ncmr_owner('  '), '')
