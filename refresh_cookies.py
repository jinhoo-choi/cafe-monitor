"""Conditional Naver cookie renewal. No paid AI, group mail, seen writes or auth bypass."""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import html
import json
import os
from pathlib import Path
import smtplib
import tempfile
import time
from email.mime.text import MIMEText

from cookie_auth import CHALLENGES, access_status, probe_urls, storage_status

COOKIE_FILE = Path('naver_cookies.json')
REPORT_FILE = Path('naver_cookie_status.json')
CONTROL_PATH = 'auth/cookie_control.json'
REPO = 'jinhoo-choi/cafe-monitor'


class RenewalError(Exception):
    """Only fixed, non-secret status codes may be stored or reported."""
    def __init__(self, status, http_status=None):
        super().__init__(status)
        self.http_status = http_status


def now():
    return datetime.now(timezone.utc)


def report(status, **details):
    value = {'schema_version': 1, 'status': status, 'checked_at': now().isoformat(),
             'paid_ai_calls': 0, **details}
    REPORT_FILE.write_text(json.dumps(value), encoding='utf-8')
    REPORT_FILE.chmod(0o600)
    print('[쿠키갱신] ' + json.dumps(value, ensure_ascii=False))
    return value


def write_storage(state):
    handle, name = tempfile.mkstemp(prefix='.naver_cookie_', dir='.')
    try:
        with os.fdopen(handle, 'w') as stream:
            json.dump(state, stream, ensure_ascii=False)
        os.replace(name, COOKIE_FILE)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load_storage():
    try:
        return json.loads(COOKIE_FILE.read_text())
    except (OSError, ValueError):
        return None


def reserve_attempt(queue, instant, run_id):
    """Durable SHA-checked reservation BEFORE login, including cancellation/failure cooldown."""
    control, sha = queue.get(CONTROL_PATH)
    control = control or {'schema_version': 1}
    previous = control.get('last_attempt_at')
    if previous:
        try:
            timestamp = datetime.fromisoformat(previous)
            if timestamp.tzinfo is None or instant - timestamp < timedelta(hours=24):
                return False
        except (ValueError, TypeError):
            return False
    control.update(last_attempt_at=instant.isoformat(), last_attempt_run=run_id)
    queue.put(CONTROL_PATH, control, sha)
    saved, _ = queue.get(CONTROL_PATH)
    if saved != control:
        raise RenewalError('control_unconfirmed')
    return True


def live_queue():
    from negative_queue import Queue
    queue = Queue()
    queue.preflight()
    settings = json.loads(queue.source('negative_subscription_config.json')[0])
    if settings.get('engine') != 'subscription' or settings.get('production_enabled') is not True:
        raise RenewalError('engine_inactive')
    return queue


def secret_key():
    import requests
    token = os.environ.get('ADMIN_GITHUB_TOKEN', '')
    if not token or os.environ.get('GITHUB_REPOSITORY') != REPO:
        raise RenewalError('secret_credentials_missing')
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json'}
    try:
        for attempt in range(3):
            try:
                response = requests.get(f'https://api.github.com/repos/{REPO}/actions/secrets/public-key',
                                        headers=headers, timeout=20)
            except requests.RequestException:
                if attempt == 2:
                    raise RenewalError('secret_preflight_unknown') from None
                time.sleep(attempt+1)
                continue
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                time.sleep(attempt+1)
                continue
            break
        if response.status_code != 200:
            status = 'secret_permission_failed' if response.status_code in {401,403,404} else 'secret_preflight_failed'
            raise RenewalError(status, response.status_code)
        data = response.json()
        if not data.get('key') or not data.get('key_id'):
            raise RenewalError('secret_permission_failed')
        return headers, data
    except RenewalError:
        raise
    except Exception:
        raise RenewalError('secret_preflight_unknown') from None


def store_secret(state, key):
    import requests
    from nacl import encoding, public
    headers, data = key
    encrypted = public.SealedBox(public.PublicKey(data['key'].encode(), encoding.Base64Encoder())).encrypt(
        json.dumps(state, ensure_ascii=False).encode())
    try:
        response = requests.put(f'https://api.github.com/repos/{REPO}/actions/secrets/NAVER_COOKIES_JSON',
            headers=headers, timeout=20, json={'key_id': data['key_id'],
            'encrypted_value': base64.b64encode(encrypted).decode()})
    except Exception:
        raise RenewalError('storage_uncertain') from None
    if response.status_code not in {201, 204}:
        raise RenewalError('storage_failed', response.status_code)


def login_once(browser):
    context = browser.new_context()
    try:
        page = context.new_page()
        page.goto('https://nid.naver.com/nidlogin.login', wait_until='domcontentloaded', timeout=20000)
        if any(hint in page.inner_text('body') for hint in CHALLENGES):
            raise RenewalError('human_required')
        page.fill('#id', os.environ['NAVER_ID'])
        page.fill('#pw', os.environ['NAVER_PW'])
        page.click('#log\\.login', timeout=10000)
        page.wait_for_timeout(2500)
        if any(hint in page.inner_text('body') for hint in CHALLENGES):
            raise RenewalError('human_required')
        if 'nidlogin' in page.url:
            raise RenewalError('login_failed')
        state = context.storage_state()
        if storage_status(state) != 'present':
            raise RenewalError('login_failed')
        return state
    except RenewalError:
        raise
    except Exception:
        raise RenewalError('login_unknown') from None
    finally:
        context.close()


