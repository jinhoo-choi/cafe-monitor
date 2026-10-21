"""Laptop guard and workflow routing, no network or credentials."""
import json, os, subprocess, tempfile, unittest
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parent
GUARD=ROOT/'deploy/negative-laptop/job-guard.sh'

class LaptopGuard(unittest.TestCase):
    def run_guard(self, *, repo='jinhoo-choi/cafe-monitor', ref='refs/heads/main', event='workflow_dispatch', workflow='refresh_cookies.yml', inputs=None, enabled=False):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'event.json'
            path.write_text(json.dumps({'inputs':inputs if inputs is not None else {'mode':'check','laptop_check':'true'}}))
            env=dict(os.environ,GITHUB_REPOSITORY=repo,GITHUB_REF=ref,GITHUB_EVENT_NAME=event,
                GITHUB_WORKFLOW_REF=f'{repo}/.github/workflows/{workflow}@{ref}',GITHUB_EVENT_PATH=str(path))
            guard=Path(temp)/'job-guard.sh'
            guard.write_text(GUARD.read_text().replace('/etc/negative-monitor/production-enabled',str(Path(temp)/'production-enabled')))
            if enabled: (Path(temp)/'production-enabled').touch()
            return subprocess.run(['bash',str(guard)],env=env,capture_output=True).returncode
    def test_readonly_body_check_allowed(self): self.assertEqual(self.run_guard(),0)
    def test_readonly_secret_access_allowed(self): self.assertEqual(self.run_guard(inputs={'mode':'check_access','laptop_check':True}),0)
    def test_other_repository_blocked(self): self.assertNotEqual(self.run_guard(repo='attacker/cafe-monitor'),0)
    def test_non_main_blocked(self): self.assertNotEqual(self.run_guard(ref='refs/heads/untrusted'),0)
    def test_pull_request_blocked(self): self.assertNotEqual(self.run_guard(event='pull_request'),0)
    def test_pull_request_target_blocked(self): self.assertNotEqual(self.run_guard(event='pull_request_target'),0)
    def test_unknown_workflow_blocked(self): self.assertNotEqual(self.run_guard(workflow='evil.yml'),0)
    def test_collection_before_cutover_blocked(self): self.assertNotEqual(self.run_guard(workflow='monitor.yml'),0)
    def test_collection_after_enable_still_blocked(self): self.assertNotEqual(self.run_guard(workflow='monitor.yml',enabled=True),0)
    def test_auto_renew_before_cutover_blocked(self): self.assertNotEqual(self.run_guard(inputs={'mode':'auto','laptop_check':True}),0)
    def test_unspecified_laptop_check_blocked(self): self.assertNotEqual(self.run_guard(inputs={'mode':'check'}),0)
    def test_push_before_cutover_blocked(self): self.assertNotEqual(self.run_guard(event='push'),0)
    def test_workflow_and_concurrency_preserved(self):
        for filename in ('monitor.yml','refresh_cookies.yml'):
            v=yaml.load((ROOT/'.github/workflows'/filename).read_text(),Loader=yaml.BaseLoader)
            expected='naver-cafe-monitor' if filename=='monitor.yml' else 'naver-cafe-cookie-renewal'
            self.assertEqual(v['concurrency'],{'group':expected,'cancel-in-progress':'false'})
            self.assertNotIn('schedule',v['on'])
            route=next(iter(v['jobs'].values()))['runs-on']
            self.assertEqual(route,'ubuntu-latest' if filename=='monitor.yml' else 'negative-monitor-kr')
        v=yaml.load((ROOT/'.github/workflows/refresh_cookies.yml').read_text(),Loader=yaml.BaseLoader)
        self.assertEqual(v['on']['push']['paths'],['cookie_refresh_request.json'])
        self.assertEqual(v['on']['workflow_dispatch']['inputs']['laptop_check']['default'],'false')
        v=json.loads((ROOT/'negative_subscription_config.json').read_text())
        self.assertEqual(v['engine'],'subscription')
        self.assertIs(v['paid_api_fallback'],False)



class CloudCollectionSplit(unittest.TestCase):
    def test_followup_cloud_check_is_readonly_even_if_laptop_fails(self):
        value=yaml.load((ROOT/'.github/workflows/refresh_cookies.yml').read_text(),Loader=yaml.BaseLoader)
        job=value['jobs']['verify_cloud']
        self.assertEqual(job['needs'],'refresh')
        self.assertIn('always()',job['if'])
        self.assertIn("outputs.mode == 'check'",job['if'])
        self.assertEqual(job['runs-on'],'ubuntu-latest')
        script='\n'.join(s.get('run','') for s in job['steps'])
        self.assertIn('refresh_cookies.py --mode check',script)
        self.assertNotIn('negative_subscription.py',script)
        self.assertNotIn('--mode auto',script)
        env={k for s in job['steps'] for k in s.get('env',{})}
        self.assertEqual(env,{'NAVER_COOKIE_STATE','NEGATIVE_QUEUE_TOKEN','NAVER_COOKIE_PROBES_JSON','COOKIE_AUTO_RENEW_ENABLED'})
        self.assertTrue(all('if' not in s or s['if']=='always()' for s in job['steps']))

    def test_cloud_preflight_is_readonly_and_fail_closed(self):
        value=yaml.load((ROOT/'.github/workflows/monitor.yml').read_text(),Loader=yaml.BaseLoader)
        steps=value['jobs']['monitor']['steps']
        check=next(s for s in steps if s.get('run')=='python refresh_cookies.py --mode check')
        self.assertEqual(set(check['env']),{'NEGATIVE_QUEUE_TOKEN','NAVER_COOKIE_PROBES_JSON','COOKIE_AUTO_RENEW_ENABLED'})
        self.assertEqual(check['env']['COOKIE_AUTO_RENEW_ENABLED'],'true')
        self.assertNotIn('continue-on-error',check)
        self.assertFalse(any('refresh_cookies.py --mode auto' in s.get('run','') for s in steps))
        self.assertLess(steps.index(check),next(i for i,s in enumerate(steps) if s.get('run')=='python negative_subscription.py collect'))
        self.assertNotIn('needs',value['jobs']['monitor'])


if __name__=='__main__': unittest.main()
