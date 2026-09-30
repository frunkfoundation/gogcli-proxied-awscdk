# gogcli-proxy Infra (CDK)

This Python AWS CDK app deploys the `gogcli-prd` environment into K-Rae account
`968138089939`, region `us-east-2`, with API Gateway stage `prd`:

- Two Python Lambdas: the Google API proxy and the owner-onboarding handler. The
  latter also dispatches DynamoDB stream events and runs delayed verification.
- One API Gateway REST API: managed Google routes require IAM SigV4 and a
  usage-plan API key; `/prd/onboarding/` uses its own owner session through the
  existing `auth.k-rae.com` Authentik service.
- A retained DynamoDB metadata/operation table with a stream and TTL, plus an
  encrypted SQS verification queue and dead-letter queue. Closing the account page
  does not interrupt saved account operations.
- SSM SecureString storage for Google client secrets, refresh tokens, and owner
  OIDC credentials using the existing AWS-managed SSM key. DynamoDB and queue
  messages contain references and operation metadata, not Google secrets.
- Explicit bounded `jdev-gogcli-proxy-prd-*` application roles, caller policies,
  and CloudWatch alarms for worker errors, stream age, and dead-letter backlog.

The gogcli client is already wired to:

- send `X-GOG-Account: <email>`
- send signed `X-GOG-Google-Host: <original-google-host>` and preserve the original Google request path
- send `x-api-key: <api-key>`
- SigV4-sign the request for `execute-api`

Managed account API requests, including Gmail history reads during watch processing, use this proxy. `gog gmail watch pull` separately connects to Google Cloud Pub/Sub through its native subscriber client and Application Default Credentials. Those credentials authorize subscription reads and acknowledgements; they do not supply the managed Gmail account's OAuth token. See `internal/cmd/gmail_watch_pull.go` for the subscriber setup.

## Deploy

The source is this repository; the target is
[`env/gogcli-prd/binding.json`](../env/gogcli-prd/binding.json). Deployment and the
first CLI caller use the Developer profile `krae-jdev-dev-prd`. The application's
roles use the existing `JayDeveloperCreatedRoleBoundary`; canonical JDev owns
that boundary and the Developer identity's scoped `execute-api:Invoke` grant.
This CDK app does not modify those protected roles or redeploy Authentik.

From the repository root, prepare the project-local Python environment:

```bash
uv venv infra/.venv
uv pip install --python infra/.venv/bin/python -r infra/requirements.txt
export AWS_REGION=us-east-2

aws --profile krae-jdev-dev-prd --region us-east-2 sts get-caller-identity
cdk synth GogcliGoogleProxyApi \
  --app 'infra/.venv/bin/python infra/app.py' \
  --context environment=gogcli-prd --profile krae-jdev-dev-prd
cdk deploy GogcliGoogleProxyApi \
  --app 'infra/.venv/bin/python infra/app.py' \
  --context environment=gogcli-prd --profile krae-jdev-dev-prd \
  --outputs-file env/gogcli-prd/access.json
```

CDK bundles the Python runtime and the onboarding HTML, JavaScript, and CSS;
The installed `uv` packages prebuilt Linux x86_64/Python 3.11 wheels; missing
wheels fail the build rather than using host binaries. Docker is not required.
Run `infra/.venv/bin/python -m unittest discover -s infra -p test_python_asset.py`
to check the actual payload, its ELF architecture, and static assets. The app uses
`CliCredentialsStackSynthesizer`, so it deploys with this explicit Developer
identity and uses the existing CDK bootstrap asset bucket without assuming
bootstrap roles.

The generated CDK outputs in `env/gogcli-prd/access.json` are grouped under
`GogcliGoogleProxyApi` and contain these non-secret access facts:

| Output | Use |
| --- | --- |
| `ProxyBaseUrl` | CLI proxy base URL, ending in `/prd/` |
| `ApiKeyId` | Identifier for the usage-plan key; not its secret value |
| `ProxyInvokeArns` | JSON array string of the six exact canonical JDev invocation resources |
| `SetupUrl` | Published HTTPS owner account connections page, ending in `/prd/onboarding/` |
| `GoogleCallbackUrl` | Exact redirect URI for the Google Web application client |
| `OwnerCallbackUrl` | Exact Authentik redirect URI |
| `OnboardingTable` | Persisted account and operation metadata table |
| `OnboardingRoleArn` | Application role used by signed verification reads |

