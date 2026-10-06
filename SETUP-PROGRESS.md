# Geo Insulation blog agent setup

Updated October 5, 2026. Test-channel listener deployed and connected on the droplet; full workflow setup remains incomplete.

Current provider decision: OpenAI only for writing, editing, chat and imagery. Earlier Claude/Anthropic credential requirements below are superseded. Dedicated Slack app and droplet listener are installed and verified; full end-to-end test remains pending.

## Confirmed by Chase

- Website: https://geo-insulation.com/.
- Slack test channel: C0B02721MNK, monarch-bot-playground.
- Approval happens in Slack. Exact approver identities remain to be verified; Chase is the proposed initial approver.
- Basecamp account URL: https://app.basecamp.com/5395893/. Project and brief list require discovery.
- Create a separate Geo Airtable base/table for keywords.
- Create a Google Cloud keyword worker; Drive source link and Google access are pending.
- Use OpenAI API generated illustrations for blog imagery, labeled Illustration: and never represented as customer work.
- Weekly schedule: Thursday 7 AM America/Los_Angeles, confirmed. Timer must stay disabled until the full clean test passes.
- Basecamp completion after verified publication is part of the originally requested workflow. Draft-ready comment behavior needs clarification of the latest spoken instruction.

## Setup boundaries

Use an isolated geo-blog user, folders and services on the shared droplet 143.110.146.87. Do not modify other applications or credentials. All testing stays in the test channel. No production membership changes or posts during setup. Production channel remains unconfigured. Setup never publishes. Only exact-version named-approver approval may publish through production_publish.py.

Never copy another client's runtime state, approvals, credentials, imagery or business claims. Preserve ambiguous send/merge evidence and reconcile before retrying. No local Slack listener. Run website builds and Lighthouse remotely, not on the shared small droplet.

## Standing memories to implement

1. Only finished blogs that passed structural and AI editor checks may reach the client; failures go to the test channel.
2. Every delivered blog requires an image. Generated images must be explicitly labeled illustrations.
3. Chase does not perform manual fixes. The agent handles repair, safe reservation recovery and reruns.
4. Topic and keyword research is supplied upstream; describe this agent as writing, editing and publishing from briefs.
5. Every Slack sentence goes on its own line through the central outbound guard.
6. Each external source URL is linked once per article. FAQ content follows the explicit accordion contract; no HTML attributes in Markdown.

## Verified access observations

- Original starter instructions, referenced docs and memory rules were read in the prior session.
- On October 4 the Desktop starter folder/zip and original project directory were absent. A targeted home-directory filename search to depth five found no starter package.
- The separate ~/geo-insulation-blog-agent folder contains only IDE files, a venv and a starter main.py. It was not changed.
- GitHub CLI repository-name lookup returned no Geo/insulation match among the first 100 repositories. Connector search also did not identify the website repository; this is not proof no repository exists.
- Homepage fetched successfully with curl.
- Browser automation failed to initialize, so no Basecamp, Google Cloud, Slack app or Airtable browser login was attempted.
- No Slack messages, Basecamp changes, Airtable changes or droplet actions performed.

## Next inputs

- Location of the moved starter folder/zip, or attach the zip again.
- Drive keyword source link.
- Project path containing reusable Basecamp OAuth application credentials. Do not paste secrets in chat.
- Website GitHub repository and Vercel project, if this website is backed by those services.
- Production Slack channel and approver identities before production activation.

Fresh credentials and state will be created for Geo. All implementation, verification and deployment phases remain pending source recovery.

## Latest recovery and keyword access update

Chase authorized checking Trash for the missing starter package and requesting the keyword source from its owner. Keywords are currently inaccessible to Chase; do not create a worker against a guessed source. Basecamp credentials will be supplied by Chase.

Trash lookup failed with macOS Operation not permitted, including an elevated retry. The source package still requires restoration or attachment. Recommended agent repository name: SFBAYKID/Geo-Insulation-Blog-Agent; website repository remains unidentified. An attempt to request keyword access through Slack was rejected by the connector because the destination is externally shared; no message was sent.

## Verified Basecamp discovery and supplied repository

