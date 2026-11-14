"""Check the existing monitor run's approved collection slot before side effects."""

from datetime import date, datetime
import json
import os
from pathlib import Path
import urllib.request
from zoneinfo import ZoneInfo

REPOSITORY = 'jinhoo-choi/cafe-monitor'
WORKFLOW = '.github/workflows/monitor.yml'
KST = ZoneInfo('Asia/Seoul')
BASE_HOURS = frozenset({8, 15, 22})
TEMPORARY_HOURS = frozenset({8, 10, 12, 15, 18, 22})
TEMPORARY_START = date(2026, 11, 9)
TEMPORARY_END = date(2026, 11, 11)


def collection_slot(created_at):
    """Use dispatch creation time, so runner queue delays do not change the slot."""
    instant = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
    if instant.utcoffset() is None:
        raise ValueError('workflow creation time needs a timezone')
    local = instant.astimezone(KST)
    temporary = TEMPORARY_START <= local.date() <= TEMPORARY_END
    hours = TEMPORARY_HOURS if temporary else BASE_HOURS
    return {
        'allowed': local.hour in hours,
        'dispatch_created_at': instant.isoformat(),
        'dispatch_kst': local.isoformat(),
        'schedule': 'temporary_20261109_11' if temporary else 'base_08_15_22',
        'reason': 'approved_collection_hour' if local.hour in hours else 'outside_approved_collection_hours',
    }


def load_run(environ, opener=urllib.request.urlopen):
    repository = environ.get('GITHUB_REPOSITORY')
    run_id = environ.get('GITHUB_RUN_ID', '')
    token = environ.get('GITHUB_TOKEN', '')
    if repository != REPOSITORY or not run_id.isdigit() or not token:
        raise ValueError('monitor run identity or token missing')
    request = urllib.request.Request(
        f'https://api.github.com/repos/{REPOSITORY}/actions/runs/{run_id}',
        headers={'Accept': 'application/vnd.github+json',
                 'Authorization': 'Bearer ' + token,
                 'X-GitHub-Api-Version': '2022-11-28'},
    )
    with opener(request, timeout=20) as response:
        run = json.load(response)
    if (str(run.get('id')) != run_id or run.get('event') != 'workflow_dispatch'
            or run.get('head_branch') != 'main' or run.get('path') != WORKFLOW
            or run.get('repository', {}).get('full_name') != REPOSITORY):
        raise ValueError('unexpected monitor run metadata')
    return run


def main(environ=None, opener=urllib.request.urlopen):
    environ = os.environ if environ is None else environ
    output = Path(environ['GITHUB_OUTPUT'])
    # A metadata/network/parse failure must leave no true output to unblock collection.
    with output.open('a') as stream:
        stream.write('allowed=false\n')
    try:
        run = load_run(environ, opener)
        decision = collection_slot(run['created_at'])
    except Exception as error:
        print(json.dumps({'allowed': False, 'reason': 'schedule_check_failed',
                          'error_type': type(error).__name__}))
        return 1
    with output.open('a') as stream:
        stream.write('allowed=' + str(decision['allowed']).lower() + '\n')
    print(json.dumps(decision, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
