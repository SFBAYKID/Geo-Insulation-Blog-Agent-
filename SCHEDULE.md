# Geo weekly schedule

Wednesday production runs at 09:00 America/Los_Angeles through the existing gated UTC 16:00/17:00 cron entries.
Monday drafting runs at 17:00 UTC (09:00 Pacific in winter, 10:00 in summer) with `python -m geo_blog.cli --env-file /var/lib/geo-blog/agent.env weekly --test`.
It sends only to the test channel and saves the checked draft in weekly-next.json for Wednesday.
An existing reusable staged draft prevents a second draft.
Install deploy/geo-blog-production.cron only in geo-blog's own crontab, preserving any other Geo entries.
The systemd weekly timer stays disabled to avoid duplicate runs.
Failures and advisory SEO findings belong only in C0B02721MNK.

Chase reconfirmed on November 12, 2026 that Wednesday publication must not wait for Slack approval.
The exact checked version publishes automatically, then a verified live link and Undo publication button go to C0C9NUA9BG9.
New Slack announcements are restricted to weekdays 09:00–17:00 America/Chicago; deferred announcements retain durable receipts.
Early staging never advances the publication date.
