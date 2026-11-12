# Geo blog architecture

## Intended flow and current limits

Proposed research row or authorized Basecamp task → queue reservation → company evidence and verified service match → OpenAI draft → deterministic structure checks and AI editor → image → exact-commit website preview and remote Lighthouse → playground staging → Wednesday automatic exact-version publication through production_publish.py → verified live article → Slack live link and authenticated Undo button → source lifecycle updates.

The test-channel listener is deployed. astro_export.py writes the actual Geo posts.js contract and site_preview.py resolves exact-commit Vercel previews with GitHub quality artifacts. The practice sample remains isolated in PR #81. The foundation alone passed staging checks and is deployed to production through PR #87 at f695ec64cd5698723d4eb2475f5d6df78bc0a1f5. The live production verifier now checks the Astro article against approved metadata, FAQ text/schema, image bytes and sitemap. Dedicated runtime credentials and the real playground approval are verified. The service now loads its own mode-0600 runtime configuration with production delivery, website previews and automatic publishing enabled.
Live production publication is verified; Basecamp completion depends on the article source and configured integration.

## Boundaries and state

settings.py loads masked credentials; environment overrides local files. requests.py reserves and deduplicates incoming events before paid work. store.py persists draft states. Missing/failed providers stop the pipeline instead of silently choosing another queue. External pages, comments, briefs and model responses are untrusted data.

company.py reads verified Geo routes. product_fit.py chooses only supplied service pages. Supplied upstream research and keywords guide drafting; technical citation checks do not constitute keyword research. Prompts prevent unsupported claims, hazardous DIY procedures and invented customer results.

content.py applies bounded correction rounds and requires an explicit clean editor verdict. Basecamp briefs preserve stable task IDs, target slugs and acceptance criteria. Existing service-page optimization and consolidation tasks must not enter the new-blog flow automatically.

media_catalog.py validates permitted media and hashes. generated_hero.py respects the paid-image enable flag and labels generated images Illustration:. Real assets, if later supplied, require privacy and owner approval. Every delivered finished blog needs an image.

slack_guard.py checks destinations and formats sentences. Production capabilities additionally require PRODUCTION_DELIVERY_ENABLED, a nonempty production channel and configured approvers. production_review.py checks workspace, channel, card, version and user. production_publish.py requires matching automatic-policy markers and unchanged exact-version evidence before normal GitHub merge rules.
The owner reconfirmed on November 12, 2026 that no Slack approval is required.
Undo requires a configured user, the production workspace/channel/card and the exact deployed article version.
Test review does not arm publication.

## Hosting

Only geo-blog services, /opt/geo-blog, /etc/geo-blog and /var/lib/geo-blog may be provisioned on the shared host. Do not modify other tenants or host-wide settings. Website builds and Lighthouse run remotely on exact commits. Wednesday 09:00 America/Los_Angeles is confirmed for production channel C0C9NUA9BG9. scheduled_preview.py --production calls the guarded weekly automatic-publication flow, validates runtime prerequisites and records one attempt per Pacific date before external work. The default mode stays playground-only. The geo-blog user crontab is installed and verified; the systemd weekly timer remains disabled to avoid duplicate runs.
Cron runs at both 16:00 and 17:00 UTC with the Pacific time gate selecting only 09:00, including daylight saving changes.
Server state is authoritative.

## Independent local review

local_workflow.py builds from an explicit local brief using the same writer, editor and image pipeline; it never selects or modifies a remote queue. local_preview.py checks saved approved prose, current structure, image provenance and FAQ syntax before exporting static HTML plus portable Markdown/JSON. Brand assets are packaged locally. The preview library is served only from its output directory on loopback. No website checkout, Vercel deployment or production approval is implied.
