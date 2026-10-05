# Exact-version publication

Setup never publishes. PRODUCTION_DELIVERY_ENABLED=false until end-to-end test success and explicit production destination/reviewers are configured. The website repo is separate from the agent repo and remains unknown.

Only production_publish.py consumes an exact-version approval by a configured reviewer. Validate matching task/draft, commit, card, current feedback and unchanged checkout. Require exact-commit CI, approved imagery and normal GitHub merge rules without administrative bypass. Wait for the successful production deployment of the merged commit and verify actual article, media integrity, metadata, canonical, schema, sitemap and indexability before reporting success.

Then post the verified live URL on the matching Basecamp task and complete it. A failure after merge must reconcile reality before retrying; retain receipts and do not erase publication evidence after notification failure. Playground Approve records editorial review only.

The inherited website adapters need Geo Astro integration before any preview or publication. The current placeholder repository prevents using the prior website. Current Basecamp briefs mention staging; branch and Vercel mapping must be verified.
