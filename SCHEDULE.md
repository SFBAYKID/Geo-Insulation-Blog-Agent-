# Geo weekly schedule

Verified against geo-blog's server crontab on November 25, 2026.
The host clock is UTC; Pacific and Central times below are what the code enforces.

## Weekly cycle

| When | Job | Result |
|---|---|---|
| Monday 17:00 UTC (09:00 PST / 10:00 PDT) | `python -m geo_blog.cli --env-file /var/lib/geo-blog/agent.env weekly --test` | Writes, edits, illustrates and previews the next approved topic, saves it in weekly-next.json and posts it only to the test channel. |
| Wednesday 09:00 America/Los_Angeles | `python -m geo_blog.scheduled_preview --production --env-file /var/lib/geo-blog/agent.env` | Publishes the staged draft automatically, or writes one first if nothing is staged. |

## Monday staging

It sends only to the test channel C0B02721MNK.
An existing reusable staged draft (ready, pending or approved) prevents a second draft.
Early staging never publishes and never advances the publication date.

## Wednesday publication

Cron fires at both 16:00 and 17:00 UTC on Wednesday; the Python gate runs only the one that is 09:00 Pacific, across daylight saving changes.
A dated receipt in storage/scheduled-production allows one attempt per Pacific date; an ambiguous run is never retried automatically.
The run refuses to start while the previous production review is unresolved; only published, rejected or unpublished count as resolved.
Chase reconfirmed on November 12, 2026 that Wednesday publication must not wait for Slack approval.
The exact checked version merges automatically through its website PR.
After merging, the agent waits up to 15 minutes for Vercel's Production deployment of that exact commit.
It then repeats the full live check (page, image, FAQ, sitemap) every 20 seconds for up to 30 attempts, because the new page can lag a successful deployment.
A page still failing after that stops as publication_needs_inspection and must be reconciled before retrying.
After verification it marks the research queue record Published and completes any Basecamp task.
The verified live link and Undo publication button go to C0C9NUA9BG9.
New Slack announcements are restricted to weekdays 09:00–17:00 America/Chicago; deferred announcements retain durable receipts.

## Alerts

Failures, advisory SEO findings and research queue warnings go only to C0B02721MNK.
Each Wednesday run checks the queue before and after; it warns when approved (Proposed) topics fall to RESEARCH_QUEUE_LOW_THRESHOLD, currently 2.

## Installation

Install deploy/geo-blog-production.cron only in geo-blog's own crontab, preserving any other Geo entries.
The systemd weekly timer stays disabled to avoid duplicate runs; it was confirmed disabled and inactive on November 25, 2026.
The Mac listener stays disabled.
