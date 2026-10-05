#!/usr/bin/env bash
# Initial privileged installation only; run after the Geo account checks pass.
set -euo pipefail
[[ $(id -u) == 0 ]] || { echo 'Run as root.' >&2; exit 1; }
[[ $(getent passwd geo-blog | cut -d: -f6) == /var/lib/geo-blog ]]
[[ -x /opt/geo-blog/.venv/bin/python ]]
[[ -f /var/lib/geo-blog/agent.env.pending ]]
[[ ! -e /etc/geo-blog/agent.env ]] || { echo 'Geo configuration already exists; inspect before replacing.' >&2; exit 1; }
for unit in geo-blog.service geo-blog-weekly.service geo-blog-weekly.timer; do
  [[ ! -e /etc/systemd/system/$unit ]] || { echo "Geo unit already exists: $unit" >&2; exit 1; }
done
# Only Geo-owned destinations are created or changed.
install -d -o root -g geo-blog -m 0750 /etc/geo-blog
install -o root -g geo-blog -m 0640 /var/lib/geo-blog/agent.env.pending /etc/geo-blog/agent.env
for unit in geo-blog.service geo-blog-weekly.service geo-blog-weekly.timer; do
  install -o root -g root -m 0644 "/opt/geo-blog/deploy/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
# Start only the test-channel listener. Do not enable the weekly timer.
systemctl enable --now geo-blog.service
systemctl is-active geo-blog.service
printf 'Geo test-channel listener installed. Weekly timer remains disabled.\n'
