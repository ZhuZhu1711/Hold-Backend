from app.models.product import ProductInfo
from app.models.user import User
from app import db
from datetime import date, datetime

from app.controllers.dispose_ctrl import (
    owner_slot,
    transfer_open_holds_on_rebind,
)


def _engineer_label(eng_id, slot_id):
    if eng_id is None or str(eng_id).strip() == '':
        return '系统'
    user = User.query.get(eng_id)
    if user and user.NAME:
        return f'{user.NAME}({slot_id})'
    if int(slot_id) == owner_slot(None):
        return '系统'
    return str(slot_id)

PIE_MAX_LEN = 50


def normalize_pie(value):
    """去掉首尾空白。空串视为未绑定。超长返回失败。"""
    if value is None:
        return True, None
    text = str(value).strip()
    if not text:
        return True, None
    if len(text) > PIE_MAX_LEN:
        return False, f'PIE 不能超过 {PIE_MAX_LEN} 个字符'
    return True, text


_FORM_SUFFIXES = ('-3.5', '-2.6')


def product_family(product_id):
    """去掉末尾的 -3.5 / -2.6。GC8613-3.5 与 GC8613-2.6 同属 GC8613。"""
    product_id = str(product_id or '').strip()
    for suffix in _FORM_SUFFIXES:
        if product_id.endswith(suffix):
            return product_id[:-len(suffix)]
    return product_id


def family_product_ids(product_id):
    """同族只包含族名本身和两种形态，精确匹配，不用 LIKE。"""
    family = product_family(product_id)
    return [family, family + '-3.5', family + '-2.6']


def products_in_family(product_id):
    rows = (
        ProductInfo.query
        .filter(ProductInfo.PRODUCT_ID.in_(family_product_ids(product_id)))
        .all()
    )
    return list(rows or [])


def _recency(product):
    updated = getattr(product, 'UPDATE_DTTM', None)
    if isinstance(updated, datetime):
        updated = updated.date()
    elif not isinstance(updated, date):
        updated = date.min
    ident = getattr(product, 'ID', None) or 0
    return (updated, ident)


def newest_filled(members, attr):
    """同族不一致时，取更新时间较新且非空的值。"""
    chosen = None
    chosen_key = None
    for product in members:
        value = getattr(product, attr, None)
        if value is None or (isinstance(value, str) and not str(value).strip()):
            continue
        key = _recency(product)
        if chosen_key is None or key > chosen_key:
            chosen = value
            chosen_key = key
    return chosen


def _form_payload(product):
    return {
        'id': product.ID,
        'product_id': product.PRODUCT_ID,
        'gross_die': product.GROSS_DIE,
        'line_type': product.LINE_TYPE,
    }


def _engineer_name(members, eng_id):
    if eng_id is None or str(eng_id).strip() == '':
        return '未分配'
    for product in members:
        if product.PRO_ENG_ID == eng_id and product.owner and product.owner.NAME:
            return product.owner.NAME
    return '未分配'


def group_product_families(products):
    """按型号族收成一条。形态仍各自保留 Gross Die。"""
    buckets = {}
    order = []
    for product in products:
        family = product_family(product.PRODUCT_ID)
        if family not in buckets:
            buckets[family] = []
            order.append(family)
        buckets[family].append(product)
    grouped = []
    for family in order:
        members = buckets[family]
        updates = [
            item.UPDATE_DTTM for item in members
            if isinstance(item.UPDATE_DTTM, (date, datetime))
        ]
        latest = max(updates) if updates else None
        if isinstance(latest, datetime):
            latest = latest.date()
        eng = newest_filled(members, 'PRO_ENG_ID')
        grouped.append({
            'family': family,
            'product_id': family,
            'forms': sorted(
                (_form_payload(item) for item in members),
                key=lambda form: form['product_id'] or '',
            ),
            'engineer_id': eng,
            'engineer_name': _engineer_name(members, eng),
            'pie': newest_filled(members, 'PIE') or None,
            'update_dt': latest.strftime('%Y-%m-%d') if latest else '-',
        })
    return grouped


def line_type_from_product_id(product_id):
    """与 TRG_PRODUCT_INFO_LINE_TYPE_ALL 相同：-3.5 为 ATE(0)，-2.6 为 WLT(1)。"""
    product_id = str(product_id or '')
    if '-3.5' in product_id:
        return 0
    if '-2.6' in product_id:
        return 1
    return -1


def get_all_products(search=""):
    """
    获取产品列表，支持按产品ID搜索，并按ID倒序排列
    """
    try:
        query = ProductInfo.query
        
        if search:
            query = query.filter(ProductInfo.PRODUCT_ID.like(f"%{search}%"))
            
        # 默认按 ID 倒序，显示最新的在前面
        products = query.order_by(ProductInfo.ID.desc()).all()
        return True, "获取成功", products
    except Exception as e:
        return False, str(e), []

def _prepare_binding(data):
    """解析请求里的工程师和 PIE。没带的字段不改。"""
    pie_set = 'pie' in data
    eng_set = 'engineer_id' in data
    pie = None
    eng = None
    if pie_set:
        ok, pie = normalize_pie(data.get('pie'))
        if not ok:
            return False, pie, None, None
    if eng_set:
        eng_id = data.get('engineer_id')
        if eng_id not in (None, ''):
            if not User.query.get(eng_id):
                return False, "指定的工程师用户不存在", None, None
            eng = eng_id
    return True, None, (eng_set, eng), (pie_set, pie)