def owner_notice(queue, status):
    """Owner only, once per status per 24h; notification failure is independent."""
    address, password = os.environ.get('GMAIL_USER'), os.environ.get('GMAIL_APP_PW')
    if not address or not password:
        return 'not_configured'
    try:
        control, sha = queue.get(CONTROL_PATH)
        control = control or {'schema_version': 1}
        notices = control.get('notice_history', {})
        previous = notices.get(status)
        if previous:
            if now() - datetime.fromisoformat(previous) < timedelta(hours=24):
                return 'suppressed'
        notices[status] = now().isoformat()
        control['notice_history'] = notices
        queue.put(CONTROL_PATH, control, sha)
        message = MIMEText('<p>네이버 쿠키 점검 상태: <b>' + html.escape(status) + '</b></p>'
            '<p>보안 인증은 자동으로 통과하지 않습니다. 회원 전용 본문 검증과 다음 자연 회차를 확인해 주세요.</p>',
            'html', 'utf-8')
        message['Subject'] = '[쿠키갱신봇] ' + status
        message['From'] = address
        message['To'] = address
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=20) as server:
            server.login(address, password)
            refused = server.sendmail(address, [address], message.as_string())
        return 'failed' if refused else 'accepted'
    except Exception:
        return 'failed'


def run(mode='auto'):
    settings = json.loads(Path('negative_subscription_config.json').read_text())
    if settings.get('engine') != 'subscription' or os.environ.get('DRY_RUN') == '1':
        print('[쿠키갱신] 보류; 로그인0/쓰기0/메일0/API0')
        return 0
    if mode == 'check_access':
        # Dedicated read-only diagnostic: no cookie access, browser, login, queue writes,
        # Secret PUT or SMTP, even if credential permissions are already valid.
        try:
            secret_key()
            report('secret_access_confirmed', login_attempted=False, secret_updated=False, notification='not_sent')
            return 0
        except Exception as error:
            status = str(error) if isinstance(error, RenewalError) else 'secret_preflight_unknown'
            details = {'http_status': error.http_status} if isinstance(error, RenewalError) and error.http_status else {}
            report(status, login_attempted=False, secret_updated=False, notification='not_sent', **details)
            return 2
    if os.environ.get('COOKIE_AUTO_RENEW_ENABLED', 'true').lower() in {'0', 'false'}:
        report('disabled', login_attempted=False, secret_updated=False)
        return 0
    urls = probe_urls(os.environ.get('NAVER_COOKIE_PROBES_JSON', ''))
    if not urls:
        try:
            from negative_queue import Queue
            queue = Queue()
            queue.preflight()
            probes, _ = queue.get('auth/cookie_probes.json')
            urls = probe_urls(json.dumps((probes or {}).get('urls', [])))
        except Exception:
            report('probe_config_unavailable', login_attempted=False, secret_updated=False)
            return 0 if mode == 'auto' else 2
    if not urls:
        report('not_configured', login_attempted=False, secret_updated=False)
        return 0 if mode == 'auto' else 2
    from playwright.sync_api import sync_playwright
    queue, attempted, secret_updated, access = None, False, False, 'unknown'
    checks = {}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                state = load_storage()
                status = access_status(browser, state, urls, checks=checks)
                access = status
                if status == 'healthy':
                    report(status, login_attempted=False, secret_updated=False, probe_checks=checks)
                    return 0
                if mode == 'check' or status != 'invalid':
                    report(status, login_attempted=False, secret_updated=False, probe_checks=checks)
                    return 0 if mode == 'auto' else 2
                if not os.environ.get('NAVER_ID') or not os.environ.get('NAVER_PW'):
                    raise RenewalError('login_credentials_missing')
                queue = live_queue()
                key = secret_key()
                if not reserve_attempt(queue, now(), os.environ.get('GITHUB_RUN_ID', 'manual')):
                    raise RenewalError('cooldown')
                live_queue()
                attempted = True
                fresh = login_once(browser)
                if access_status(browser, fresh, urls) != 'healthy':
                    raise RenewalError('new_cookie_unverified')
                live_queue()
                store_secret(fresh, key)
                secret_updated = True
                try:
                    write_storage(fresh)
                except Exception:
                    raise RenewalError('renewed_local_failed') from None
            finally:
                browser.close()
        notice = owner_notice(queue, 'renewed')
        report('renewed', login_attempted=attempted, secret_updated=True, notification=notice)
        return 0
    except Exception as error:
        status = str(error) if isinstance(error, RenewalError) else 'unknown'
        notice = owner_notice(queue, status) if queue and mode != 'check' else 'not_sent'
        details = {'http_status': error.http_status} if isinstance(error, RenewalError) and error.http_status else {}
        report(status, login_attempted=attempted, secret_updated=secret_updated, notification=notice,
               access_status=access, **details)
        return 0 if mode == 'auto' else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['auto', 'check', 'check_access', 'renew'], default='auto')
    raise SystemExit(run(parser.parse_args().mode))