- Agent repository supplied by Chase: git@github.com:SFBAYKID/Geo-Insulation-Blog-Agent-.git (trailing hyphen is intentional). Keep WEBSITE_REPOSITORY blank until the website repo is verified.
- Supplied credential file located at ~/geo-insulation-blog-agent/.env. Its values were never printed or changed. Used only for authorized setup discovery; not imported as permanent runtime credentials.
- OAuth refresh succeeded; fresh access token saved privately in ignored storage. No replacement refresh token was returned. Separate Geo authorization remains pending.
- Account 5395893 contains verified Geo Insulation project 40851698. Local .env.local now records those identifiers.
- Blog Work list 8271007254 has no open tasks. Content Sprint list 10345058686 has five existing-page optimization tasks, including one blog consolidation/redirect task. It is not verified as a new weekly-blog queue; BASECAMP_TODOLIST_ID remains blank. No Basecamp writes occurred.
- Current briefs identify an Astro website and staging branch, not the inherited website schema. Integration must adapt to the actual website rather than copy the prior site exporter unchanged.

## Source recovery and local adaptation — October 4

Starter folder and zip restored to Desktop and read. Source copied into this workspace as geo_blog; old history, website reference, photos, approvals and credentials excluded. Rewrote Geo instructions, architecture, operations docs, prompts and six .claude/memory files. Cleared media/task catalogs. Updated service routes from Geo homepage, reviewer checks to installation configuration, and production delivery to an explicit default-off gate. Shared-host bootstrap no longer installs browser software or changes host-wide settings. Weekly timer template is Thursday 07:00 Pacific, still not installed.

Offline checks: 302 tests passed, Ruff and formatting passed, mypy passed, source limits passed. Doctor reports missing runtime connections and unverified Astro integration; this is not an end-to-end pass. No paid draft, image generation, Slack post, Basecamp mutation or server deployment ran. New regression tests cover default-off production, removed-reviewer approval rejection, disabled-image spending and actual Geo service URLs.

Browser access is now available. Dedicated Geo blog Slack manifest is prepared on the Create review screen in Monarch with app_mentions:read and chat:write, app_mention event, Socket Mode and interactivity. Creation/installation and connections:write token generation await action-time access confirmation. Existing technical-agent app left untouched.

Remaining: Geo Slack app/credentials and reviewer verification; separate Basecamp runtime authorization and new-blog briefs; keyword source access and Geo Airtable/Google worker; Claude/OpenAI keys and model access checks; actual Astro website repo/Vercel integration; icon; all live checks; isolated droplet deployment and test run. Production channel remains unset.

## Slack and repository milestone

Baseline commit 9159ab1 pushed to the supplied agent repository main branch. No secrets or private setup evidence included. After Chase confirmed app access, created/installed dedicated Slack app A0C6QMK0PT3 in workspace T01DFJLFKE3, bot U0C7H50485N. auth.test passed and temporary Socket Mode WebSocket connected, then closed. Invited the new bot only to test channel C0B02721MNK. Verified Chase U01DPJVURHU as initial test reviewer. Private local credentials are now saved; source-supplied Basecamp credentials remain untouched. No local listener remains running. Slack icon and full draft test remain pending.

## OpenAI credential verified

Chase supplied the OpenAI key in the separate Geo blog folder under OPEN_AI_KEY. Copied only that newly supplied key into this workspace's ignored, mode-0600 .env.local as OPENAI_API_KEY; source file and Basecamp credentials left unchanged. Read-only OpenAI model discovery returned HTTP 200 and listed the configured gpt-image-2.5-flare-2026-09-08 model. No paid generation was performed; image generation and billing remain untested. Claude writing/editing key is still missing.

## OpenAI-only provider migration

Chase explicitly declined Anthropic and selected OpenAI for the entire model workflow. Replaced the Anthropic transport and dependency with OpenAI Responses requests, neutral saved response types and OpenAI usage estimates. Default text model: gpt-6-luna; existing OpenAI illustration model retained. Removed Anthropic requirements from doctor, CLI, nightly preflight and the local credential form. Updated offline audit batches and cache diagnostics. Stable prompts, domain-filtered source evidence, strict editor JSON, refusal/truncation gates, persistent spending reservations and test-only Slack boundaries remain enforced. Fixed inherited non-insulation wording in the research prompt.