def _apply_engineer(product, new_raw):
    old_raw = product.PRO_ENG_ID
    old_slot = owner_slot(old_raw)
    new_slot = owner_slot(new_raw)
    product.PRO_ENG_ID = new_raw
    if old_slot != new_slot:
        note = (
            f'型号工程师由 {_engineer_label(old_raw, old_slot)} '
            f'调整为 {_engineer_label(new_raw, new_slot)}'
        )
        transfer_open_holds_on_rebind(
            product.PRODUCT_ID, old_slot, new_slot, note,
        )


def _touch(product, today):
    product.UPDATE_DTTM = today


def _apply_binding(members, eng_spec, pie_spec, today):
    eng_set, eng = eng_spec
    pie_set, pie = pie_spec
    if eng_set:
        for product in members:
            _apply_engineer(product, eng)
            _touch(product, today)
    if pie_set:
        for product in members:
            product.PIE = pie
            _touch(product, today)


def _apply_gross_die(members, gross_by_id, today):
    if not gross_by_id:
        return True, None
    member_ids = {member.ID for member in members}
    for form_id in gross_by_id:
        if form_id not in member_ids:
            return False, "形态不存在"
    for product in members:
        if product.ID in gross_by_id:
            product.GROSS_DIE = gross_by_id[product.ID]
            _touch(product, today)
    return True, None


def add_product(data):
    """
    新增一种形态。工程师和 PIE 属于整个型号：
    请求里带了就写回同族已有行，没带就从同族已有行抄过来。
    """
    try:
        product_id = str(data['product_id']).strip()
        if ProductInfo.query.filter_by(PRODUCT_ID=product_id).first():
            return False, "产品ID已存在"

        ok, err, eng_spec, pie_spec = _prepare_binding(data)
        if not ok:
            return False, err

        siblings = products_in_family(product_id)
        eng_set, eng = eng_spec
        pie_set, pie = pie_spec
        if not eng_set:
            eng = newest_filled(siblings, 'PRO_ENG_ID')
        if not pie_set:
            pie = newest_filled(siblings, 'PIE')

        today = datetime.now().date()
        new_product = ProductInfo()
        new_product.PRODUCT_ID = product_id
        new_product.GROSS_DIE = data.get('gross_die', 0)
        new_product.LINE_TYPE = line_type_from_product_id(product_id)
        new_product.PRO_ENG_ID = eng
        new_product.PIE = pie
        new_product.UPDATE_DTTM = today
        _apply_binding(siblings, eng_spec, pie_spec, today)

        db.session.add(new_product)
        db.session.commit()
        return True, "产品添加成功"
    except Exception as e:
        db.session.rollback()
        return False, str(e)

def update_product(product_id, data):
    """
    更新一行。Gross Die 只改这一行。
    请求里的工程师或 PIE 写到同族每一行；没带的字段不动。
    """
    try:
        product = ProductInfo.query.get(product_id)
        if not product:
            return False, "产品不存在"

        ok, err, eng_spec, pie_spec = _prepare_binding(data)
        if not ok:
            return False, err

        binding = eng_spec[0] or pie_spec[0]
        members = products_in_family(product.PRODUCT_ID) if binding else [product]
        if binding and not any(member.ID == product.ID for member in members):
            members = [product, *members]
        gross = {product.ID: data['gross_die']} if 'gross_die' in data else None
        return _commit_members(members, eng_spec, pie_spec, gross)
    except Exception as e:
        db.session.rollback()
        return False, str(e)


def update_family(family, data):
    """按型号族改工程师、PIE，并按形态 id 改各自的 Gross Die。"""
    family = product_family(family)
    if not family:
        return False, "产品ID不能为空"
    try:
        members = products_in_family(family)
        if not members:
            return False, "产品不存在"
        ok, err, eng_spec, pie_spec = _prepare_binding(data)
        if not ok:
            return False, err
        gross = {}
        for item in data.get('forms') or []:
            if item.get('id') is None or 'gross_die' not in item:
                continue
            gross[int(item['id'])] = item['gross_die']
        return _commit_members(members, eng_spec, pie_spec, gross or None)
    except Exception as e:
        db.session.rollback()
        return False, str(e)


def _commit_members(members, eng_spec, pie_spec, gross):
    today = datetime.now().date()
    _apply_binding(members, eng_spec, pie_spec, today)
    ok, err = _apply_gross_die(members, gross, today)
    if not ok:
        db.session.rollback()
        return False, err
    db.session.commit()
    return True, "更新成功"


def lookup_pie_by_product(product_id):
    """按族名或任一形态查所属 PIE。族里没有任何行才算不存在。"""
    product_id = str(product_id or '').strip()
    if not product_id:
        return False, "产品ID不能为空", None
    try:
        members = products_in_family(product_id)
        if not members:
            return False, "产品不存在", None
        return True, "获取成功", {
            'product_id': product_family(product_id),
            'pie': newest_filled(members, 'PIE') or None,
            'forms': sorted(
                (_form_payload(item) for item in members),
                key=lambda form: form['product_id'] or '',
            ),
        }
    except Exception as e:
        return False, str(e), None


def lookup_products_by_pie(pie):
    """按 PIE 精确匹配，结果按型号族去重。"""
    ok, normalized = normalize_pie(pie)
    if not ok:
        return False, normalized, None
    if not normalized:
        return False, "PIE 不能为空", None
    try:
        products = (
            ProductInfo.query
            .filter(ProductInfo.PIE == normalized)
            .order_by(ProductInfo.PRODUCT_ID.asc())
            .all()
        )
        return True, "获取成功", {
            'pie': normalized,
            'products': group_product_families(products),
        }
    except Exception as e:
        return False, str(e), None