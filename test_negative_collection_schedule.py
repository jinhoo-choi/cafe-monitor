"""Offline checks: schedule boundaries and workflow side-effect gates."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

import yaml
import negative_collection_schedule as schedule


class CollectionScheduleTests(unittest.TestCase):
    def test_base_hours_after_temporary_window(self):
        for hour in range(24):
            with self.subTest(hour=hour):
                self.assertEqual(schedule.collection_slot(f'2026-11-14T{hour:02}:00:25+09:00')['allowed'],
                                 hour in {8, 15, 22})

    def test_temporary_window_expires_at_korean_midnight(self):
        self.assertFalse(schedule.collection_slot('2026-11-08T10:00:00+09:00')['allowed'])
        for day in [9, 10, 11]:
            for hour in [10, 12, 18]:
                with self.subTest(day=day, hour=hour):
                    self.assertTrue(schedule.collection_slot(f'2026-11-{day:02}T{hour}:00:00+09:00')['allowed'])
        for hour in [10, 12, 18]:
            self.assertFalse(schedule.collection_slot(f'2026-11-12T{hour}:00:00+09:00')['allowed'])
        self.assertEqual(schedule.collection_slot('2026-11-11T15:00:00Z')['schedule'], 'base_08_15_22')

    def test_real_missed_round_dispatches_are_blocked(self):
        for stamp in ['2026-11-14T01:00:25Z', '2026-11-14T03:00:26Z', '2026-11-14T09:00:25Z']:
            self.assertFalse(schedule.collection_slot(stamp)['allowed'])
        for stamp in ['2026-11-13T23:00:27Z', '2026-11-14T06:00:25Z', '2026-11-14T13:00:27Z']:
            self.assertTrue(schedule.collection_slot(stamp)['allowed'])

    def test_naive_and_invalid_times_fail_closed(self):
        for stamp in ['2026-11-14T08:00:00', 'not-a-date', '']:
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                schedule.collection_slot(stamp)

    def run_main(self, stamp, mutate=None, fail=False):
        run = {'id': 43597560784, 'created_at': stamp, 'event': 'workflow_dispatch',
               'head_branch': 'main', 'path': schedule.WORKFLOW,
               'repository': {'full_name': schedule.REPOSITORY}}
        if mutate:
            mutate(run)
        requests = []
        def opener(request, timeout):
            requests.append(request)
            if fail:
                raise OSError('private diagnostic MUST NOT appear in logs')
            return io.StringIO(json.dumps(run))
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/'output'
            env = {'GITHUB_REPOSITORY': schedule.REPOSITORY, 'GITHUB_RUN_ID': '43597560784',
                   'GITHUB_TOKEN': 'offline-test-token', 'GITHUB_OUTPUT': str(output)}
            log = io.StringIO()
            with redirect_stdout(log):
                code = schedule.main(env, opener)
            outputs = dict(line.split('=', 1) for line in output.read_text().splitlines())
        self.assertNotIn('offline-test-token', log.getvalue())
        self.assertNotIn('private diagnostic', log.getvalue())
        return code, outputs, log.getvalue(), requests

    def test_queued_runner_uses_original_dispatch_time(self):
        # No runner wall-clock input: an08h dispatch remains the08h slot even if it starts later.
        code, output, _, requests = self.run_main('2026-11-14T23:00:26Z')
        self.assertEqual(code, 0)
        self.assertEqual(output['allowed'], 'true')
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].get_method(), 'GET')

    def test_outside_dispatch_skips_successfully(self):
        code, output, log, _ = self.run_main('2026-11-14T09:00:25Z')
        self.assertEqual(code, 0)
        self.assertEqual(output['allowed'], 'false')
        self.assertIn('outside_approved_collection_hours', log)

    def test_bad_run_metadata_never_unblocks_collection(self):
        for key, value in [('id', 1), ('head_branch', 'other'), ('event', 'push'),
                           ('path', '.github/workflows/other.yml'),
                           ('repository', {'full_name': 'other/repo'})]:
            with self.subTest(key=key):
                code, output, _, _ = self.run_main('2026-11-14T13:00:27Z',
                                                  lambda run: run.update({key: value}))
                self.assertEqual(code, 1)
                self.assertEqual(output['allowed'], 'false')

    def test_api_failure_never_unblocks_collection(self):
        code, output, _, _ = self.run_main('2026-11-14T13:00:27Z', fail=True)
        self.assertEqual(code, 1)
        self.assertEqual(output['allowed'], 'false')

    def test_workflow_gates_all_subscription_side_effects(self):
        workflow = yaml.safe_load(Path('.github/workflows/monitor.yml').read_text())
        steps = workflow['jobs']['monitor']['steps']
        gate = next(s for s in steps if s.get('id') == 'collection_schedule')
        self.assertIn('negative_collection_schedule.py', gate['run'])
        self.assertEqual(workflow['permissions']['actions'], 'read')
        names = ['수집 패키지·브라우저 설치', '네이버 쿠키 복원',
                 '저장된 쿠키의 회원 본문 확인 — 로그인0·갱신0·메일0·큐쓰기0',
                 '구독 회차 수집 — API0·운영 발송0·이력 쓰기0']
        for name in names:
            step = next(s for s in steps if s.get('name') == name)
            self.assertIn("steps.collection_schedule.outputs.allowed == 'true'", step['if'])
            self.assertLess(steps.index(gate), steps.index(step))
        notice = next(s for s in steps if s.get('name') == '구독 오류 본인 알림')
        self.assertIn("steps.collection_schedule.outputs.allowed == 'true'", notice['if'])
        self.assertIn("steps.collection_schedule.outcome == 'failure'", notice['if'])
        self.assertEqual(next(s for s in steps if s.get('name') == '명시적 API 모드 운영')['if'],
                         "steps.engine.outputs.value == 'api'")


if __name__ == '__main__':
    unittest.main()
