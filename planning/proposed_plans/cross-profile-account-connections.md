# Proposed: account connection links across browser profiles

Status: implemented, integrated, deployed, and independently verified in production. Deployed source: `5893406bf97181ba01cec54775296da4bbb86bb7`. Real Google enrollment remains a user consent step; one account is connected and seven remain.
Base: `4593f9dcbc410a76acd8864f316fa61a8af420db`.
Worktree: `/Users/jay/Projects/worktrees/gogcli-proxy/cross-profile-connections`.
Branch: `feat/cross-profile-account-connections`.

## Outcome and authority

Jay manages eight Google accounts in separate browser profiles. His management profile has an owner session from `auth.k-rae.com`; the Google profiles do not. From the authenticated account dashboard he generates and copies a connection link for one saved account, opens it in that account's Google profile, confirms the account and services, approves Google consent, and sees only that connection's result. The owner dashboard observes the same persisted operation. The target profile never receives an owner session and never visits Authentik.

A connection link is a narrowly scoped bearer capability, not general owner authentication. It authorizes one frozen enrollment operation for one owner/account/services/client revision and account generation. It cannot list accounts, change services, enable tool access, change caller policy, disconnect, configure clients, or create another enrollment. Existing owner controls retain OwnerAuth and CSRF/Origin checks; signed proxy routes retain IAM and existing policy checks. The capability delegates Google consent only. Any resulting credential/policy installation uses the authority already approved by the owner when minting, through existing effect fencing.

Use the permanent existing onboarding base and Google callback. This work does not replace the custom domain or alter Google/Authentik redirect registrations. It is separate from the released status-display fix.

## Current code facts

- `onboarding.py:dispatch` authenticates OwnerAuth before both `/oauth/google/callback` and every `/v1/` route. A callback without the owner cookie discards Google's code/state and redirects to the owner setup page.
- `onboarding_auth.py` stores an opaque `__Host-gog-owner` cookie/session, checks the Authentik owner group on login, and checks exact Origin plus CSRF token for owner mutations. Sessions last 12 hours.
- `onboarding_commands.py:enroll` fingerprints/replays accepted requests before interpreting new fields, pins the Web client revision/reference, snapshots services and owner access, increments the account generation, and binds consent to `digest(session_id)`.
- `onboarding_consent.py` stores random OAuth state, PKCE verifier, nonce and authorization URL in encrypted SSM artifacts. Both consent response and callback require the operation's owner-session hash. OAuth consent lasts 30 minutes. Callback claims `authorizing -> exchanging` conditionally before the single Google code exchange and saves the encrypted response before identity validation.
- `onboarding_google.py` uses fixed Google endpoints, S256 PKCE, offline access, consent prompt and an account login hint. Identity validation verifies the ID token, client audience, nonce, verified email, exact requested email, existing Google subject if known, and selected service scopes. A login hint alone is not identity proof.
- Account generation plus active-operation checks fence consent and worker effects. The shared account effect lease protects credentials, policies, access and removal. Restrictive owner actions can supersede consent; keep those guarantees.
- Saved callback responses can be validated after an interrupted request; an unsaved ambiguous exchange requires fresh consent. Old installed credentials remain until a validated replacement succeeds. Account state has no TTL; terminal operation/idempotency diagnostics and saved recovery retain seven days.
- The worker currently treats due `preparing_consent` or `authorizing` as expired. A link cannot reuse the two-minute preparing deadline as a 24-hour handoff. Add an explicit waiting phase and its scheduling semantics.
- `onboarding_resources.py` already routes `/onboarding` and its greedy child to the onboarding Lambda without gateway auth; application code enforces owner auth. Existing table, stream, queue and encrypted attempts paths suffice. No new service or gateway route is needed.
- Response helpers already provide no-store, no-referrer, same-origin CSP and no framing. API access logs use standard fields/resource template with data tracing disabled, but execution logging is INFO; prove the new exchange payload and callback query do not appear in execution logs before release.

## Smallest coherent design

### Owner mint and frozen enrollment

Add an authenticated owner command that accepts selected services and an optional explicit replacement choice. Apply the existing enrollment validation and conflict rules, including application readiness and installed-service preservation. Do not add project selection. Snapshot existing desired owner-tool access; tool-access changes still use the existing separate owner action.

Accept an enrollment operation in `awaiting_connection` and reserve its generation/active-operation slot with the existing transactional command machinery. Persist its owner, normalized email, services, client revision/ref, requested intent, generation, owner command key/fingerprint, link identifier/hash and start deadline. The enrollment kind remains `enrollment`, allowing the existing worker phases after consent. Set `status=accepted`, `next_attempt=link_expires_at`. Add this phase to ACTIVE so stream delivery schedules expiry; due execution terminates it with `connection_link_expired`. It must not create a Google authorization URL before target-profile binding.

