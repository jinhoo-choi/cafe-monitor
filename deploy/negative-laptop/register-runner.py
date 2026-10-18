#!/usr/bin/env python3
"""Enter a repository runner registration token locally. Never prints credentials."""
import getpass, os, subprocess
from pathlib import Path

folder=Path('/opt/negative-monitor/runner')
if os.getuid()==0:
    raise SystemExit('Run as negativebot, never root')
if (folder/'.runner').exists():
    raise SystemExit('Already registered; no replacement performed')
token=getpass.getpass('Repository runner registration token (hidden; never paste in chat): ').strip()
if not token:
    raise SystemExit('No token entered; no registration performed')
env=os.environ.copy()
env['ACTIONS_RUNNER_INPUT_TOKEN']=token
env['ACTIONS_RUNNER_HOOK_JOB_STARTED']='/etc/negative-monitor/job-guard.sh'
env['PATH']='/opt/negative-monitor/venv/bin:'+env['PATH']
env['PYTHONUTF8']='1'
os.umask(0o077)
result=subprocess.run(['./config.sh','--unattended','--url','https://github.com/jinhoo-choi/cafe-monitor','--name','negative-monitor-kr-laptop','--no-default-labels','--labels','negative-monitor-kr','--work','_work'],cwd=folder,env=env,capture_output=True,text=True)
token=None
if result.returncode:
    raise SystemExit('Runner configuration failed; credential-bearing output suppressed')
with (folder/'.env').open('a') as out:
    out.write('\nACTIONS_RUNNER_HOOK_JOB_STARTED=/etc/negative-monitor/job-guard.sh\nPYTHONUTF8=1\n')
for name in ('.env','.credentials','.credentials_rsaparams','.runner'):
    p=folder/name
    if p.exists(): p.chmod(0o600)
print('Runner registered with dedicated label; no PAT stored; not started')