Live provider verification passed five bounded calls: short homeowner prose, strict editor approval JSON, official-domain research citations, a read-only tool call and its reply. No Slack messages, images, Basecamp writes, publication or deployment occurred. Prompt-free private usage receipts saved in ignored storage. This verifies transport and access, not full article quality or the end-to-end publishing workflow.

Verification after migration: 315 offline tests passed; Ruff lint/format, mypy and source limits passed. Five live text-provider checks passed, with a conservative usage estimate of $0.011211 total. Doctor now requires only OpenAI for models and correctly remains incomplete for separate Basecamp runtime authorization, queue selection and Astro website integration.

## Local preview workflow and first illustrated article

Chase authorized local article production and visual review while website GitHub/Vercel access is unavailable. Inspected Geo’s live blog index and a current article; recorded green/blue/neutral color tokens, Bitter headings and Hind Siliguri body fonts in LOCAL_PREVIEW.md. Built a self-contained responsive article template, draft library, FAQ accordion/schema and downloadable Markdown/content JSON. Logo and font files are local with source records and font licenses.

Added local-draft (resume by brief fingerprint, no queue/Slack/publication) and local-preview (render saved approved prose/image without provider calls). The first sample uses config/practice-brief.json: Attic Insulation and Home Insulation Planning, 920 words. All structural and AI editor checks passed; generated a 1536×864 labeled OpenAI illustration. Source evidence and provider receipts remain private in storage/local-design-sample. Preview library: previews/index.html; article: previews/attic-insulation-planning-san-antonio/index.html. Loopback preview server uses 127.0.0.1:8766 and serves only previews.

Desktop 1728px and phone 390px visual checks passed, including all local images/fonts, no horizontal overflow, mobile navigation, library links and FAQ interaction/schema agreement. Fixed a mobile library thumbnail sizing issue during review. All 324 offline tests, Ruff, formatting, mypy and source checks passed. No Slack message, Basecamp mutation, website deployment or droplet change occurred. Publishing integration and its end-to-end test remain pending real website access.

## Droplet SSH bootstrap pending

Chase requested connection to 143.110.146.87. Read-only SSH reached the host with strict host-key checking, but root authentication failed with publickey denial. No remote changes occurred. Generated a dedicated local Ed25519 key in ignored storage/ssh/geo-blog-ed25519 (private mode 0600); supplied its public key for a one-time geo-blog account bootstrap in Chase's existing droplet terminal. SSH installation is not verified. Do not use the unrelated monarch SSH alias, which resolves to a different host.

## Droplet account and runtime staged — October 5

Chase ran the Geo SSH bootstrap. Dedicated geo-blog login verified (uid/gid 1000), home /var/lib/geo-blog, no sudo privileges. Ubuntu Python 3.12.3, about 2GB RAM and 1.3GB available during installation; /opt/geo-blog was empty. Uploaded reviewed code without local storage, previews or credentials in the archive, created /opt/geo-blog/.venv, installed pinned isolated dependencies and passed pip check plus all 324 offline tests on the server. No host packages, firewall, swap or other tenants were changed.

Geo Slack/OpenAI credentials are staged as /var/lib/geo-blog/agent.env.pending, geo-blog-owned mode 0600. Other-customer Basecamp secrets were explicitly excluded. Verified Slack workspace/bot identity, OpenAI writer/image model access and temporary Socket Mode connection, then closed it without messages. Storage is /var/lib/geo-blog/storage; production/publishing/schedule/site preview flags are disabled. Local Slack listener remains off. DEPLOYMENT_TARGET=droplet enables accurate server status reporting.

Reviewed resource-limited Geo units and deploy/install-geo-services.sh are staged; systemd-analyze verify passed. No Geo systemd units are installed yet. Chase must run the prepared installer in the existing root terminal because the isolated Geo account has no admin rights. It installs only /etc/geo-blog and named Geo units, starts the test-channel listener, and leaves the Thursday weekly timer disabled. Permanent listener activation remains unverified until that command runs.


## Permanent listener verified, October 5, 2026