Generate a 32-byte `secrets.token_urlsafe(32)` capability. Link IDs use the existing `new_id("cl")` helper; ID entropy is not the capability. Persist only the token hash in the lookup/authorization row. Keep the original token encrypted under the existing `attempts/<op-id>/connection-link` hierarchy so an exact owner command retry can return the original link after a response is lost. Track its reference explicitly and extend cleanup to that field using the existing attempts deletion permission. Persist the link record and reserved operation/idempotency facts atomically, with encrypted artifact preparation before acceptance. On interrupted preparation, same-key retries finish preparation for the same reserved operation; never produce a second reserved enrollment. Unaccepted prepared artifacts get a bounded tracked cleanup row. Acceptance atomically extends both preparation and cleanup retention; elapsed unaccepted preparation requires a fresh command key.

Link start deadline: 24 hours from owner acceptance. This gives time to switch among eight profiles. Display the expiry as local date/time on both pages. Explicit server time comparisons enforce expiry; DynamoDB TTL is cleanup only. A started Google attempt gets its own 30-minute deadline even if the handoff deadline subsequently passes. Validation/installation continue after either deadline because they no longer need bearer consent authority.

Return the link only to the owner response/replay. Never expose it in the general account/operation projection, logs or analytics. An authenticated owner can explicitly replace a waiting/authorizing attempt with a new command key; this cancels the old operation/generation and invalidates its capability and target cookies. Changing access/disconnecting retains existing restrictive supersession semantics. Waiting-phase conflict checks must be added to owner enrollment/account-action behavior; it must not be mistaken for validation already in progress.

### Link landing, target binding and Google consent

Recommended link shape:

`<permanent-onboarding-base>connect#cl_<id>.<capability>`

The fragment keeps the capability out of the initial HTTP request, server URL/access logs and HTTP referrers. A small same-origin landing script reads it into memory, immediately removes it with `history.replaceState`, then POSTs it in a JSON body to claim. Do not persist it in local/session storage. The page has no third-party scripts, fonts, analytics or external subresources. Clipboard and browser history handling are part of browser proof, not an assumption that fragment secrets are invisible everywhere.

GET of the landing page has no side effects; link preview/prefetch does not consume it. Claim requires exact same-origin Origin and rejects cross-origin requests/CORS. No owner cookie is required. It atomically claims an unexpired, unsuperseded link and binds one new random target-profile session token hash to this operation. Repeated claim with a matching connection cookie returns the same projection; claim after consumption without that cookie returns a safe generic conflict and cannot reset binding. The first claim response sets an HttpOnly, Secure, SameSite=Lax per-operation `__Host-gog-connect-<operationId>` cookie with Path=/; this is a different cookie name and record type from OwnerAuth. Per-operation cookies avoid one account overwriting another within the same profile. A lost first response before its cookie arrives requires an owner replacement link; no ambiguous rebind path.

Show a narrow page with requested email, selected services, expiry and “Continue to Google”. Profile selection is manual: a link cannot discover or open the intended browser profile. This page clearly names the account so a wrong-profile opening is visible before consent. It never offers service/account/access editing.

The explicit Continue POST requires the scoped cookie, exact Origin and a scoped CSRF token returned to this page. It snapshots no new authority: it transitions the reserved operation into `preparing_consent`, reuses Google consent generation with `auth_mode=connection`, and binds its OAuth state to the target cookie hash instead of owner session hash. Conditional state/operation/generation checks handle concurrent starts. An Idempotency-Key makes a double click/network retry return the same attempt/URL. A reopened bound target may use a fresh key to retrieve the same valid authorizing URL; this never creates new state or another exchange. Reopening in the same profile resumes the same operation and existing URL while authorizing; it does not mint fresh state automatically. Existing 30-minute consent deadlines and single exchange rules remain.

The permanent Google callback selects auth mode from the saved STATE/OP rows, not a query flag. Owner mode keeps current OwnerAuth/session checks. Connection mode requires the exact scoped cookie hash, current attempt state, matching operation/generation and unexpired consent. Unknown state never bypasses owner authentication or chooses a caller-provided operation. The code and state are removed by immediate 303 to the scoped result page. That page polls only its operation, without owner login.

Callback replay with valid binding shows already saved progress and never re-exchanges the Google code. Only the authorizing claimant exchanges; a simultaneous replay while exchanging shows progress. A callback with the wrong/missing cookie, expired/superseded state or wrong auth mode cannot mutate an operation. Reject safely without echoing code/state. Denial, wrong account or missing permissions terminates this attempt; the owner generates a fresh link for another consent attempt. The target profile cannot issue generic `restart`, `recover` or `enroll` commands.

