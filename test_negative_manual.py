import copy
from datetime import timedelta
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

import negative_manual as manual
from negative_queue import digest, now
from test_negative_verification import fixture


class ManualSupplement(unittest.TestCase):
    def setUp(self):
        self.value = {'schema_version': 1, 'items': [{
            'post_id': 'stocktraining:101', 'title': '한국투자증권 앱 주문 오류 불편',
            'url': 'https://cafe.naver.com/f-e/cafes/29798500/articles/101',
            'body': '한국투자증권 앱에서 주문이 계속 실패해서 불편합니다. 실제 체결 여부를 확인할 수 없습니다.',
            'date_raw': '2026.10.20. 15:21', 'observed_at': (now()-timedelta(minutes=1)).isoformat(),
            'body_verified': True}]}
        self.patch = patch.object(manual.common, 'seen_snapshot', return_value=({}, 'seen'))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def prepare(self, value=None):
        return manual.prepare(self.value if value is None else value, 'negative-manual-offline')

    def test_existing_validator_accepts_review_and_rejects_production_promotion(self):
        job, skipped = self.prepare()
        _, result = fixture()
        post_id = job['input']['items'][0]['post_id']
        result.update(job_id=job['job_id'], input_hash=job['input_hash'])
        result['posts'][0]['post_id'] = post_id
        result['groups'][0].update(member_post_ids=[post_id], representative_post_id=post_id)
        manual.common.validate(job, result)
        self.assertEqual(skipped, 0)
        self.assertFalse(job['input']['collection']['cookie_auth_verified'])
        job.update(mode='production', self_test=False)
        with self.assertRaisesRegex(ValueError, '과거 검수 자료 혼입'):
            manual.common.validate(job, result, allow_production=True)

    def test_missing_unverified_body_and_bad_source_rejected(self):
        for change in [{'body_verified': False}, {'body_verified': 1}, {'body': ''},
                       {'post_id': 'stocktraining:102'}, {'url': self.value['items'][0]['url']+'?art=private'},
                       {'url': 'https://cafe.naver.com/f-e/cafes/1/articles/101'},
                       {'observed_at': '2026-10-21T12:00:00'},
                       {'observed_at': (now()+timedelta(days=1)).isoformat()}]:
            value = copy.deepcopy(self.value)
            value['items'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.prepare(value)

    def test_seen_and_duplicate_are_never_silently_reimported(self):
        with patch.object(manual.common, 'seen_snapshot', return_value=({'stocktraining:101': 'stamp'}, 'seen')):
            with self.assertRaisesRegex(ValueError, '이미 처리'):
                self.prepare()
        self.value['items'] *= 2
        with self.assertRaisesRegex(ValueError, '중복'):
            self.prepare()

    def test_uncertain_date_requires_review_and_masks_personal_information(self):
        self.value['items'][0].update(date_raw='15:21')
        self.value['items'][0]['body'] += ' 연락처 010-1234-5678, test@example.invalid'
        job, _ = self.prepare()
        entry = job['input']['items'][0]
        self.assertTrue(entry['collection_window_uncertain'])
        self.assertNotIn('010-1234-5678', entry['body_excerpt'])
        self.assertNotIn('test@example.invalid', entry['body_excerpt'])
        self.assertEqual(job['input_hash'], digest(job['input']))

    def test_cli_writes_private_review_only_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder)/'input.json', Path(folder)/'review.json'
            source.write_text(json.dumps(self.value))
            with patch('sys.argv', ['negative_manual.py', '--input', str(source), '--output', str(output),
                                    '--job-id', 'negative-manual-offline']), \
                 patch('negative_queue.Queue', side_effect=AssertionError('queue forbidden')), \
                 patch('smtplib.SMTP_SSL', side_effect=AssertionError('mail forbidden')):
                manual.main()
                self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
                data = output.read_bytes()
                with self.assertRaises(FileExistsError):
                    manual.main()
                self.assertEqual(output.read_bytes(), data)


if __name__ == '__main__':
    unittest.main()