Chase successfully ran the root installer.
Read-back verification confirms geo-blog.service is enabled, active and running, with zero restarts and about 35 MB memory usage at inspection.
Slack startup logs confirm an established Socket Mode session receiving messages.
Runtime configuration restricts delivery to C0B02721MNK, monarch-bot-playground.
Production delivery, publishing, scheduling and website previews remain disabled.
geo-blog-weekly.timer is installed, disabled and inactive.
The Mac listener remains off.
This verifies service deployment and Slack connectivity, not a complete draft/approval/publication test.


## Slack branding completed, October 5, 2026

Uploaded and saved the Geo company logo adapted to Slack's square icon format in app A0C6QMK0PT3.
Source: geo_blog/preview_assets/geo-logo.webp, from Geo's website.
Square image adaptation is saved at config/geo-slack-icon.png.
Set and saved the app background to website green #2e7d46; updated the manifest to match.
Verified the logo in the app preview and the completed Save Changes state.


## Website access verified, October 5, 2026

Chase supplied the Vercel project URL; signed-in dashboard access succeeds.
Project geo-insulation, ID prj_BCWJ5JbWhI4zqrn56wMIKp1H9AFy, is connected to Calvo-Consulting/geo-insulation.
Production deploys main to https://geo-insulation.com/; staging has a separate Ready preview.
GitHub SSH read access and isolated reference clone succeeded at storage/website-reference.
Both remote branch heads matched 5f435fcca4b705736c614a2c5042ad3662c2c79a at inspection.
Blog data lives in src/data/posts.js and renders through src/pages/blog/[slug].astro.
Existing repository workflows deploy main and fast-forward staging when safe.
No website changes or deployments were made.
The remaining publishing work is adapting the exporter, configuring runtime GitHub access, and verifying exact-commit previews and quality checks.
Verified project URLs are stored in config/website-discovery.json.

Chase also supplied production Slack channel C0BP597644B and explicitly approved one introductory message.
The dedicated bot was added and posted that introduction, receipt 1791227040.024119.
This one-message exception did not enable persistent production delivery or scheduled publishing.


## Astro preview integration and Slack test card

Implemented the actual src/data/posts.js exporter, preserving existing articles and escaping authored HTML.
Export refuses duplicate slugs, image overwrites, altered image hashes, invalid dimensions and unlabeled generated illustrations.
Website draft PR: https://github.com/Calvo-Consulting/geo-insulation/pull/81, targeting staging.
It includes the existing approved practice article, matching FAQ accordion/schema, metadata overrides only for agent articles, mobile hero rendition and deferred footer reviews on agent articles.
No merge to staging or main occurred.
The authoritative final tested website commit is 7cca3edbe04a8b5e5f4ff36d449d7549ca808df3.
GitHub run 37420600899 passed the build, all 133 pages' existing SEO checks, rendered article checks and three-run mobile Lighthouse medians: performance 90, accessibility 100, best practices 100, SEO 100.
Remote artifact validation now rejects a mismatched commit and an older successful workflow superseded by a failed run.
Agent validation: 331 tests passed, Ruff lint/format, mypy and source limits passed.
Hosted preview: https://geo-insulation-9d6heyhp2-pablo-calvo-s-projects.vercel.app/blog/attic-insulation-planning-san-antonio/
Vercel authentication is required; Chase's existing signed-in browser opened it successfully.
Desktop and 390px mobile rendering inspected; mobile image selected, no horizontal overflow, FAQ expansion passed.
Posted test review from the server's Geo bot only to C0B02721MNK, message 1791268378.637799.
Server draft ID integration-preview-20261005 is pending human approval; no approval click has been simulated.
Sample uses supplied practice content rather than a Basecamp publication assignment.
Publishing, production delivery and the weekly timer remain disabled.
The website foundation still needs review/merge, server GitHub runtime credentials still need configuration, and the separate production-publication adapter still needs reconciliation with Astro before any production activation.


## Wednesday schedule preparation, October 6

