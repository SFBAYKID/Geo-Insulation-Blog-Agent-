# Geo Insulation agent instructions

Follow AGENTS.md. Read README.md for commands, ARCHITECTURE.md for system behavior, IMAGE_CATALOG_CONTRACT.md for media requirements, and .claude/memory/MEMORY.md for standing preferences.

Write homeowner-focused insulation content for Geo Insulation, https://geo-insulation.com/, in San Antonio, Texas. Fetch actual company pages for every business claim. Service area, prices, savings, warranties, rebates and capabilities must never be inferred from keywords. Current Basecamp briefs state residential-only and no cost-led content; resolve conflicting business claims from current approved evidence before drafting.

Test channel: C0B02721MNK. Production channel remains unset; Chase is the configured test reviewer. Thursday 7 AM America/Los_Angeles is confirmed, but the timer stays disabled pending a clean test. Local listener stays disabled.

Only a named configured Slack approver can approve an exact commit for production_publish.py. Test approval is editorial only. Setup never publishes. Finished blogs require all structural/editor checks, labeled imagery, a checked preview and exact-commit quality results. Failures go only to the test channel.

Each external source URL appears as a link once per article. Use the explicit FAQ section contract, exported to one accordion and matching schema. External link attributes belong in the website renderer, never Markdown.

Use typed focused modules and docstrings. Aim below 600 lines; split before 800. Run all documented local checks. Distinguish inherited implementation from verified Geo behavior. Do not claim a service, integration or photo is approved or running without evidence.

Use OpenAI only for the runtime. No Anthropic credential is required; see .claude/memory/openai-only.md.
