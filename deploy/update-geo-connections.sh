#!/usr/bin/env bash
# One-time privileged update of Geo's existing service; no other tenant is changed.
set -euo pipefail
[[ $(id -u) == 0 ]] || { echo 'Run from the existing root terminal.' >&2; exit 1; }
[[ $(getent passwd geo-blog | cut -d: -f6) == /var/lib/geo-blog ]]
release=${1:?Pass the reviewed Geo release identifier}
[[ $release =~ ^[a-f0-9]{7,40}$ ]]
archive="/var/lib/geo-blog/geo-runtime-${release}.tar"
source_dir="/opt/geo-blog/releases/${release}"
[[ -f "$archive" && -f "$source_dir/main.py" ]]
[[ -f /var/lib/geo-blog/agent.env.pending ]]
# Validate the explicit staged config before touching the running service.
runuser -u geo-blog -- /opt/geo-blog/.venv/bin/python - "$source_dir" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from geo_blog.settings import Settings
from geo_blog.store import Store
s = Settings(_env_file='/var/lib/geo-blog/agent.env.pending')
s.require('basecamp_client_id', 'basecamp_client_secret', 'basecamp_refresh_token',
          'basecamp_account_id', 'basecamp_project_id', 'basecamp_todolist_id',
          'slack_bot_token', 'slack_app_token')
assert s.basecamp_project_id == 40851698
assert not s.production_delivery_enabled and not s.publishing_enabled
assert str(s.storage_dir) == '/var/lib/geo-blog/storage'
row = Store(s.storage_dir).get('integration-preview-20261005')
assert row and row['status'] == 'approved', 'Playground approval test must pass first'
assert not (s.storage_dir / 'publication-plan.json').exists()
print('Geo staged configuration and real playground approval verified.')
PY
backup="/var/lib/geo-blog/service-backups/$(date -u +%Y%m%dT%H%M%SZ)"
install -d -o root -g root -m 0700 "$backup"
cp -p /etc/systemd/system/geo-blog.service "$backup/"
cp -p /etc/geo-blog/agent.env "$backup/previous-agent.env"
# Extract as the isolated user, preserving its existing assets and runtime state.
runuser -u geo-blog -- tar -xf "$archive" -C /opt/geo-blog
install -o geo-blog -g geo-blog -m 0600 /var/lib/geo-blog/agent.env.pending /var/lib/geo-blog/agent.env
install -d -o root -g root -m 0755 /etc/systemd/system/geo-blog.service.d
cat > /etc/systemd/system/geo-blog.service.d/geo-runtime.conf <<'EOF'
[Service]
EnvironmentFile=
EnvironmentFile=/var/lib/geo-blog/agent.env
EOF
install -o root -g root -m 0644 "$source_dir/deploy/geo-blog-weekly.service" /etc/systemd/system/geo-blog-weekly.service
install -o root -g root -m 0644 "$source_dir/deploy/geo-blog-weekly.timer" /etc/systemd/system/geo-blog-weekly.timer
# Future authorized Geo config updates no longer need changes to root-owned files.
systemctl daemon-reload
systemctl restart geo-blog.service
systemctl is-active geo-blog.service
printf 'Geo connections installed. Weekly activation is managed separately after website readiness.\n'
