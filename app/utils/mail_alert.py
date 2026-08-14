"""严重报错邮件通知（SMTP 用户名+密码）。普通异常不要走这里。"""

from __future__ import annotations

import logging
import smtplib
import socket
import sys
import threading
import traceback
from datetime import datetime
from email.mime.text import MIMEText
from typing import Any

logger = logging.getLogger(__name__)

_hooks_installed = False
_cooldown_lock = threading.Lock()
_last_sent: dict[str, float] = {}


class SevereError(Exception):
    """显式严重错误：捕获后应调用 notify_severe_error，或让其冒泡给全局钩子。"""


def _cfg():
    from app.config import Config
    return Config


def _recipients(cfg) -> list[str]:
    raw = getattr(cfg, 'ALERT_MAIL_TO', None) or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(x).strip() for x in raw if str(x).strip()]


def mail_configured(cfg=None) -> bool:
    cfg = cfg or _cfg()
    if not getattr(cfg, 'ALERT_MAIL_ENABLED', True):
        return False
    host = str(getattr(cfg, 'ALERT_SMTP_HOST', '') or '').strip()
    user = str(getattr(cfg, 'ALERT_SMTP_USER', '') or '').strip()
    password = str(getattr(cfg, 'ALERT_SMTP_PASSWORD', '') or '')
    return bool(host and user and password and _recipients(cfg))


def _fingerprint(subject: str, detail: str) -> str:
    first = (detail or '').strip().splitlines()[0] if detail else ''
    return f'{subject}|{first}'[:400]


def _under_cooldown(key: str, seconds: int) -> bool:
    now = datetime.now().timestamp()
    with _cooldown_lock:
        last = _last_sent.get(key, 0)
        if now - last < seconds:
            return True
        _last_sent[key] = now
        return False


def send_alert_mail(subject: str, body: str, cfg=None) -> bool:
    """SMTP 密码验证发信。配置不全或发送失败返回 False，不向外抛。"""
    cfg = cfg or _cfg()
    if not mail_configured(cfg):
        logger.warning('严重报错邮件未发送：SMTP 配置不完整')
        return False

    host = str(cfg.ALERT_SMTP_HOST).strip()
    port = int(getattr(cfg, 'ALERT_SMTP_PORT', 465) or 465)
    user = str(cfg.ALERT_SMTP_USER).strip()
    password = str(cfg.ALERT_SMTP_PASSWORD)
    mail_from = str(getattr(cfg, 'ALERT_MAIL_FROM', '') or '').strip() or user
    recipients = _recipients(cfg)
    use_ssl = bool(getattr(cfg, 'ALERT_SMTP_SSL', True))

    msg = MIMEText(body, 'plain', 'utf-8')
    msg['Subject'] = subject
    msg['From'] = mail_from
    msg['To'] = ', '.join(recipients)

    try:
        if use_ssl:
            smtp = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            smtp = smtplib.SMTP(host, port, timeout=15)
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
        try:
            smtp.login(user, password)
            smtp.sendmail(mail_from, recipients, msg.as_string())
        finally:
            try:
                smtp.quit()
            except Exception:
                pass
        logger.info('严重报错邮件已发送: %s', subject)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error('严重报错邮件发送失败: %s', exc)
        return False


def notify_severe_error(
    subject: str,
    detail: str = '',
    exc: BaseException | None = None,
    cfg=None,
) -> bool:
    """仅用于严重故障。不要在普通业务 except 里调用。"""
    cfg = cfg or _cfg()
    hostname = socket.gethostname()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    parts = [
        f'时间: {now}',
        f'主机: {hostname}',
        f'主题: {subject}',
    ]
    if detail:
        parts.append('')
        parts.append(detail.strip())
    if exc is not None:
        parts.append('')
        parts.append(''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    body = '\n'.join(parts)
    full_subject = f'[Hold-Backend 严重错误] {subject}'

    cooldown = int(getattr(cfg, 'ALERT_MAIL_COOLDOWN_SECONDS', 1800) or 0)
    key = _fingerprint(subject, body)
    if cooldown > 0 and _under_cooldown(key, cooldown):
        logger.error('严重报错（冷却期内未重复发信）: %s', subject)
        return False

    logger.error('严重报错: %s', subject)
    return send_alert_mail(full_subject, body, cfg=cfg)


def _format_uncaught(exc_type, exc_value, exc_tb) -> str:
    return ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))


def _sys_excepthook(exc_type, exc_value, exc_tb):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    notify_severe_error(
        f'未捕获异常: {exc_type.__name__}',
        _format_uncaught(exc_type, exc_value, exc_tb),
        exc=exc_value if isinstance(exc_value, BaseException) else None,
    )
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def _thread_excepthook(args: Any) -> None:
    exc_type = args.exc_type
    if exc_type and issubclass(exc_type, KeyboardInterrupt):
        return
    name = getattr(args.thread, 'name', None) if getattr(args, 'thread', None) else 'unknown'
    notify_severe_error(
        f'线程未捕获异常 [{name}]: {getattr(exc_type, "__name__", exc_type)}',
        _format_uncaught(args.exc_type, args.exc_value, args.exc_traceback),
        exc=args.exc_value if isinstance(args.exc_value, BaseException) else None,
    )


class _CriticalMailHandler(logging.Handler):
    """仅 logging.CRITICAL 发信；ERROR/WARNING 不发。"""

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.CRITICAL:
            return
        try:
            msg = self.format(record)
            exc = record.exc_info[1] if record.exc_info else None
            notify_severe_error(record.getMessage(), msg, exc=exc)
        except Exception:  # noqa: BLE001
            self.handleError(record)


def install_severe_error_hooks() -> None:
    """安装未捕获异常钩子 + CRITICAL 日志发信。可重复调用。"""
    global _hooks_installed
    if _hooks_installed:
        return
    sys.excepthook = _sys_excepthook
    threading.excepthook = _thread_excepthook
    root = logging.getLogger()
    if not any(isinstance(h, _CriticalMailHandler) for h in root.handlers):
        handler = _CriticalMailHandler()
        handler.setLevel(logging.CRITICAL)
        handler.setFormatter(logging.Formatter('%(asctime)s %(name)s %(levelname)s %(message)s'))
        root.addHandler(handler)
    _hooks_installed = True
