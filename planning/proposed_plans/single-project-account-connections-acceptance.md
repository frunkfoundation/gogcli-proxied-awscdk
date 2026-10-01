# Single-application account connections: acceptance evidence

Date: 2026-09-30

Status: Implementation, canonical client provisioning, production deployment, and read-only runtime verification complete. Real Google account enrollment remains pending.

## Delivered implementation

- Sole hosted application: `gogcli-proxy`, project number `1042630585914`, organization `kraecollective.com`.
- Account-only page: services, direct Connect, connection evidence, recovery, access controls, disconnect, and Add accounts.
- Removed project selectors, setup instructions, client upload, API/publishing forms, shared/default assignment, and page CLI configuration.
- Singular server-side application resolution; no account seeds or default-project mutation. Fresh project-bearing requests fail closed; exact accepted requests remain replayable.
- Operator-only Web-client import/review through `just configure-google-application`, with explicit source/target, validation, immutable revisions, conditional review, and nonsecret output.
- Previous installed credentials, paused access, callback/recovery references, and restrictive-command fences preserved.
- Removed real obsolete hosted project/client identifiers from the current source/configuration/documentation tree. Earlier plans retain truthful evidence and valid stabilization invariants.

## Checks passed

| Check | Result |
| --- | --- |
| `just proxy-test` | 136 Lambda tests passed |
| `just operator-test` | 8 operator tests passed |
| Infrastructure contracts | 8 passed; singular binding Red → Green |
| Standalone named browser journey | Passed account-only rendering, eight cards, service drafts, readiness isolation, paused access, recovery, callback focus, keyboard, and 390px layout |
| Real-handler named browser journey | Passed direct canonical consent, continuation/restart/denial, actual callback focus, saved authorization retry without another exchange, successful paused previous-app replacement, Calendar-only consent/probes, and restrictive supersession |
| Connection progress | Callback and credential promotion did not increment completion until actual named service probes passed |
| Linux Lambda payload | Python 3.11 x86_64 wheels and final UI assets verified |
| Documentation | `make docs-check` passed: 759 command pages, 28 feature pages |
| OpenAPI | 3.1 schema validation passed; owner contract version 3.0.0 |
| Syntax/diff | JavaScript syntax and whitespace checks passed |
| Independent integration review | No remaining material backend/operator/HTTP concerns; identified count, paused-access rendering, and runbook issues corrected |
| CDK deployment review | Only both Lambda code packages and singular onboarding environment settings change; no new resources or replacements |

Browser Google navigation was intercepted in named offline sessions and asserted an `offlineGoogle` marker. No real consent, token exchange, or Google content mutation occurred during these tests.

## Production baseline and remaining release steps

The implementation-turn read-only metadata check found eight saved accounts, one existing previous-application connection, zero canonical connections, zero active account operations, zero usable saved recovery, and no canonical client record. This is an observation at that check, not a live enrollment counter.

Production target remains `gogcli-prd`, account `968138089939`, region `us-east-2`, stack `GogcliGoogleProxyApi`, profile `krae-jdev-dev-prd`.

Release completed from canonical source commit `67121f96` to the target above. The newest Web-client JSON in Downloads on `cocos` passed project, endpoint, and exact callback validation and was imported through the operator command. Gmail/Drive are confirmed ready; Calendar API enablement remains unobserved and publishing status is recorded as `unknown`.

The pre-deployment check found eight accounts, no active account operations, and zero canonical installed connections. CDK changed only both Lambda packages and singular onboarding environment variables. The stack reached `UPDATE_COMPLETE`; both Lambdas are Active with successful updates. Deployed ZIP hashes and 21 committed source/assets/requirements files match. Six invoke grants and permanent callbacks are unchanged. Live source setup reads preserve eight accounts and the configured singular application. Public assets match exactly with `no-store`; owner API/authentication guards pass.

Remaining steps:

1. Observe Calendar API enablement in the canonical Google project and update operator readiness when confirmed.
2. Reconnect the personal account through the live page, verify selected-service checks and authorized caller access, then continue the remaining accounts.

Published account URL after release: https://gp-039168171bf05570eadba6a8.k-rae.com/prd/onboarding/

The account-only implementation is now the live page. Existing installed account credentials remain preserved; software deployment has not enrolled the eight accounts. No Google content mutation or real consent was performed by release verification.
