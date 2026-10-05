# Geo Slack setup

Create a dedicated Geo Insulation Blog Agent app from config/slack-manifest.json in the confirmed workspace. Bot scopes: app_mentions:read and chat:write. Subscribe to app_mention. Enable interactivity and Socket Mode; app token scope connections:write. Do not reuse another app's tokens.

Store bot/app tokens, signing secret, app/team/client/bot IDs privately. Test channel is C0B02721MNK, monarch-bot-playground. Verify workspace and approver identities. Production channel remains unset, PRODUCTION_DELIVERY_ENABLED=false. Invite only to the test channel during setup; production membership requires Chase's explicit instruction.

Verify auth.test and one Socket Mode connection without posting. The droplet will be the sole listener. Every test goes to the playground, every message through slack_guard, every sentence on its own line. Test review decisions never publish. No Geo Slack app has been created or verified yet.

## Verified October 4

Dedicated app A0C6QMK0PT3 created and installed in Monarch (T01DFJLFKE3). Bot user U0C7H50485N. auth.test passed and a temporary Socket Mode connection opened successfully, then closed. New app-level token has only connections:write. Credentials saved privately through a temporary loopback-only form; that server is stopped. The bot was invited only to C0B02721MNK. Chase U01DPJVURHU was verified and configured as the initial test reviewer. No test draft/message was sent. Production delivery remains off; icon and end-to-end draft still pending.