### Identity and tokens

Reuse current Google ID-token validation and frozen client/scopes. Exact verified email is required on first enrollment; when a prior Google subject exists it must match too. A request/URL cannot override either. Existing same-subject/same-client refresh-token reuse remains valid only under the same shared validator. Missing durable refresh authorization remains an error.

Tokens, client secrets, PKCE verifier, callback response and candidate credentials stay in existing encrypted server artifacts. The browser receives only the Google authorization URL, safe operation projection and scoped CSRF token. Link expiry/revocation cannot restart token exchange; saved-token worker validation, promotion, policy application and propagation remain generation/effect-lease fenced. Wrong identity never promotes a candidate. No new token cache or client-side token handling.

After completion a target cookie can read only its frozen operation summary for a bounded maximum of 25 hours from mint (24-hour handoff plus up to 30-minute consent and display margin), then expires. It cannot read seven-day owner recovery or other operations. Polling may read its completed terminal summary when the account has the same generation but no active operation; a newer generation revokes target access. Account state and authenticated dashboard remain the long-term authority. Narrow GET is idempotent and must not automatically trigger retries.

## Caller HTTP contract

Paths below are relative to the permanent onboarding base; update `docs/onboarding.openapi.yaml` (OpenAPI 3.1) before implementation. Continue RFC 9457 safe Problem Details.

| Route | Authority and request | Response / semantics |
| --- | --- | --- |
| `POST v1/accounts/{email}/connection-links` | Owner session, current owner CSRF + exact Origin, Idempotency-Key. Body `{services: [...], replace: false}`; unknown fields rejected. Account must belong to owner. | `201 Created`, Location `v1/connection-links/{linkId}`, `{id, operationId, connectionUrl, expiresAt, email, services}`. Link is available immediately although enrollment remains accepted. Same key/body returns same resource; changed body/key fingerprint -> 409. |
| `GET v1/connection-links/{linkId}` | Owner session belonging to frozen owner. | `200` metadata/status and related operation URL, no raw capability. Owner copy after lost response uses original mint replay. Missing/wrong owner -> 404. |
| `DELETE v1/connection-links/{linkId}` | Owner session + CSRF/Origin. | `204` when waiting/authorizing attempt canceled via generation-fenced cancellation. Repeated cancellation is idempotent. Exchange/validation already started -> 409; use existing owner restrictive controls for account intent changes. |
| `GET connect` | Public static landing page. | `200`; no claim or owner auth redirect. Fragment is interpreted only locally. |
| `POST connection/v1/claims` | Exact Origin; JSON `{linkId, capability}`; optional matching connection cookie for retry. | `200` scoped metadata, operationId, CSRF and result URL; Set-Cookie on first successful claim. Unrecognized/expired link -> generic 404/410; previously claimed without binding or superseded -> 409. No account enumeration. |
| `GET connection/v1/operations/{operationId}` | Exact matching scoped cookie. | `200` `{id, email, services, status, phase, errorCode?, safeMessage?, consentExpiresAt?, canContinue}`. No owner identity, account inventory, tool-policy details, artifacts, continuation request/key or raw OAuth state. Missing/expired binding -> 401; another operation -> 404; superseded -> safe 409. |
| `POST connection/v1/operations/{operationId}/consents` | Scoped cookie + exact Origin + scoped CSRF + Idempotency-Key, empty JSON body. | `202` and Location pointing to scoped operation; includes authorizationUrl only while its existing consent attempt is valid. Same key/request restores same result/attempt. No scope/access/restart fields. Terminal attempt -> 409 requiring owner fresh link. |
| `GET oauth/google/callback` | Persisted state chooses existing owner binding or scoped connection binding. | `303` to owner dashboard for owner mode; scoped result page for connection mode. Failure redirects contain only stable safe recovery codes. Saved exchange remains durable. |
| `GET connect/result?operation={operationId}` | Public shell; data requires scoped cookie. | `200` scoped result UI, never dashboard/owner-login redirect. Opaque operation ID is not authority. |

Mint replay is scoped to owner, account, route and environment with immutable fingerprint before acceptance. Retain accepted idempotency results seven days as today, but replay of an expired/revoked link returns original metadata with expired/revoked status and no usable URL; it never extends expiry. Target Continue keys are scoped to connection binding/operation and route; retain through operation diagnostics, and reject expired consent rather than return a usable URL. The callback's persisted phase is its one-shot provider-exchange guard, not an invented Idempotency-Key on Google's GET.

## Security and exposure proof

