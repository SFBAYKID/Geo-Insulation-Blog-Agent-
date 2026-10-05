# Geo weekly schedule

Chase confirmed Thursday at 07:00 America/Los_Angeles. deploy/geo-blog-weekly.timer uses that IANA time zone, preserving local time across DST, with Persistent=true. This is separate from the other tenant's weekly job.

Timer stays disabled until weekly --test produces a finished blog in C0B02721MNK with a clean editor result, image, checked preview, exact-commit CI and Lighthouse scores. Require Performance >=90 and Accessibility, Best Practices and SEO 100. Verify memory and no other active publication before enabling. Missed-run behavior must not overwrite an open review.

Production channel and approvers remain unconfigured. No schedule or listener has been enabled. Optional keyword sync is proposed at 06:30 Pacific and requires its own source/config verification.
