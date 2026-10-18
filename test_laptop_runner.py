"""Laptop guard and workflow routing, no network or credentials."""
import json, os, subprocess, tempfile, unittest
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parent
GUARD=ROOT/'deploy/negative-laptop/job-guard.sh'

class LaptopGuard(unittest.TestCase):
    def run_guard(self, *, repo='jinhoo-choi/cafe-monitor', ref='refs/heads/main', event='workflow_dispatch', workflow='refresh_cookies.yml', inputs=None):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'event.json'
            path.write_text(json.dumps({'inputs':inputs if inputs is not None else {'mode':'check','laptop_check':'true'}}))
            env=dict(os.environ,GITHUB_REPOSITORY=repo,GITHUB_REF=ref,GITHUB_EVENT_NAME=event,
                GITHUB_WORKFLOW_REF=f'{repo}/.github/workflows/{workflow}@{ref}',GITHUB_EVENT_PATH=str(path))
            return subprocess.run(['bash',str(GUARD)],env=env,capture_output=True).returncode
    def test_readonly_body_check_allowed(self): self.assertEqual(self.run_guard(),0)
    def test_readonly_secret_access_allowed(self): self.assertEqual(self.run_guard(inputs={'mode':'check_access','laptop_check':True}),0)
    def test_other_repository_blocked(self): self.assertNotEqual(self.run_guard(repo='attacker/cafe-monitor'),0)
    def test_non_main_blocked(self): self.assertNotEqual(self.run_guard(ref='refs/heads/untrusted'),0)
    def test_pull_request_blocked(self): self.assertNotEqual(self.run_guard(event='pull_request'),0)
    def test_pull_request_target_blocked(self): self.assertNotEqual(self.run_guard(event='pull_request_target'),0)
    def test_unknown_workflow_blocked(self): self.assertNotEqual(self.run_guard(workflow='evil.yml'),0)
    def test_collection_before_cutover_blocked(self): self.assertNotEqual(self.run_guard(workflow='monitor.yml'),0)
    def test_auto_renew_before_cutover_blocked(self): self.assertNotEqual(self.run_guard(inputs={'mode':'auto','laptop_check':True}),0)
    def test_unspecified_laptop_check_blocked(self): self.assertNotEqual(self.run_guard(inputs={'mode':'check'}),0)
    def test_push_before_cutover_blocked(self): self.assertNotEqual(self.run_guard(event='push'),0)
    def test_workflow_and_concurrency_preserved(self):
        for filename in ('monitor.yml','refresh_cookies.yml'):
            v=yaml.load((ROOT/'.github/workflows'/filename).read_text(),Loader=yaml.BaseLoader)
            self.assertEqual(v['concurrency'],{'group':'naver-cafe-monitor','cancel-in-progress':'false'})
            self.assertNotIn('schedule',v['on'])
            route=next(iter(v['jobs'].values()))['runs-on']
            if filename=='monitor.yml': self.assertEqual(route,'ubuntu-latest')
            else:
                self.assertIn('inputs.laptop_check',route)
                self.assertNotIn('vars.',route)
        v=yaml.load((ROOT/'.github/workflows/refresh_cookies.yml').read_text(),Loader=yaml.BaseLoader)
        self.assertEqual(v['on']['push']['paths'],['cookie_refresh_request.json'])
        self.assertEqual(v['on']['workflow_dispatch']['inputs']['laptop_check']['default'],'false')
        v=json.loads((ROOT/'negative_subscription_config.json').read_text())
        self.assertEqual(v['engine'],'subscription')
        self.assertIs(v['paid_api_fallback'],False)

if __name__=='__main__': unittest.main()