Chase requested Wednesday 9 AM Pacific and explicitly prohibited production-channel posts.
Asked whether scheduled drafts should go only to the playground or remain disabled; that destination decision is pending.
Prepared updated systemd templates plus a geo-blog-only cron template; neither scheduler was activated.
The cron template uses both 16:00 and 17:00 UTC on Wednesday with a Python America/Los_Angeles gate, so only 09:00 Pacific runs.
Added scheduled_preview.py with one durable date receipt, an overlap lock, no automatic retries after an ambiguous attempt, test-channel enforcement and prerequisite checks before provider work.
Staged only that module in the Geo server code directory; it is inert without a scheduler.
Read-only server inspection confirms Basecamp runtime credentials/list, Airtable source and website runtime configuration are missing; production delivery and publication are off.
The sample remains pending in the server review store; Chase praised the design here but no Slack click was simulated.
338 offline tests passed, along with Ruff, formatting, mypy and source checks.
This is schedule preparation, not completed production activation; source briefs, runtime access, destination confirmation and production integration checks remain outstanding.


## Production authorization and adapter repair, October 6

Chase explicitly authorized production destination C0BP597644B, Basecamp connection, publishing integration and Wednesday 09:00 Pacific scheduling.
This supersedes the prior destination question; do not ask it again.
Added an explicit production scheduler mode with separate durable receipts and the existing guarded weekly flow.
Publishing now requires both production delivery and publication enable flags.
Weekly sharing rejects staging or foreign-repository PRs before Slack calls.
Replaced the inherited live-site script with an Astro verifier for approved metadata, FAQ content/schema, image hash/label and sitemap inclusion.
The practice article remains in unmerged draft PR #81 against staging and is not a production assignment.
Server verification: geo-blog.service active, no GitHub authorization, and no geo-blog crontab.
Chase approved and the dedicated Basecamp application was registered as integration 30193 under Monarch. Credentials have not yet been saved and OAuth authorization has not completed: the local callback browser permission was denied. No other customer credential was reused.
GitHub browser permission was denied; no alternate route was used to create that authorization.
Runtime credentials, a clean complete playground workflow, website foundation rollout and final server activation remain outstanding.
No production post or publication occurred during these changes.

Agent commit 8e25ffa is pushed and staged at /opt/geo-blog/releases/8e25ffa.
All 351 offline tests passed locally and on the droplet; local Ruff, formatting, mypy and source checks passed.
The existing listener was not replaced and no scheduler was enabled.
Browser access to github.com, app.basecamp.com and the local OAuth callback was denied by permission review; further connection work awaits restored permissions.


## Dedicated Basecamp authorization verified, October 6

Chase completed OAuth authorization for Geo application 30193.
Corrected the newly supplied secret from the separate Geo setup file without changing older customer entries.
Fresh Geo access and refresh tokens are saved privately; refresh and project access passed through the runtime Basecamp adapter.
Verified account 5395893 and Geo project 40851698.
The old Blog Work list 8271007254 now returns 404 and is absent from active and archived lists.
Created and verified a separate internal Geo Blog Briefs list 10378779880 in the Geo project, leaving existing task lists untouched.
The new queue contains zero briefs; no topics or publishing assignments were invented.
Staged the fresh configuration at /var/lib/geo-blog/agent.env.pending (0600) and verified its Basecamp access from the droplet.
The running service still uses its earlier root-owned configuration; staged credentials are not yet active in that listener.
Production flags and scheduler remain off pending GitHub runtime authorization, website foundation rollout and complete playground verification.


## GitHub fine-grained token and Actions permission, October 6

Chase supplied a new token under GIT_HUB_FINE in the separate Geo setup env file.
Saved it privately as GITHUB_TOKEN in the active local config and staged it in Geo's pending server config.
GitHub /user authenticates as SFBAYKID (HTTP 200), but all website-repository probes return HTTP 404, including metadata, pull requests, Actions and deployments.
Organization approval or repository authorization remains unresolved; asked whether the token shows Pending approval.
Checks permission is not required: the active quality gate now reads the named blog-quality workflow and the Lighthouse job from its latest exact-commit attempt through the Actions API.
Wrong commits, unrelated workflows, failed newer runs and missing/skipped jobs fail closed; artifact quality and GitHub merge protection checks remain in place.
351 offline tests, Ruff, mypy and source checks pass.
