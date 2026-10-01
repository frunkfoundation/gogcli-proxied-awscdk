# Cross-profile account connections: acceptance evidence

Status: implementation and local verification complete; production release pending.

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

Source: isolated feature worktree, integrated committed code to `/Users/jay/Projects/gogcli-proxy`.
Target: `gogcli-prd`, account `968138089939`, region `us-east-2`, stack `GogcliGoogleProxyApi`, profile `krae-jdev-dev-prd`.

Page remains https://gp-039168171bf05570eadba6a8.k-rae.com/prd/onboarding/ . The permanent Google callback remains unchanged.

After release, the user generates a link from an account's Connection options and opens it in that account's Google browser profile. Software release and real account enrollment are separate outcomes.
