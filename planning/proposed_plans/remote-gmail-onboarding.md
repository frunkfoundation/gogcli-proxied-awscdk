# Remote Gmail setup and account enrollment

Implement the approved remote onboarding plan in K-Rae production, account `968138089939`, `us-east-2`.

## Fixed choices

- Owner-only account connections at the permanent published `/prd/onboarding/` URL, using the existing `auth.k-rae.com` Authentik service and Jay's daily user.
- Request Google `openid email https://www.googleapis.com/auth/gmail.modify` with a Web application client and exact hosted callback.
- Preserve the proxy's AWS SigV4 and API-key contract. First CLI caller is `JayDeveloperFromJdev` using `krae-jdev-dev-prd`.
- First account is `jay.jakosky@gmail.com`; prefill `jay@frunkholdings.ltd` and `treasurer@longmonttheatre.org`, and support eight or more accounts.
- Google secrets and refresh tokens stay remotely in SSM SecureString using the existing AWS-managed SSM key. No Mac-keychain dependency.

## Implementation

Extend the existing Python Lambda package with a separate onboarding handler, owner authentication, safe instructional assets, and operation-based APIs. Reuse existing Google/proxy helpers. Add DynamoDB metadata and conditional workflow state, its stream as durable delivery, and SQS for delayed verification. Only secret references go into DynamoDB and operation IDs into queues; sensitive temporary data goes into SSM and is cleaned up.

The original application-setup UI is superseded by [single-project-account-connections.md](single-project-account-connections.md). The account page uses one canonical application and persisted account records; operator provisioning is separate. Progress and worker verification survive closing the page. Every account has a factual status and recovery action.

Use state, PKCE, OIDC nonce and verified identity/account/scope checks. Preserve old credentials until replacement refresh and candidate Gmail reads pass. Consume callback attempts conditionally; never repeat ambiguous code exchange. Promote to the current per-account SSM format, update explicit caller allowlists, and verify normal signed proxy reads after at least 65 seconds to outlast the existing credential cache. Empty mailboxes pass. Failed verification retains saved authorization and supports retry without consent.

Add canonical JDev support for `execute-api:Invoke`, scoped only to the deployed API's prd Gmail routes and enabled only for K-Rae production. Apply it through the central CloudFormation runner to the Developer identity and created-role boundary. Explicit bounded application roles must match the live `jdev-*` naming contract. Do not edit protected roles from this application.

Create the app-specific confidential Authentik provider/application through its REST API; restrict its owner group to Jay and reuse existing shared flows/signing keys. Register exact deployed callbacks and publish non-secret environment binding/access records. Do not redeploy Authentik infrastructure.

## Verification and delivery

Add focused behavior tests for authorization/CSRF, invalid Web JSON, identity/scope/PKCE/nonce failures, state expiry/replay, callback duplication, concurrent reconnects, interrupted workflows, delayed verification, and permission-blocked retry. Assert encryption and narrow IAM in IaC. Run browser checks for guidance and recovery, and relevant existing checks.

Deploy repo-owned infrastructure and the scoped JDev binding, configure Authentik login, and provide the working page. Jay completes real Google consent. Verify Gmail profile and a one-message ID-only listing through the normal proxy, then a read from the actual CLI; send or modify no mail. Connect remaining accounts individually. Add sanitized operation logs and worker/DLQ alarms. Finish with task-only commits in both repositories after applicable validation.
