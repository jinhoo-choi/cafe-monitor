"""Fault injection with fake cookies/SMTP/queue/browser. No network or real credentials."""
import copy
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import cookie_auth as auth
import refresh_cookies as renewal

URLS = ['https://cafe.naver.com/f-e/cafes/100/articles/101',
        'https://cafe.naver.com/f-e/cafes/200/articles/201']
STATE = {'cookies': [{'name': name, 'value': 'FAKE-' + name, 'domain': '.naver.com',
                     'path': '/', 'expires': -1} for name in auth.CORE], 'origins': []}


class FakeQueue:
    def __init__(self, value=None):
        self.value = copy.deepcopy(value)
        self.sha = 'initial' if value else None
        self.writes = 0

    def get(self, path):
        return copy.deepcopy(self.value), self.sha

    def put(self, path, value, sha):
        if sha != self.sha:
            raise RuntimeError('SHA conflict')
        self.value, self.sha = copy.deepcopy(value), 'next-' + str(self.writes)
        self.writes += 1


class CookieTests(unittest.TestCase):
    def test_key_service_failure_is_bounded_and_not_mislabeled_permission(self):
        with patch.dict(os.environ,{'ADMIN_GITHUB_TOKEN':'FAKE_SECRET','GITHUB_REPOSITORY':renewal.REPO}), \
             patch('requests.get',return_value=MagicMock(status_code=500)) as get, \
             patch.object(renewal.time,'sleep'):
            with self.assertRaises(renewal.RenewalError) as raised:
                renewal.secret_key()
            self.assertEqual(str(raised.exception),'secret_preflight_failed')
            self.assertEqual(raised.exception.http_status,500); self.assertEqual(get.call_count,3)

    def test_secret_access_denial_has_safe_http_code_and_no_retry(self):
        for code in [401,403,404]:
            response=MagicMock(status_code=code)
            with patch.dict(os.environ,{'ADMIN_GITHUB_TOKEN':'FAKE_SECRET','GITHUB_REPOSITORY':renewal.REPO}), \
                 patch('requests.get',return_value=response) as get:
                with self.assertRaises(renewal.RenewalError) as raised:
                    renewal.secret_key()
                self.assertEqual(raised.exception.http_status,code)
                self.assertNotIn('FAKE_SECRET',str(raised.exception))
                self.assertEqual(get.call_count,1)
                self.assertEqual(get.call_args.kwargs['timeout'],20)

    def test_transient_public_key_get_is_retried_without_login(self):
        ok=MagicMock(status_code=200)
        ok.json.return_value={'key':'FAKE_PUBLIC','key_id':'FAKE_ID'}
        with patch.dict(os.environ,{'ADMIN_GITHUB_TOKEN':'FAKE_SECRET','GITHUB_REPOSITORY':renewal.REPO}), \
             patch('requests.get',side_effect=[MagicMock(status_code=500),ok]) as get, \
             patch.object(renewal.time,'sleep'), patch.object(renewal,'login_once') as login:
            headers,key=renewal.secret_key()
            self.assertEqual(key['key_id'],'FAKE_ID'); self.assertEqual(get.call_count,2)
            login.assert_not_called()

    def setUp(self):
        self.network = patch.object(socket, 'create_connection', side_effect=AssertionError('network forbidden'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_session_and_tracking_expiry(self):
        state = copy.deepcopy(STATE)
        state['cookies'].append({'name': 'nid_inf', 'value': 'FAKE', 'expires': 1})
        self.assertEqual(auth.storage_status(state, 100), 'present')

    def test_invalid_auth_cookie_variants(self):
        for mutation in [{'expires': 0}, {'expires': 99}, {'expires': float('nan')},
                         {'expires': True}, {'value': ''}, {'domain': 'naver.com.evil.example'}]:
            state = copy.deepcopy(STATE)
            state['cookies'][0].update(mutation)
            with self.subTest(mutation=mutation):
                self.assertEqual(auth.storage_status(state, 100), 'invalid')
        self.assertEqual(auth.storage_status({'cookies': []}), 'invalid')

    def test_url_allowlist(self):
        self.assertEqual(auth.probe_urls(json.dumps(URLS)), URLS)
        for value in [URLS[:1], [URLS[0], URLS[0]], [URLS[0], 'https://evil.test/1'],
                      [URLS[0], URLS[1]+'?art=secret'], [URLS[0], 1]]:
            self.assertEqual(auth.probe_urls(json.dumps(value)), [])

    def test_blank_masked_and_punctuation_not_readable(self):
        for text in ['\u200b'*100, '!!!'*100, '[외부주소]'*100, 'https://example.test/'*100]:
            self.assertFalse(auth.readable(text))

    def test_protected_cookie_validation_outcomes(self):
        scenarios = [(['auth_required','member_required','body','body'], 'healthy'),
                     (['auth_required','member_required','auth_required','auth_required'], 'invalid'),
                     (['auth_required','member_required','body','permission_denied'], 'permission_denied'),
                     (['auth_required','member_required','human_required','auth_required'], 'human_required'),
                     (['auth_required','member_required','unknown','unknown'], 'unknown')]
        for values, expected in scenarios:
            browser = MagicMock()
            with self.subTest(expected=expected), patch.object(auth, 'page_status', side_effect=values):
                self.assertEqual(auth.access_status(browser, STATE, URLS), expected)
                self.assertEqual(browser.new_context.call_count, 2)
                self.assertEqual(browser.new_context.return_value.close.call_count, 2)

    def test_public_or_unknown_probe_never_authenticates(self):
        for values in [['body','auth_required'], ['unknown','auth_required'], ['permission_denied','auth_required']]:
            browser = MagicMock()
            with patch.object(auth, 'page_status', side_effect=values):
                self.assertEqual(auth.access_status(browser, STATE, URLS), 'probe_unconfirmed')
                self.assertEqual(browser.new_context.call_count, 1)

    def test_http_500_and_public_home_not_healthy(self):
        page = MagicMock()
        page.goto.return_value.status = 500
        self.assertEqual(auth.page_status(page, URLS[0]), 'unknown')
        page.goto.return_value.status = 200
        page.url = 'https://www.naver.com/'
        page.frames = []
        self.assertEqual(auth.page_status(page, URLS[0]), 'unknown')

    def test_unknown_does_not_remove_existing_cookie_or_claim_verified(self):
        self.assertEqual(auth.collector_auth(STATE, {'status': 'not_configured'}), (True,'not_configured',False))
        self.assertEqual(auth.collector_auth(STATE, {'status': 'healthy'}), (True,'healthy',True))
        self.assertFalse(auth.collector_auth(STATE, {'status': 'cooldown','access_status':'invalid'})[0])

    def test_durable_cooldown_survives_failed_or_cancelled_attempt(self):
        queue = FakeQueue()
        instant = datetime(2026,10,15,tzinfo=timezone.utc)
        self.assertTrue(renewal.reserve_attempt(queue, instant, 'attempt-1'))
        self.assertFalse(renewal.reserve_attempt(queue, instant+timedelta(hours=23), 'attempt-2'))
        self.assertEqual(queue.writes, 1)
        self.assertTrue(renewal.reserve_attempt(queue, instant+timedelta(hours=24), 'attempt-3'))

    def test_cooldown_control_missing_timezone_fails_closed(self):
        queue = FakeQueue({'last_attempt_at': '2026-10-15T00:00:00'})
        self.assertFalse(renewal.reserve_attempt(queue, datetime.now(timezone.utc), 'attempt'))
        self.assertEqual(queue.writes, 0)

    def test_sha_conflict_or_unconfirmed_reservation_prevents_login(self):
        queue = FakeQueue()
        with patch.object(queue, 'put', side_effect=RuntimeError('conflict')):
            with self.assertRaises(RuntimeError):
                renewal.reserve_attempt(queue, datetime.now(timezone.utc), 'attempt')
        with patch.object(queue, 'get', side_effect=[(None,None),(None,None)]):
            with self.assertRaises(renewal.RenewalError):
                renewal.reserve_attempt(queue, datetime.now(timezone.utc), 'attempt')


class RunTests(unittest.TestCase):
    def setUp(self):
        self.previous = os.getcwd()
        self.directory = tempfile.TemporaryDirectory()
        os.chdir(self.directory.name)
        self.addCleanup(os.chdir, self.previous)
        self.addCleanup(self.directory.cleanup)
        Path('negative_subscription_config.json').write_text(json.dumps({'engine':'subscription'}))
        renewal.write_storage(STATE)
        self.env = patch.dict(os.environ, {'NAVER_COOKIE_PROBES_JSON':json.dumps(URLS),
            'NAVER_ID':'FAKE','NAVER_PW':'FAKE','GITHUB_REPOSITORY':renewal.REPO,'GITHUB_RUN_ID':'fake-run'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.browser = patch('playwright.sync_api.sync_playwright')
        self.browser.start()
        self.addCleanup(self.browser.stop)
        self.report = patch.object(renewal, 'report')
        self.reporter = self.report.start()
        self.addCleanup(self.report.stop)
        self.net = patch.object(socket, 'create_connection', side_effect=AssertionError('network forbidden'))
        self.net.start()
        self.addCleanup(self.net.stop)

    def test_healthy_check_and_auto_login_secret_smtp_all_zero(self):
        for mode in ['check','auto','renew']:
            with patch.object(renewal,'access_status',return_value='healthy'), \
                 patch.object(renewal,'login_once') as login, patch.object(renewal,'store_secret') as secret, \
                 patch.object(renewal,'owner_notice') as smtp:
                self.assertEqual(renewal.run(mode), 0)
                login.assert_not_called(); secret.assert_not_called(); smtp.assert_not_called()

    def test_check_invalid_cookie_does_not_login_or_write_private_state(self):
        with patch.object(renewal,'access_status',return_value='invalid'), \
             patch.object(renewal,'live_queue') as queue, patch.object(renewal,'login_once') as login:
            self.assertEqual(renewal.run('check'), 2)
            queue.assert_not_called(); login.assert_not_called()

    def test_unknown_permission_or_public_probe_never_logs_in(self):
        for status in ['unknown','permission_denied','human_required','probe_unconfirmed']:
            with patch.object(renewal,'access_status',return_value=status), \
                 patch.object(renewal,'login_once') as login, patch.object(renewal,'store_secret') as secret:
                self.assertEqual(renewal.run('auto'), 0)
                login.assert_not_called(); secret.assert_not_called()

    def successful_mocks(self):
        return [patch.object(renewal,'access_status',side_effect=['invalid','healthy']),
                patch.object(renewal,'live_queue',return_value=FakeQueue()),
                patch.object(renewal,'secret_key',return_value='FAKE'),
                patch.object(renewal,'login_once',return_value=STATE),
                patch.object(renewal,'store_secret'), patch.object(renewal,'owner_notice',return_value='failed')]

    def test_renewal_success_is_independent_of_notice_failure(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            mocks=[stack.enter_context(p) for p in self.successful_mocks()]
            self.assertEqual(renewal.run('auto'), 0)
            self.assertEqual(mocks[3].call_count, 1); self.assertEqual(mocks[4].call_count, 1)
            self.reporter.assert_called_with('renewed',login_attempted=True,secret_updated=True,notification='failed')
            self.assertEqual(Path('naver_cookies.json').stat().st_mode & 0o777, 0o600)

    def test_captcha_no_secret_write_and_cooldown_remains(self):
        queue = FakeQueue()
        with patch.object(renewal,'access_status',return_value='invalid'), \
             patch.object(renewal,'live_queue',return_value=queue), patch.object(renewal,'secret_key'), \
             patch.object(renewal,'login_once',side_effect=renewal.RenewalError('human_required')) as login, \
             patch.object(renewal,'store_secret') as secret, patch.object(renewal,'owner_notice'):
            self.assertEqual(renewal.run('renew'), 2)
            self.assertEqual(renewal.run('auto'), 0)
            self.assertEqual(login.call_count, 1); secret.assert_not_called()
            self.assertEqual(queue.writes, 1)

    def test_unverified_fresh_cookie_does_not_replace_old_cookie(self):
        before=Path('naver_cookies.json').read_bytes()
        with patch.object(renewal,'access_status',side_effect=['invalid','unknown']), \
             patch.object(renewal,'live_queue',return_value=FakeQueue()), patch.object(renewal,'secret_key'), \
             patch.object(renewal,'login_once',return_value=STATE), patch.object(renewal,'store_secret') as secret, \
             patch.object(renewal,'owner_notice'):
            self.assertEqual(renewal.run('renew'), 2)
            secret.assert_not_called(); self.assertEqual(before, Path('naver_cookies.json').read_bytes())

    def test_secret_permission_failure_stops_before_login(self):
        with patch.object(renewal,'access_status',return_value='invalid'), \
             patch.object(renewal,'live_queue',return_value=FakeQueue()), \
             patch.object(renewal,'secret_key',side_effect=renewal.RenewalError('secret_permission_failed')), \
             patch.object(renewal,'login_once') as login, patch.object(renewal,'owner_notice'):
            renewal.run('auto'); login.assert_not_called()

    def test_storage_timeout_never_retries_or_changes_local_cookie(self):
        from contextlib import ExitStack
        before=Path('naver_cookies.json').read_bytes()
        with ExitStack() as stack:
            mocks=[stack.enter_context(p) for p in self.successful_mocks()]
            mocks[4].side_effect=renewal.RenewalError('storage_uncertain')
            renewal.run('auto')
            self.assertEqual(mocks[3].call_count,1); self.assertEqual(mocks[4].call_count,1)
            self.assertEqual(before,Path('naver_cookies.json').read_bytes())
            self.assertEqual(self.reporter.call_args.args[0],'storage_uncertain')

    def test_engine_paused_and_dry_run_are_inert(self):
        for engine, dry in [('paused',''),('api',''),('subscription','1')]:
            Path('negative_subscription_config.json').write_text(json.dumps({'engine':engine}))
            with patch.dict(os.environ,{'DRY_RUN':dry}), patch.object(renewal,'access_status') as access, \
                 patch.object(renewal,'live_queue') as queue:
                self.assertEqual(renewal.run('auto'),0)
                access.assert_not_called(); queue.assert_not_called()


if __name__ == '__main__':
    unittest.main()
