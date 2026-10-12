"""缺陷代码全量同步的纯逻辑。不连接数据库。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime


GRADE_MAX = 10
NAME_MAX = 100
CODE_ABS_MAX = 99999999999
MESSAGE_MAX = 1000

STATUS_OK = 'OK'
STATUS_SKIPPED = 'SKIPPED'
STATUS_FAILED = 'FAILED'


@dataclass
class SourceRow:
    classify: str
    product_code: str
    grade: str
    code: object
    name: str
    update_time: datetime | None = None


@dataclass
class Winner:
    code: int
    grade: str
    name: str


@dataclass
class ProductBatch:
    product_code: str
    line: str
    src_rows: int
    winners: list[Winner] = field(default_factory=list)
    conflicts: int = 0
    invalid: int = 0
    truncated: int = 0


@dataclass
class ExistingDefect:
    id: int
    code: int
    grade: str
    name: str
    bsl: float | None = None


@dataclass
class ChangePlan:
    inserts: list[Winner] = field(default_factory=list)
    updates: list[tuple[int, str, str]] = field(default_factory=list)
    delete_ids: list[int] = field(default_factory=list)


def parse_code(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, float):
        if not value.is_integer():
            return None
        number = int(value)
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            number = int(text)
        except ValueError:
            return None
    if abs(number) > CODE_ABS_MAX:
        return None
    return number


def _clip(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _time_key(value, index: int):
    if isinstance(value, datetime):
        return (1, value, index)
    return (0, datetime.min, index)


def group_source(rows, ignore_pairs, line_of) -> tuple[list[ProductBatch], int]:
    """按型号归组。ignore_pairs 是 (classify, product_code)，这些行整行丢弃。"""
    ignored = 0
    grouped: dict[str, list[SourceRow]] = {}
    for row in rows:
        product_code = (row.product_code or '').strip()
        classify = (row.classify or '').strip()
        if not product_code or not classify:
            continue
        if (classify, product_code) in ignore_pairs:
            ignored += 1
            continue
        grouped.setdefault(product_code, []).append(row)

    batches = []
    for product_code in sorted(grouped):
        items = grouped[product_code]
        lines = []
        for item in items:
            line = line_of(item.classify)
            if line and line not in lines:
                lines.append(line)
        line = 'FT' if 'FT' in lines else (lines[0] if lines else '')
        winners, conflicts, invalid, truncated = _dedupe(items)
        batches.append(ProductBatch(
            product_code=product_code,
            line=line,
            src_rows=len(items),
            winners=winners,
            conflicts=conflicts,
            invalid=invalid,
            truncated=truncated,
        ))
    return batches, ignored


def _dedupe(items: list[SourceRow]):
    best: dict[int, tuple] = {}
    conflicts = 0
    invalid = 0
    truncated = 0
    for index, item in enumerate(items):
        code = parse_code(item.code)
        grade, grade_cut = _clip(str(item.grade or '').strip(), GRADE_MAX)
        name, name_cut = _clip(str(item.name or '').strip(), NAME_MAX)
        if code is None or not grade or not name:
            invalid += 1
            continue
        if grade_cut or name_cut:
            truncated += 1
        key = _time_key(item.update_time, index)
        prev = best.get(code)
        if prev is not None:
            conflicts += 1
            if key <= prev[0]:
                continue
        best[code] = (key, Winner(code=code, grade=grade, name=name))
    winners = [item[1] for item in sorted(best.values(), key=lambda pair: pair[1].code)]
    return winners, conflicts, invalid, truncated


def _keeper(rows: list[ExistingDefect]) -> ExistingDefect:
    return min(rows, key=lambda row: (row.bsl is None, row.id))


def plan_changes(existing: list[ExistingDefect], winners: list[Winner]) -> ChangePlan:
    """按 CODE 对齐。保留已有 BSL，不把它写进更新语句。"""
    by_code: dict[int, list[ExistingDefect]] = {}
    for row in existing:
        by_code.setdefault(int(row.code), []).append(row)

    plan = ChangePlan()
    source_codes = set()
    for winner in winners:
        source_codes.add(winner.code)
        current = by_code.get(winner.code)
        if not current:
            plan.inserts.append(winner)
            continue
        keep = _keeper(current)
        for row in current:
            if row.id != keep.id:
                plan.delete_ids.append(row.id)
        if (keep.grade or '') != winner.grade or (keep.name or '') != winner.name:
            plan.updates.append((keep.id, winner.grade, winner.name))

    for code, rows in by_code.items():
        if code in source_codes:
            continue
        for row in rows:
            plan.delete_ids.append(row.id)
    return plan


def build_message(batch: ProductBatch | None, ignored_rows: int = 0, error: str = '') -> str:
    parts = []
    if error:
        parts.append(error)
    if batch is not None:
        if batch.conflicts:
            parts.append(f'同 CODE 多行已取最新（{batch.conflicts} 行）')
        if batch.invalid:
            parts.append(f'跳过无法解析的 CODE（{batch.invalid} 行）')
        if batch.truncated:
            parts.append('超长字段已截断')
    if ignored_rows:
        parts.append(f'已忽略 WLT 分类下的同名配置（{ignored_rows} 行）')
    return '；'.join(parts)[:MESSAGE_MAX]


def run_product_sync(
    batches,
    ignored_count,
    *,
    find_product,
    load_defects,
    apply_changes,
    save_log,
    run_at: datetime,
):
    """每个型号单独提交。一个型号失败不影响后面的型号。"""
    seen_ignored_model = False
    for batch in batches:
        started = datetime.now()
        ignored_rows = ignored_count if batch.product_code == 'GC3003-3.5' else 0
        if ignored_rows:
            seen_ignored_model = True
        try:
            product_info_id = find_product(batch.product_code)
        except Exception as exc:
            _save_quietly(save_log, _record(
                batch, STATUS_FAILED, None, ChangePlan(), run_at, started,
                error=str(exc), ignored_rows=ignored_rows,
            ))
            continue
        if product_info_id is None:
            _save_quietly(save_log, _record(
                batch, STATUS_SKIPPED, None, ChangePlan(), run_at, started,
                error='未建档', ignored_rows=ignored_rows,
            ))
            continue
        try:
            existing = load_defects(product_info_id)
            plan = plan_changes(existing, batch.winners)
            apply_changes(product_info_id, plan)
        except Exception as exc:
            _save_quietly(save_log, _record(
                batch, STATUS_FAILED, product_info_id, ChangePlan(), run_at, started,
                error=str(exc), ignored_rows=ignored_rows,
            ))
            continue
        _save_quietly(save_log, _record(
            batch, STATUS_OK, product_info_id, plan, run_at, started,
            ignored_rows=ignored_rows,
        ))

    if ignored_count and not seen_ignored_model:
        started = datetime.now()
        placeholder = ProductBatch(
            product_code='GC3003-3.5',
            line='FT',
            src_rows=0,
        )
        _save_quietly(save_log, _record(
            placeholder, STATUS_OK, None, ChangePlan(), run_at, started,
            error='无 FT 配置，未改动缺陷代码',
            ignored_rows=ignored_count,
        ))


def _record(batch, status, product_info_id, plan, run_at, started, error='', ignored_rows=0):
    message = build_message(batch, ignored_rows=ignored_rows, error=error)
    return {
        'run_at': run_at,
        'product_code': batch.product_code,
        'prod_line': batch.line,
        'product_info_id': product_info_id,
        'status': status,
        'src_rows': batch.src_rows,
        'inserted': len(plan.inserts) if status == STATUS_OK else 0,
        'updated': len(plan.updates) if status == STATUS_OK else 0,
        'deleted': len(plan.delete_ids) if status == STATUS_OK else 0,
        'conflicts': batch.conflicts,
        'message': message,
        'started_at': started,
        'finished_at': datetime.now(),
    }


def _save_quietly(save_log, record):
    try:
        save_log(record)
    except Exception:
        logging.getLogger('defect_code_sync').exception(
            '写入同步记录失败 product=%s', record.get('product_code')
        )
