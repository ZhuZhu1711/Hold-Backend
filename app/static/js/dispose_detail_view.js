/**
 * 处置详情展示：片数少时直出；片数多时预览 + 点击弹窗看全文。
 * 依赖：页面已有 esc() 时可传入；否则自带简易转义。
 */
(function (global) {
    var PREVIEW_MAX = 3;
    var _store = {};
    var _seq = 0;
    var _ready = false;

    function _esc(v) {
        if (typeof global.esc === 'function') return global.esc(v);
        return String(v == null ? '' : v)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function splitWaferDetails(text) {
        if (text == null) return [];
        var t = String(text).trim();
        if (!t || t === '-') return [];
        var body = t;
        if (/^W:/i.test(body)) body = body.slice(2);
        // 新直白格式（#xx，…）或旧 W: 编码，均按 ; 分段
        if (body.indexOf(';') >= 0 || /^#/.test(body) || /[=:]/.test(body)) {
            return body.split(';').map(function (s) { return s.trim(); }).filter(Boolean);
        }
        return [t];
    }

    function ensureModal() {
        if (_ready) return;
        _ready = true;
        var style = document.createElement('style');
        style.textContent = [
            '.ddv-inline{line-height:1.55;color:#555;}',
            '.ddv-preview{line-height:1.55;color:#555;}',
            '.ddv-link{color:#007bff;background:none;border:none;padding:0;margin-top:4px;',
            'cursor:pointer;font:inherit;text-decoration:underline;}',
            '.ddv-link:hover{color:#0056b3;}',
            '.ddv-mask{display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:90;}',
            '.ddv-mask.open{display:block;}',
            '.ddv-modal{display:none;position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);',
            'width:min(560px,92vw);max-height:80vh;background:#fff;border-radius:8px;',
            'box-shadow:0 8px 32px rgba(0,0,0,.2);z-index:100;overflow:hidden;',
            'flex-direction:column;}',
            '.ddv-modal.open{display:flex;}',
            '.ddv-modal-h{display:flex;justify-content:space-between;align-items:center;',
            'padding:14px 16px;border-bottom:1px solid #eee;font-weight:600;font-size:15px;}',
            '.ddv-modal-b{padding:14px 16px;overflow:auto;font-size:13px;line-height:1.6;}',
            '.ddv-list{margin:0;padding-left:18px;}',
            '.ddv-list li{margin-bottom:6px;}',
            '.ddv-close{border:none;background:#e9ecef;border-radius:4px;padding:4px 10px;cursor:pointer;}'
        ].join('');
        document.head.appendChild(style);

        var mask = document.createElement('div');
        mask.id = 'ddvMask';
        mask.className = 'ddv-mask';
        mask.addEventListener('click', closeDisposeDetailModal);

        var modal = document.createElement('div');
        modal.id = 'ddvModal';
        modal.className = 'ddv-modal';
        modal.innerHTML =
            '<div class="ddv-modal-h"><span id="ddvTitle">处置详情</span>' +
            '<button type="button" class="ddv-close" id="ddvCloseBtn">关闭</button></div>' +
            '<div class="ddv-modal-b" id="ddvBody"></div>';

        document.body.appendChild(mask);
        document.body.appendChild(modal);
        document.getElementById('ddvCloseBtn').addEventListener('click', closeDisposeDetailModal);
        document.addEventListener('keydown', function (ev) {
            if (ev.key === 'Escape') closeDisposeDetailModal();
        });
        document.addEventListener('click', function (ev) {
            var btn = ev.target.closest('[data-ddv-key]');
            if (!btn) return;
            var key = btn.getAttribute('data-ddv-key');
            openDisposeDetailModal('处置详情', _store[key] || '');
        });
    }

    function closeDisposeDetailModal() {
        var mask = document.getElementById('ddvMask');
        var modal = document.getElementById('ddvModal');
        if (mask) mask.classList.remove('open');
        if (modal) modal.classList.remove('open');
    }

    function openDisposeDetailModal(title, text) {
        ensureModal();
        document.getElementById('ddvTitle').textContent = title || '处置详情';
        var parts = splitWaferDetails(text);
        var body = document.getElementById('ddvBody');
        if (parts.length > 1) {
            body.innerHTML = '<ul class="ddv-list">' +
                parts.map(function (p) { return '<li>' + _esc(p) + '</li>'; }).join('') +
                '</ul>';
        } else {
            body.innerHTML = '<div>' + _esc(text || '-') + '</div>';
        }
        document.getElementById('ddvMask').classList.add('open');
        document.getElementById('ddvModal').classList.add('open');
    }

    /**
     * 返回可直接插入 DOM 的 HTML。
     * 片数 > PREVIEW_MAX 时显示前 2 条 +「查看全部 N 片」。
     */
    function renderDisposeDetail(text) {
        ensureModal();
        if (text == null || String(text).trim() === '' || String(text).trim() === '-') {
            return _esc(text == null || String(text).trim() === '' ? '' : text);
        }
        var parts = splitWaferDetails(text);
        if (parts.length <= PREVIEW_MAX) {
            if (parts.length > 1) {
                return '<div class="ddv-inline">' +
                    parts.map(function (p) { return _esc(p); }).join('<br>') +
                    '</div>';
            }
            return _esc(String(text));
        }
        var key = 'k' + (++_seq);
        _store[key] = String(text);
        var preview = parts.slice(0, 2).map(function (p) { return _esc(p); }).join('<br>');
        return '<div class="ddv-preview">' + preview +
            '<br><button type="button" class="ddv-link" data-ddv-key="' + key + '">查看全部 ' +
            parts.length + ' 片</button></div>';
    }

    global.renderDisposeDetail = renderDisposeDetail;
    global.openDisposeDetailModal = openDisposeDetailModal;
    global.closeDisposeDetailModal = closeDisposeDetailModal;
    global.splitWaferDetails = splitWaferDetails;
})(window);
