#!/bin/bash
set -euo pipefail
exec 9>/run/negative-monitor-runner.lock
flock -n 9 || exit 0
cd /opt/negative-monitor/runner
[[ -f .runner ]] || { echo 'Runner registration required'; exit 2; }
export PATH=/opt/negative-monitor/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export ACTIONS_RUNNER_HOOK_JOB_STARTED=/etc/negative-monitor/job-guard.sh
export PYTHONUTF8=1
export LANG=C.UTF-8
export HOME=/home/negativebot USER=negativebot LOGNAME=negativebot
umask 077
while true; do
  runuser -u negativebot --preserve-environment -- ./run.sh || true
  sleep 15
done