The invocation list contains these resource suffixes under this exact API and
`prd`: `*/gmail/*`, `*/drive/v3/*`, `*/upload/drive/v3/*`,
`*/resumable/upload/drive/v3/*`, `*/calendar/v3/*`, and `GET/proxy/v1/accounts`. Inventory is GET-only;
this grant does not include onboarding, other stages, or generic
Google HTTP batch routes. Account access and caller/account policy still apply
after API Gateway accepts a signed request.

Apply this list through the canonical JDev runner to both the Developer identity
policy and created-role boundary. Source:
`/Users/jay/Projects/development/jdevaws/templates/jdev-target-access.yaml`.
Target: the existing `krae-jdev-access-target` stack in **us-east-1**, account
`968138089939`, explicit manifest profile `krae-jdev-admin-prd`. Proxy resources
are in **us-east-2**. Update only K-Rae production:

```bash
cd /Users/jay/Projects/development/jdevaws
uv run python scripts/jdev_target_access.py plan production --account k-rae-prd
JDEV_DEPLOY_CONFIRM=deploy-production uv run python scripts/jdev_target_access.py deploy production --account k-rae-prd
```

The plural `ApplicationApiInvokeArns` source contract replaces the old singular
Gmail parameter. Other manifest targets remain disabled. A source change is not
proof of a deployed binding; inspect stack completion and real signed requests
after rollout. Do not run a fleet deployment to clear other targets' expected
baseline-version differences.

## Stable public address

The production binding fixes `public_domain_name` under the existing `k-rae.com`
Route53 zone. The stack owns a DNS-validated regional ACM certificate, API
Gateway custom domain, `/prd` base-path mapping, and DNS alias. All public setup,
callback, and proxy URLs use that hostname; the underlying API ID remains an IAM
invocation resource, not an OAuth registration address.

The domain, certificate, and DNS alias are retained on stack deletion. Preserve
the binding across API replacements and repoint the mapping to the replacement
stage. Recreating a deleted stack requires importing these retained resources;
do not generate another hostname to bypass ownership conflicts. Intentional
retirement requires removing external registrations and explicitly cleaning up
the retained resources. This endpoint contract does not replace credential and
account-state preservation.

## Configure owner login and connect Google accounts

After deploying and generating `access.json`, configure this app on the existing
Authentik service from the repository root:

```bash
uv run --with requests --with boto3 python scripts/configure_owner_login.py
```

The script reads the concrete binding and CDK outputs, creates or updates the
app-specific confidential provider/application, restricts its owner group to
Jay's `jay.jakosky` account, and reuses the shared authorization/invalidation
flows and signing key. It registers `OwnerCallbackUrl` exactly. Authentik API
access comes from the installed credential catalog; the client credentials are
saved back to that catalog and SSM SecureString without being printed. Its final
read-only checks verify provider discovery, JWKS, the exact callback, and owner
membership.

## Configure the Google application

The source is this checkout; the target is the existing `gogcli-prd` binding
(account `968138089939`, `us-east-2`, explicit profile `krae-jdev-dev-prd`).
The singular `google_application` binding identifies project `gogcli-proxy`,
number `1042630585914`, owned by `kraecollective.com`. This project and its OAuth
application already exist. Use the downloaded **Web application** client JSON;
keep it outside the repository.

Before provisioning, inspect the existing Google Console configuration and
confirm the selected service APIs are enabled, the scope list includes the
selected services, and the intended audience can approve consent. Confirm the
Web client's redirect URI is exactly `GoogleCallbackUrl` from `access.json`:

```text
https://gp-039168171bf05570eadba6a8.k-rae.com/prd/onboarding/oauth/google/callback
```

The callback is permanent across API replacements. Register no generated API
hostname. Google authorized-domain settings for this callback use `k-rae.com`;
the project organization owner is `kraecollective.com`. JSON and imported metadata
cannot establish API enablement, publishing status, test users, or Workspace approval. Record only settings actually observed.
For the currently confirmed Gmail/Drive setup and a confirmed Testing status:

