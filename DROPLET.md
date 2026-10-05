# Geo isolated deployment

Shared host: 143.110.146.87. Provision only user/group geo-blog, code /opt/geo-blog, state /var/lib/geo-blog/storage and credentials /etc/geo-blog/agent.env (root:geo-blog, mode 0640). Geo units live in deploy/. Never modify other tenants' files, processes, services, credentials or schedules. No host firewall/swap changes or package replacement.

bootstrap-small.sh is a guarded directory/user preparation template only. It does not enable units. Read it fully before execution. Use the existing shared Node installation read-only when needed; remote website checks are required. Do not run Chrome or site builds on the small host.

Upload only reviewed code and fresh Geo configuration. Install deploy/agent-requirements.txt into a separate venv. Never upload local storage or credentials over an existing server deployment. Verify memory headroom, unit hardening, server_smoke and one Socket Mode listener before enabling anything. Acquire workload locks before later restarts. The Geo account, code and virtual environment are now installed. Root registration of the reviewed units is pending; see SETUP-PROGRESS.md.

## Verified installation, October 5, 2026

SSH uses geo-blog@143.110.146.87 with the private local key storage/ssh/geo-blog-ed25519. That account has no sudo privileges. Server configuration is staged privately at /var/lib/geo-blog/agent.env.pending. Root runs /opt/geo-blog/deploy/install-geo-services.sh once to install /etc/geo-blog/agent.env and the Geo units. The script starts only the test-channel listener and never enables the weekly timer. It refuses to overwrite existing server configuration or units. MemoryHigh=384M, MemoryMax=512M, CPUQuota=50% and TasksMax=64 limit each Geo service.

The Mac listener stays off. Server DEPLOYMENT_TARGET=droplet lets the bot report its actual deployment location. Basecamp runtime authorization, queue selection and production integration remain pending.
