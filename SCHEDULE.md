# Geo weekly schedule

Wednesday production runs at 09:00 America/Los_Angeles through the existing gated UTC 16:00/17:00 cron entries.
Monday drafting runs at 17:00 UTC (09:00 Pacific in winter, 10:00 in summer) with `python -m geo_blog.cli --env-file /var/lib/geo-blog/agent.env weekly --test`.
It sends only to the test channel and saves the checked draft in weekly-next.json for Wednesday.
An existing reusable staged draft prevents a second draft.
Install deploy/geo-blog-production.cron only in geo-blog's own crontab, preserving any other Geo entries.
The systemd weekly timer stays disabled to avoid duplicate runs.
Failures and advisory SEO findings belong only in C0B02721MNK.
