# Geo isolated deployment

Shared host: 143.110.146.87. Provision only user/group geo-blog, code /opt/geo-blog, state /var/lib/geo-blog/storage and credentials /etc/geo-blog/agent.env (root:geo-blog, mode 0640). Geo units live in deploy/. Never modify other tenants' files, processes, services, credentials or schedules. No host firewall/swap changes or package replacement.

bootstrap-small.sh is a guarded directory/user preparation template only. It does not enable units. Read it fully before execution. Use the existing shared Node installation read-only when needed; remote website checks are required. Do not run Chrome or site builds on the small host.

Upload only reviewed code and fresh Geo configuration. Install deploy/agent-requirements.txt into a separate venv. Never upload local storage or credentials over an existing server deployment. Verify memory headroom, unit hardening, server_smoke and one Socket Mode listener before enabling anything. Acquire workload locks before later restarts. No server changes have been performed.