Preserve no-store/no-referrer/CSP/no-framing on every shell, JSON response and redirect. Do not put raw capabilities in path/query, Location headers after claim, Problem Details, DOM attributes after consumption, log events, screenshots intended for reports or operation projections. Log only request/operation/link identifiers, auth mode, phase/outcome and safe error code; redact cookie/CSRF/body/code/state/verifier/tokens. Do not print whole Lambda events. Confirm API Gateway execution logging does not capture request bodies/query parameters for onboarding; if INFO logs do, explicitly disable execution logging for these routes while keeping safe access metrics/logs. The implementation-turn read-only audit inspected 1,385 current execution log events: zero request-query, request-header, or request-body lines with data tracing disabled. No logging/IaC change was needed. The fragment choice does not protect the subsequent POST body from body tracing.

Capability possession can claim this one operation; claim consumes its sharing authority into one profile binding. A stolen unused link cannot connect a different Google identity, but must be treated as a secret because it can consume or authorize the bound account. Owner replacement revokes it. This is the concrete consequence of the requested link, not a new administrative role framework.

## Implementation slices and acceptance

1. Update OpenAPI and focused HTTP/store tests for owner mint, immutable snapshot/idempotency, atomic reserve/claim and scoped projection. Add waiting-phase worker scheduling and expiry tests using the existing MemoryStore/store contract boundaries.
2. Implement the owner mint and minimal capability/cookie helpers, reuse enrollment/consent/effects. Factor shared validation only where both entry points need it; do not relax OwnerAuth globally. Cover supersession between claim, start, callback and promotion, cancellation races, preparing crash, concurrent start and lost responses.
3. Add the connection landing/result shells and dashboard Generate/Copy action. Use existing assets/styles; no new framework. Keep existing owner-in-profile connection action working and maintain current status-display behavior.
4. Prove callback exchange-once, saved-response recovery, unsaved ambiguity, wrong email/subject/audience/nonce/scopes, insufficient refresh authorization, denial and expired cookie/state. Reuse existing meaningful worker recovery and policy fencing tests; extend them through scoped HTTP contracts.
5. Run relevant Lambda and infrastructure tests via existing `just proxy-test`, OpenAPI validation, generated Lambda asset verification if packaged, then normal required repository checks. Documentation-only planning needs diff/Markdown checks, not runtime tests.

Browser proof must use named `agent-browser` sessions and separate cookie jars/profiles; no assumption that a running browser has owner auth. First prove locally with the existing contract fixture extended to exercise the real HTTP handler and persisted operation flow. Then, only within a separately authorized runtime target, prove real Google consent:

- Owner profile generates two account links; matching Google-only profiles claim/approve without any auth.k-rae.com navigation or owner cookie. Target results show only their own connection, and dashboard reflects the persisted successful result.
- Refresh/close/reopen in the same target profile resumes the same waiting/authorizing/validating operation. A fresh profile cannot reuse a claimed link. Concurrent claim/start/callback requests do not duplicate operations or exchanges.
- Wrong Google profile/account produces a clear exact-account failure, preserves old installed credentials and cannot silently attach the wrong subject; owner replacement then permits the intended profile to connect.
- Link opened after 24 hours asks for an owner-generated replacement. A consent started just before handoff expiry retains its full 30 minutes. Google denial and consent timeout show a scoped safe result, without a login loop.
- Owner revoke, access restriction or disconnect fences an already-open link and late callback. Target cannot reach owner account/tool policy APIs, read other operations or change services.
- Capture sanitized network/console evidence for fragment removal, no leaked referrers, cookie flags, CSRF/Origin enforcement, callback redirect query cleanup, no tokens in browser storage and safe execution logs. Record actual permanent callback URL from public configuration at implementation verification; do not mutate prd just to test.

## Alternatives and limits

Copying the existing Google URL fails because it remains owner-session bound. Signing into Authentik in every Google profile works today but adds the management login the user explicitly wants to avoid. Removing callback owner checks globally grants excessive authority and loses browser binding.

A mint-without-operation design could leave account intent untouched until claim, but needs an atomic generation-fenced claim plus new enrollment acceptance and more race handling. Reserving one existing enrollment operation is simpler, makes dashboard progress visible immediately, and invalidates older links through established supersession. The deliberate cost is a waiting link occupies that account's active slot until claim, replacement, cancellation or expiry; display this clearly and permit owner cancellation. There is no automatic retry of failed consent and no portable resume after clearing cookies; mint a replacement instead.

The fragment landing needs JavaScript, already required by the dashboard; it is warranted to keep bearer authority out of request URLs. A direct token path/query link would be smaller but would carry the secret through URL logging/history and is not chosen. No QR code, bulk link generator, email delivery, general invitation system or target-profile account-management UI is required for this workflow.
