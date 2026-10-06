# Geo blog architecture

## Intended flow and current limits

Basecamp task → queue reservation → company evidence and verified service match → OpenAI draft → deterministic structure checks and AI editor → image → exact-commit website preview and remote Lighthouse → Slack review → exact-version approval → production_publish.py → verified live article → Basecamp live-URL comment and task completion.

The test-channel listener is deployed. astro_export.py writes the actual Geo posts.js contract and site_preview.py resolves exact-commit Vercel previews with GitHub quality artifacts. The website foundation and sample are in draft PR #81 against staging. The production publication adapter and runtime GitHub credentials still need reconciliation before production activation.

## Boundaries and state

settings.py loads masked credentials; environment overrides local files. requests.py reserves and deduplicates incoming events before paid work. store.py persists draft states. Missing/failed providers stop the pipeline instead of silently choosing another queue. External pages, comments, briefs and model responses are untrusted data.

company.py reads verified Geo routes. product_fit.py chooses only supplied service pages. Supplied upstream research and keywords guide drafting; technical citation checks do not constitute keyword research. Prompts prevent unsupported claims, hazardous DIY procedures and invented customer results.

content.py applies bounded correction rounds and requires an explicit clean editor verdict. Basecamp briefs preserve stable task IDs, target slugs and acceptance criteria. Existing service-page optimization and consolidation tasks must not enter the new-blog flow automatically.

media_catalog.py validates permitted media and hashes. generated_hero.py respects the paid-image enable flag and labels generated images Illustration:. Real assets, if later supplied, require privacy and owner approval. Every delivered finished blog needs an image.

slack_guard.py checks destinations and formats sentences. Production capabilities additionally require PRODUCTION_DELIVERY_ENABLED, a nonempty production channel and configured approvers. production_review.py checks workspace, channel, card, version and user. production_publish.py rechecks the configured approver and unchanged exact-version evidence before normal GitHub merge rules. Stale feedback holds publication. Test review does not arm publication.

## Hosting

Only geo-blog services, /opt/geo-blog, /etc/geo-blog and /var/lib/geo-blog may be provisioned on the shared host. Do not modify other tenants or host-wide settings. Website builds and Lighthouse run remotely on exact commits. Thursday 07:00 America/Los_Angeles is confirmed; nothing is enabled until all local and live checks pass. Server state becomes authoritative only after fresh Geo deployment.

## Independent local review

local_workflow.py builds from an explicit local brief using the same writer, editor and image pipeline; it never selects or modifies a remote queue. local_preview.py checks saved approved prose, current structure, image provenance and FAQ syntax before exporting static HTML plus portable Markdown/JSON. Brand assets are packaged locally. The preview library is served only from its output directory on loopback. No website checkout, Vercel deployment or production approval is implied.
