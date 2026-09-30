# Proxy stabilization acceptance

Date: 2026-09-30
Plan: [proxy-stabilization.md](proxy-stabilization.md)

## Source and target

- Application source: `/Users/jay/Projects/gogcli-proxy`, branch `update/gogcli-v0.42.0`.
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

These service selections are defaults awaiting the owner's page review, not
claims of granted Google permissions. Reuse `jayj-gogcli-full-access` unless an
observed organization requirement calls for another recorded project.

## Verification in progress

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
- Final preflight confirms zero installed Google credentials, account/caller
  grants, and CLIENT/SETUP/ACCOUNT/OP records; live String policies match their
  owning stack. Empty-state cutover remains valid.

## Completion gates

Application rollout and deployed checks are pending final integration.
Product readiness requires actual owner-caller Gmail and Drive reads through an
enrolled account. Eight-account enrollment remains incomplete until each account
is connected and checked, or has an explicit owner/Workspace action recorded.
No Google content writes are used for production acceptance.

Owner page: <https://u5q3kdnam4.execute-api.us-east-2.amazonaws.com/prd/onboarding/>
