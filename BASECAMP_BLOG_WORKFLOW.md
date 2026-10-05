# Basecamp blog workflow

Use only tasks in the verified Geo project and chosen blog list. Required inputs: target keyword, exact /blog/ URL, description or attached brief, and acceptance criteria. Read current comments and attachments; preserve task ownership and source IDs. Completed, reserved or already-reviewed tasks are skipped. API failure must not trigger fallback to another queue.

Setup and weekly --test hold draft-ready comments. Slack is the approval surface. Any optional Basecamp review notice requires a verified project-member reviewer and ready exact-commit preview; the desired notice behavior remains to be clarified. Deduplicate comments by task and draft version and reconcile ambiguous POSTs.

After named-reviewer approval and verified live publication, comment the actual live URL and complete only the corresponding Geo blog task. A preview, PR or approval alone does not complete a task. Existing service optimization, redirects and page consolidation require separate handling; the new-blog queue remains unconfigured.