```bash
just configure-google-application /absolute/path/to/client.json gmail,drive testing
```

Use `gmail,drive,calendar` only after confirming Calendar API and its scope;
use `production` only after confirming that exact publishing state. The command
validates canonical project identity, Web-client type, callback, explicit AWS
identity and target; it stores an encrypted immutable client revision and
revision-bound API/publishing confirmations without printing secret values.
Reimporting the same client reuses its credential revision and creates no new
encrypted client secret; recording the operator review writes metadata. Replacement
revisions leave installed connections pinned to their original client until a
successful reconnect. The operator handles provisioning failures here; the
account page contains no credential upload or configuration controls.

Testing is supported. Google's seven-day refresh-token expiry applies to Testing
applications using these scopes unless an applicable documented exception is
established. Workspace trust concerns each organization's users; personal Gmail
and other organizations do not inherit it. Check the actual audience/test-user
and publishing configuration before describing token lifetime. See
[Google's app-state overview](https://developers.google.com/identity/protocols/oauth2/production-readiness/overview).

Open `SetupUrl` and follow the [account connection guide](../docs/account-onboarding.md).
Each account chooses Gmail, Drive, and optional Calendar for the same application.
A blocked organization may require administrator approval for the canonical
client and requested scopes. Provisioning does not connect any Google account.

The shared service registry requests `openid` and `email` for identity, plus
`gmail.modify`, `drive`, and `calendar` for the selected services. The provisioned owner
policy allows those services' normal GET/POST/PUT/PATCH/DELETE routes; independent
account policies and Google permissions narrow what actually succeeds. These
scopes do not promise permanent Gmail deletion, Gmail settings operations,
large-file transfers, or content/folder/label filtering.

A fresh candidate refresh and minimal selected-service reads must pass before
installation. Gmail checks profile and at most one message ID; Drive checks the
account email and at most one file ID. Empty collections pass. After installation,
the worker waits at least 65 seconds before repeating those probes through the
signed proxy as the **verifier role**. Its result records the service, revision,
principal, and time; it does not prove the actual CLI caller's IAM/policy access.
Run inventory and the same minimal reads as that caller for acceptance.

Google connection, the latest attempt, and desired tool access
are separate facts. Connect enables the configured owner unless an existing
saved pause must be preserved. **Pause tool access** controls the concrete
`binding.client_role_arn` (`ONBOARDING_OWNER_CALLER_ARN` at runtime). Turning it off
removes the owner's explicit email grant while retaining Google authorization
and limited verifier access. Reconnect preserves the flag and installed
connection until a usable replacement passes validation. Durable account state
survives temporary operation cleanup; recovery uses account-level controls.

Disconnect removes owner/verifier grants and installed credentials through a
persisted operation. It does not mutate Google content or revoke the shared
Google application's consent. Removal remains applying for at least 65 seconds
after the final authorization write. Starting requests fail within the 60-second
configuration cache bound; an already authorized request may finish within its
existing 27-second budget. Failures remain retryable and never report a completed
removal before its writes and propagation window finish.

For the first CLI caller, fetch the usage-plan key using `ApiKeyId` from the
generated access record:

```bash
aws --profile krae-jdev-dev-prd --region us-east-2 apigateway get-api-key \
  --api-key '<ApiKeyId>' --include-value --query value --output text
```

Configure `proxy_base_url` from `ProxyBaseUrl`, `proxy_api_key` from the returned
value, `aws_profile` as `krae-jdev-dev-prd`, and a verified `default_account` in the
CLI's `config.json`. Run `bin/gog config path` to locate it. Google credentials
stay on the server; the account guide provides the
first read-only CLI checks. Signed `GET /proxy/v1/accounts` returns only metadata
for the caller's permitted installed accounts and never reads account credential
parameters or Google data.

## SSM Parameters

The Lambda reads SSM parameters under `GOG_SSM_PREFIX` (default: `/gogcli-proxy`).

Parameter names use base64url (no padding) encoding for safety.

### Allowlist (deny-by-default)

Name:

- `<prefix>/allow/<caller_id_enc>`

Value JSON:

```json
{
  "emails": ["user@example.com"],
  "domains": ["example.com"]
}
```

Notes:

- `caller_id` comes only from API Gateway's authenticated request identity. Cognito identity IDs take precedence; otherwise `userArn` (or an ARN-valued `caller`) identifies the IAM principal.
- Assumed-role sessions use a stable role identity: `arn:aws:sts::123456789012:assumed-role/proxy-agent/session-name` becomes `arn:aws:iam::123456789012:role/proxy-agent`. Account, partition, and role remain part of the key; session names cannot grant another role's access. IAM user ARNs remain unchanged.
- Missing or invalid identities, including a bare `AROA...:session-name` without an ARN, are denied before reading allowlists. There is no `unknown` or session-name fallback.
- Email/domain matching is case-insensitive. Operator-managed caller documents can use domains or `"*"`; managed owner/verifier access toggles require explicit-email-only grants.
- If a managed allowlist contains a wildcard/domain grant, account toggles fail with `409 broad_allowlist_requires_operator` instead of silently removing that configuration or claiming one email is revoked.

### Runtime identity and account metadata

Use the stable authenticated role ARN as the caller key. Session-name keys are
obsolete and are never consulted. Independent consumers need distinct IAM
principals when their permissions differ; a shared Developer role cannot express
per-session grants.

DynamoDB retains `CLIENT#<project-id>`, `SETUP`, and `ACCOUNT#<email>` facts.
`OP#` and scoped idempotency records retain terminal diagnostics for seven days.
Nonterminal work has explicit recovery/expiry handling rather than disappearing
through TTL. Queue messages contain operation IDs. Temporary secret references
stay internal and never enter public account/operation responses.

### Google Credentials (per account)

Name:

- `<prefix>/google/accounts/<email_enc>`

Value JSON: one of:

Service account (domain-wide delegation):

```json
{
  "type": "service_account",
  "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
  "service_account": {
    "type": "service_account",
    "project_id": "my-project",
    "private_key_id": "....",
    "private_key": "-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n",
    "client_email": "svc@my-project.iam.gserviceaccount.com",
    "client_id": "....",
    "token_uri": "https://oauth2.googleapis.com/token"
  }
}
```

OAuth refresh token:

```json
{
  "type": "oauth_refresh",
  "client_id": "....apps.googleusercontent.com",
  "client_secret": "....",
  "refresh_token": "....",
  "token_uri": "https://oauth2.googleapis.com/token"
}
```

Notes:

- For `service_account`, the proxy impersonates the requested `X-GOG-Account` email (domain-wide delegation subject).
- For `oauth_refresh`, omit `scopes` unless you know the refresh token was minted with those scopes.
- Warm Lambda invocations reuse access tokens separately for each account until 60 seconds before their reported expiry. Tokens without a usable expiry are never cached.
- SSM account and allowlist configuration caches cannot exceed 60 seconds. A changed credential configuration or default scope list invalidates that account's cached token when the new configuration is observed.

## Policy Enforcement (Lambda)

Policy is enforced in the proxy Lambda (deny-by-default) when `GOG_POLICY_ENABLED=true` (default).

Policy SSM prefix defaults to:

- `<GOG_SSM_PREFIX>/policy` (for example `/gogcli-proxy/policy`)

Policy documents:

1. `<policy_prefix>/default`
2. `<policy_prefix>/caller/<caller_id_enc>`
3. `<policy_prefix>/account/<email_enc>`

A valid default document is required, even when a caller document exists;
missing default returns `{"error":"policy_not_found"}`. Malformed configured
documents fail closed. Provisioning creates an empty default-deny document.

### Policy JSON schema (v1)

```json
{
  "version": 1,
  "default_effect": "deny",
  "rules": [
    {
      "id": "gmail-read",
      "effect": "allow",
      "priority": 100,
      "match": {
        "methods": ["GET"],
        "accounts": ["user@frunkholdings.ltd"],
        "hosts": ["gmail.googleapis.com"],
        "path_regex": "^/gmail/v1/users/me/(labels|messages|threads)(/.*)?$",
        "query": {
          "forbid_keys": ["uploadtype"]
        }
      },
      "constraints": {
        "max_request_bytes": 1048576,
        "require_account_domain": ["frunkholdings.ltd"]
      },
      "on_match": {
        "emit_event": true,
        "event_type": "proxy.policy.allow.gmail_read",
        "detail": {
          "policyVersion": "v1"
        }
      }
    },
    {
      "id": "deny-mutations",
      "effect": "deny",
      "priority": 90,
      "match": {
        "methods": ["POST", "PUT", "PATCH", "DELETE"]
      }
    }
  ]
}
```

Notes:

- Any matching explicit deny in default, caller, or account documents vetoes the request, regardless of priority.
- The caller document supplies positive permission when present; otherwise the default supplies it. A restrictive caller document cannot inherit unrelated default allows.
- An account document is an independent ceiling: it must also allow the request. A missing account document adds no ceiling.
- Within each document, `default_effect` applies when no rule matches. Priority and declaration order choose among equivalent matches, never allow over deny.
- Optional `match.accounts` contains exact normalized email addresses. An absent or empty list is unconstrained; use explicit addresses for account-dependent permission.
- Unknown keys fail closed. Policies authorize API operations, not response fields, Gmail label membership, or Drive folder membership.

For an account whitelist, use `default_effect: deny` and explicit allowed paths,
methods, and hosts. For an account denylist, use `default_effect: allow` plus
explicit denied operations. Both remain ceilings: an account allow cannot
create permission denied or absent in the caller document.

This replaces the obsolete shared priority-sorted override semantics. Before
rollout, inspect actual nonsecret policies and account metadata; explicitly
convert any configuration relying on account allows overriding caller denies.
The preflight for this stabilization found no installed remote accounts or
account policy documents.

Policy caches cannot exceed 30 seconds. Allowlist/account configuration caches
cannot exceed 60 seconds; environment values may shorten these bounds but cannot
extend them. Authorization runs before reusing a cached Google token.

### Policy trigger events (optional)

Enable match-triggered events:

```bash
export GOG_POLICY_EVENTS_ENABLED=true
export GOG_POLICY_EVENT_BUS_ARN='arn:aws:events:...:event-bus/your-bus'
```

When enabled and a matching rule has `on_match.emit_event=true`, Lambda emits an EventBridge event with decision metadata.
Trigger failures are logged but do not change allow/deny results.

## Routing, payloads, and timeouts

`X-GOG-Google-Host` is required and validated against the supported Google API host allowlist, `docs.google.com`, or a valid subdomain of `googleusercontent.com` for Photos Picker media. The original path is preserved, including upload prefixes and API versions. Path-only host inference is obsolete and has been removed. Upgrade clients and the handler together; older clients without the signed host header receive HTTP 400. Proxy authentication/account/routing headers are removed before forwarding, and Google redirects are returned to the client for another signed request. Duplicate routing headers, method overrides, malformed/ambiguous paths, and contradictory query representations fail before token minting or Google forwarding. Policy evaluates the prepared destination and the same repeated query parameters that are sent.

The request body cap is 4 MiB. `GOG_MAX_REQUEST_BYTES` may lower it, but cannot raise it. This leaves room for ordinary API Gateway event/base64 overhead below Lambda's 6 MiB invocation limit. Gmail's upstream 35 MiB raw-message limit does not apply to this proxy; the proxy body cap includes MIME/base64/JSON content transmitted to Google.

Responses have both a streamed raw-body cap (`GOG_MAX_RESPONSE_BYTES`, default 5,500,000 bytes) and a hard 6 MiB cap on the serialized Lambda response, including headers, base64 expansion, JSON escaping, and the response envelope. Oversized responses return HTTP 502, and upstream response streams are always closed.

Token minting and forwarding share a request budget capped at 27 seconds, or two seconds less than the Lambda invocation's remaining time if shorter. Connect/read timeouts use each phase's remaining budget, token refresh retries are bounded by its refresh deadline, and streamed chunks are checked against the request deadline. Expired budgets return HTTP 504 before starting another phase.

Run the offline Lambda checks and synthesized infrastructure contracts:

```bash
just proxy-test
infra/.venv/bin/python -m unittest discover -s infra -p test_onboarding_infrastructure.py
```

Maps/Places and public YouTube API-key modes also retain upstream project credentials and do not consume managed-account Google OAuth tokens. These ancillary API-key requests go directly to their provider; account-authenticated Google API requests use the proxy.
