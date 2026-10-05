#!/usr/bin/env bash
set -euo pipefail
[[ "${GEO_PROVISION_HOST:-}" == "yes" ]] || { echo "Review this script and set GEO_PROVISION_HOST=yes." >&2; exit 1; }
[[ "$(id -u)" == "0" ]] || { echo "Root required." >&2; exit 1; }
command -v python3 >/dev/null
command -v git >/dev/null
id geo-blog >/dev/null 2>&1 || useradd --create-home --home-dir /var/lib/geo-blog --shell /bin/bash geo-blog
install -d -o geo-blog -g geo-blog -m 0700 /opt/geo-blog /var/lib/geo-blog /var/lib/geo-blog/storage
install -d -o root -g geo-blog -m 0750 /etc/geo-blog
# Credential installation, reviewed code upload and dependency installation are separate.
# This script never enables services or timers or changes host-wide configuration.
