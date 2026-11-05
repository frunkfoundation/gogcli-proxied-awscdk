# Cross-profile account connections: acceptance evidence

Status: implementation, local verification, production deployment, and independent read-only runtime verification complete.

The isolated feature provides owner-generated, account-specific connection links. A target browser profile claims only the frozen operation, approves its matching Google account, and sees its result without an Authentik owner login. Owner management controls remain protected.

Links use the permanent onboarding address with a secret fragment, removed immediately from browser history. First claim binds a Secure/HttpOnly/SameSite=Lax per-operation cookie. Generation fences invalidate older links on owner replacement, pause, disconnect, or cancellation. Existing PKCE, nonce, verified email/prior subject/scope validation, staged credentials, saved-response recovery, and exchange-once behavior remain shared.

## Local checks

- 160 Lambda behavior tests passed, including new link lifecycle, scoped HTTP, and actual Store serialization/precondition contracts. Focused regressions first failed for missing link behavior and delayed preparation retention, then passed.
- Named offline cross-profile browser journey passed with isolated owner and two target profiles: no owner cookies or Authentik visits; canonical scopes/account; fragment scrub and no storage/referrer leak; authorizing reopen; repeated same-profile claim; wrong-cookie and callback replay without another exchange; two successful worker flows; cross-operation denial; wrong-account credential preservation; pause/disconnect fencing; same-tab replacement link regression.
- Both original rendered owner-page and real-handler owner journeys passed.
- Eight synthesized infrastructure contracts and the Linux Python 3.11 x86_64 payload check passed, including new modules and connection assets.
- OpenAPI 3.1 validates; docs coverage passes (759 command pages, 28 feature pages); JavaScript syntax and whitespace checks pass.
- Read-only API execution-log audit inspected 1,385 events and found no request-query/header/body log lines with data tracing disabled. No resource, IAM, hostname, callback, or logging configuration changes are needed.

Google navigation was intercepted and marked offline. No real Google consent, Google content mutation, or production connection link was created to verify this feature.

## Release

Source: isolated feature worktree, integrated committed code to `/Users/jay/Projects/gogcli-proxy`; deployed commit `5893406bf97181ba01cec54775296da4bbb86bb7`.
Target: `gogcli-prd`, account `968138089939`, region `us-east-2`, stack `GogcliGoogleProxyApi`, profile `krae-jdev-dev-prd`.

Page remains https://gp-039168171bf05570eadba6a8.k-rae.com/prd/onboarding/ . The permanent Google callback remains unchanged.

After release, the user generates a link from an account's Connection options and opens it in that account's Google browser profile. Software release and real account enrollment are separate outcomes.

## Production verification

Deployment completed on 2026-09-30. The stack is `UPDATE_COMPLETE`; both Lambda functions are `Active` with successful updates. Both deployed ZIP SHA256 hashes and all 28 committed source/assets match the release commit. Only the two Lambda code packages changed.

Independent read-only checks confirmed:

- All six existing invoke ARNs and the permanent Google callback are unchanged.
- The single canonical Google application is configured, with Gmail, Drive, and Calendar APIs confirmed ready.
- All eight accounts remain: one verified connection using the canonical application, seven not connected, and no active operations.
- All six public pages/assets match committed source and retain no-store, no-referrer, and CSP headers.
- Unauthenticated owner setup returns 401 with the owner login URL; scoped operation access returns 401 without an owner login URL.
- An unknown-state callback redirects to a clean public result without echoing the supplied code.

Evidence: `/tmp/gogcli-cross-profile-deploy.log` and `/tmp/gogcli-cross-profile-runtime-verify-results.b1b6bvlk.log`. Verification performed only production metadata reads and HTTP GETs; no production link or Google consent was created.

## Usability release

Source commit `e405ea51` (owner/connection page controls) deployed 2026-10-01 05:11 UTC from `/Users/jay/Projects/gogcli-proxy` to `gogcli-prd` (`968138089939`, `us-east-2`, `GogcliGoogleProxyApi`, profile `krae-jdev-dev-prd`). The pre-deploy diff changed only the two Lambda code packages; `access.json` outputs are unchanged. The stack is `UPDATE_COMPLETE` and both functions are `Active`/`Successful`.

Read-only checks: all seven public pages/assets match the commit byte-for-byte with no-store, no-referrer, and CSP headers. Unauthenticated owner setup returns 401 with a login URL. The live connect page runs the new script. Evidence: `/tmp/gogcli-ui-usability-deploy.1BDgdL`. No production link, consent, or account change was made; the authenticated owner page was not exercised.
