# Proxy stabilization acceptance

Date: 2026-09-30
Plan: [proxy-stabilization.md](proxy-stabilization.md)

## Source and target

- Application source: `/Users/jay/Projects/gogcli-proxy`, branch `update/gogcli-v0.42.0`.
- Application implementation commit: `99ead79354e2`.
- Application rollout: completed on 2026-09-30; stack `UPDATE_COMPLETE`, both
  Lambdas active with successful updates (Python 3.11, x86_64).
- Application target: K-Rae `968138089939`, `us-east-2`, `GogcliGoogleProxyApi`, `prd`.
- Canonical JDev source: `/Users/jay/Projects/development/jdevaws`, commit `d40fff8`.
- Canonical target: `krae-jdev-access-target`, `us-east-1`, K-Rae production only.
- JDev rollout: completed at `jdev-v15`; canonical capability and drift checks passed.
- Additional live simulation: Developer identity and workload boundary allow the
  five intended route families; both deny inventory POST, Calendar, and onboarding.

## Enrollment roster

Jay supplied the remaining addresses on 2026-09-30. This is eight distinct
accounts after combining them with the three historical records. No account is
counted as remotely connected without installed credential and service evidence.
The historical Desktop setup is separate from remote proxy enrollment.

| Account | Selected services | Remote enrollment | Actual caller check |
| --- | --- | --- | --- |
| jay.jakosky@gmail.com | Gmail and Drive | Pending Web client / owner consent | Not run |
| jay@frunkholdings.ltd | Gmail and Drive | Pending Web client / owner consent | Not run |
| treasurer@longmonttheatre.org | Gmail and Drive | Pending Web client / owner consent | Not run |
| jay@frunkfoundation.org | Gmail and Drive | Pending Web client / owner consent | Not run |
| jay@longmonttheatre.org | Gmail and Drive | Pending Web client / owner consent | Not run |
| jay@kraecollective.com | Gmail and Drive | Pending Web client / owner consent | Not run |
| jay@krystaljakosky.com | Gmail and Drive | Pending Web client / owner consent | Not run |
| jay@marinasailing.com | Gmail and Drive | Pending Web client / owner consent | Not run |

All eight account metadata records were saved through the production account
service using the uniquely recorded owner. Installed connections: **0 of 8**.
This saved metadata only; no Google calls, credential writes, or caller grants.

These service selections are defaults awaiting the owner's page review, not
claims of granted Google permissions. Reuse `jayj-gogcli-full-access` unless an
observed organization requirement calls for another recorded project.

## Verification completed

- 83 canonical JDev offline tests passed.
- 8 CDK/resource and real Linux Python 3.11 artifact tests passed.
- 6 independent offline tests used production Store/OnboardingService, real
  boto3 serialization, and moto DynamoDB/SSM expression evaluation.
- 120 Python 3.11 Lambda tests passed, including policy, request boundary,
  inventory, cached-token revocation, and account lifecycle regressions.
- Desktop and mobile page fixtures and real owner-handler browser integration
  passed, including consent continuation after reload and withdrawn owner access.
- Full `make ci` passed: formatting, lint (0 issues), native/Linux dead-code
  checks, Go and Node tests, docs coverage, and generated skill checks.
- OpenAPI 3.1 validates. Rebuilt 759 command pages and 24 generated service skills.
- Predeployment preflight confirmed zero installed Google credentials, account/caller
  grants, and CLIENT/SETUP/ACCOUNT/OP records; live String policies matched their
  owning stack. The eight account metadata records were saved after deployment.

## Completion gates

Application rollout and deployed infrastructure checks are complete:

- Published HTML, JavaScript, and CSS match committed UTF-8 bytes and use no-store.
- Missing owner sessions return 401; login redirects to the existing Authentik
  client and owner callback with Secure/HttpOnly session binding.
- Browser check shows owner sign-in and Refresh with no JavaScript errors.
- Signed inventory correctly returns 403 until caller account access is configured.
- An unlisted synthetic Gmail account is denied at proxy admission, before Google;
  Calendar is denied at the API Gateway IAM boundary.
- Published access outputs match the stack and contain five bounded invocation
  route families. Recent runtime logs showed no credential markers.
- The source-built CLI identifies the implementation commit and upstream v0.42.0.
  Its private configuration now uses the remote proxy and explicit AWS profile
  `krae-jdev-dev-prd`, with `jay.jakosky@gmail.com` as the default account.
  Real CLI inventory reaches the proxy but is denied because no account grants
  have been enrolled; this is not a successful live Google acceptance check.

Next owner action: sign in on the page, select the existing
`jayj-gogcli-full-access` project, and follow its Web-client configuration steps.
Import the Web-client JSON on the page, then complete Google consent for the first
account. Historical Desktop clients do not enroll remote proxy accounts.

Product readiness requires actual owner-caller Gmail and Drive reads through an
enrolled account. Eight-account enrollment remains incomplete until each account
is connected and checked, or has an explicit owner/Workspace action recorded.
No Google content writes are used for production acceptance.

Owner page: <https://gp-039168171bf05570eadba6a8.k-rae.com/prd/onboarding/>
