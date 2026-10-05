# Separate Geo keyword worker

Source: pending verified Drive keyword file. Destination: pending separate Geo Airtable base/table. Runtime names: geo-keyword-sync.service and geo-keyword-sync.timer. Configuration: /etc/geo/keyword-sync.json. State: /var/lib/geo/keyword-sync. Code: /opt/geo-keyword-sync.

Proposed sync time is 06:30 America/Los_Angeles, separate from other tenants. Do not enable before source access, mapping and a read-only comparison pass. Chase mentioned a Google Cloud worker; confirm Cloud hosting versus the separate droplet template when configuring the project. Never deploy both.

Identity is normalized keyword plus target page, not row number. Update only mapped source-owned fields. Preserve publication status, record IDs and manual changes when source fields are unchanged. Conflicting edits, duplicate identities, formula errors, possible renames and ambiguous writes stop execution. Save backups and a pending journal before authorized mutations; reconcile interruptions before retrying. Retain removals rather than deleting rows. No worker is deployed.
