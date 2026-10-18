#!/bin/bash
set -euo pipefail
[[ $(id -u) == 0 ]] || exit 1
umask 077
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git python3 python3-venv python3-pip libicu70 libssl3 libkrb5-3 zlib1g util-linux
id negativebot >/dev/null 2>&1 || useradd --create-home --shell /bin/bash negativebot
passwd -l negativebot >/dev/null
install -d -m 700 -o negativebot -g negativebot /opt/negative-monitor /opt/negative-monitor/runner
install -d -m 755 /etc/negative-monitor
install -m 755 /tmp/negative-laptop/job-guard.sh /etc/negative-monitor/job-guard.sh
install -m 755 /tmp/negative-laptop/supervise.sh /usr/local/sbin/negative-runner-supervise
python3 -m venv /opt/negative-monitor/venv
/opt/negative-monitor/venv/bin/pip install requests playwright jsonschema pynacl pyyaml
chown -R negativebot:negativebot /opt/negative-monitor/venv
/opt/negative-monitor/venv/bin/playwright install-deps chromium
runuser -u negativebot -- /opt/negative-monitor/venv/bin/playwright install --only-shell chromium
cat >/etc/wsl.conf <<'EOF'
[automount]
enabled=false
[interop]
enabled=false
appendWindowsPath=false
[user]
default=negativebot
EOF
if [[ ! -x /opt/negative-monitor/runner/bin/Runner.Listener ]]; then
  curl --fail --location --max-time 600 https://github.com/actions/runner/releases/download/v2.338.0/actions-runner-linux-x64-2.338.0.tar.gz -o /tmp/negative-monitor-runner.tgz
  echo 'af4b794c1bc41d73d40535e3fe092a39f9679cd8d965954c2aca25a05ca41d32  /tmp/negative-monitor-runner.tgz' | sha256sum -c -
  tar xzf /tmp/negative-monitor-runner.tgz -C /opt/negative-monitor/runner
  chown -R negativebot:negativebot /opt/negative-monitor/runner
  rm -f /tmp/negative-monitor-runner.tgz
fi
install -m 755 /tmp/negative-laptop/register-runner.py /usr/local/bin/negative-register-runner
# Never grant the runner sudo access; dependencies are prepared by root here.
echo 'Bootstrap ready; runner is not registered or started'
