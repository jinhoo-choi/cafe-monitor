#!/bin/bash
set -euo pipefail
deny() { echo '::error::Laptop runner rejected an unauthorized job'; exit 1; }
[[ ${GITHUB_REPOSITORY:-} == jinhoo-choi/cafe-monitor ]] || deny
[[ ${GITHUB_REF:-} == refs/heads/main ]] || deny
case ${GITHUB_WORKFLOW_REF:-} in
  jinhoo-choi/cafe-monitor/.github/workflows/monitor.yml@refs/heads/main)
    [[ ${GITHUB_EVENT_NAME:-} == workflow_dispatch ]] || deny ;;
  jinhoo-choi/cafe-monitor/.github/workflows/refresh_cookies.yml@refs/heads/main)
    [[ ${GITHUB_EVENT_NAME:-} == workflow_dispatch || ${GITHUB_EVENT_NAME:-} == push ]] || deny ;;
  *) deny ;;
esac
# Before cutover, only an explicit read-only manual check may use this laptop.
if [[ ! -f /etc/negative-monitor/production-enabled ]]; then
  [[ ${GITHUB_WORKFLOW_REF:-} == */refresh_cookies.yml@refs/heads/main ]] || deny
  [[ ${GITHUB_EVENT_NAME:-} == workflow_dispatch ]] || deny
  python3 - <<'PY'
import json, os
v=json.load(open(os.environ['GITHUB_EVENT_PATH']))['inputs']
assert str(v.get('laptop_check','')).lower() == 'true'
assert v.get('mode') in ('check','check_access')
PY
fi
echo 'Laptop workflow allowlist passed'
