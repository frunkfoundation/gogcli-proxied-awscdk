# gogcli-proxy Infra (CDK)

This Python AWS CDK app deploys the `gogcli-prd` environment into K-Rae account
`968138089939`, region `us-east-2`, with API Gateway stage `prd`:

- Two Python Lambdas: the Google API proxy and the owner-onboarding handler. The
  latter also dispatches DynamoDB stream events and runs delayed verification.
- One API Gateway REST API: managed Google routes require IAM SigV4 and a
  usage-plan API key; `/prd/onboarding/` uses its own owner session through the
  existing `auth.k-rae.com` Authentik service.
- A retained DynamoDB metadata/operation table with a stream and TTL, plus an
  encrypted SQS verification queue and dead-letter queue. Closing the setup page
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
Docker must be available for packaging. The app uses
`CliCredentialsStackSynthesizer`, so it deploys with this explicit Developer
identity and uses the existing CDK bootstrap asset bucket without assuming
bootstrap roles.

The generated CDK outputs in `env/gogcli-prd/access.json` are grouped under
`GogcliGoogleProxyApi` and contain these non-secret access facts:

| Output | Use |
| --- | --- |
| `ProxyBaseUrl` | CLI proxy base URL, ending in `/prd/` |
| `ApiKeyId` | Identifier for the usage-plan key; not its secret value |
| `ProxyInvokeArn` | Scope for the canonical JDev Gmail invoke binding |
| `SetupUrl` | Published HTTPS owner setup page, ending in `/prd/onboarding/` |
| `GoogleCallbackUrl` | Exact redirect URI for the Google Web application client |
| `OwnerCallbackUrl` | Exact Authentik redirect URI |
| `OnboardingTable` | Persisted checklist and operation metadata table |
| `OnboardingRoleArn` | Application role used by signed verification reads |

Apply the API-specific Gmail invoke scope through the canonical JDev deployment
runner to both the Developer identity and created-role boundary. Keep the grant
limited to this API's `prd` Gmail routes; account enrollment adds explicit email
allowlist entries for the CLI and verifier.

## Configure owner login and connect Gmail

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

Open `SetupUrl` and follow the [remote Gmail account guide](../docs/account-onboarding.md).
Create a Google **Web application** client with the displayed `GoogleCallbackUrl`,
publish the consent screen before connecting accounts, and upload the downloaded
JSON through the owner page. Each account completes Google consent separately.
Saved authorization is promoted only after candidate refresh and Gmail reads
pass. The worker then waits at least 65 seconds before checking profile and a
one-message ID-only listing through the normal signed proxy; an empty mailbox
passes. Failed verification retains authorization for a retry without consent.

For the first CLI caller, fetch the usage-plan key using `ApiKeyId` from the
generated access record:

```bash
aws --profile krae-jdev-dev-prd --region us-east-2 apigateway get-api-key \
  --api-key '<ApiKeyId>' --include-value --query value --output text
```

Configure `proxy_base_url` from `ProxyBaseUrl`, `proxy_api_key` from the returned
value, `aws_profile` as `krae-jdev-dev-prd`, and a verified `default_account` in the
CLI's `config.json`. Run `bin/gog config path` to locate it. Google credentials
stay on the server; the page's final section and the account guide provide the
first read-only CLI check.

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
- Email/domain matching is case-insensitive. `emails` entries may also be `"*"` to allow any email for that caller.

### Existing assumed-role allowlist and policy migration

Before deploying this handler to an existing proxy, create the allowlist and caller-policy parameters under the base64url-encoded stable role identity. The obsolete keys encoded only the STS session name; this handler never reads them. For example, encode `arn:aws:iam::123456789012:role/proxy-agent`, then copy the intended allowlist to `<prefix>/allow/<new_key>` and caller policy to `<policy_prefix>/caller/<new_key>`. Review the combined permissions for that role rather than combining session allowlists automatically. Update any policy `on_match.detail` values or downstream consumers that relied on session names in emitted `caller_id` metadata, and retire the obsolete session-name parameters after rollout.

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
- SSM account configuration is refreshed after its existing 60-second cache TTL. A changed credential configuration or default scope list invalidates that account's cached token when the new configuration is observed.

## Policy Enforcement (Lambda)

Policy is enforced in the proxy Lambda (deny-by-default) when `GOG_POLICY_ENABLED=true` (default).

Policy SSM prefix defaults to:

- `<GOG_SSM_PREFIX>/policy` (for example `/gogcli-proxy/policy`)

Policy document lookup order:

1. `<policy_prefix>/default`
2. `<policy_prefix>/caller/<caller_id_enc>`
3. `<policy_prefix>/account/<email_enc>`

If no policy document exists, requests are denied with `{"error":"policy_not_found"}`.

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

- Rules are evaluated by `priority` desc, then source specificity (account > caller > default), then declaration order.
- First matching rule wins.
- Unknown keys are rejected (fail closed).

### Policy trigger events (optional)

Enable match-triggered events:

```bash
export GOG_POLICY_EVENTS_ENABLED=true
export GOG_POLICY_EVENT_BUS_ARN='arn:aws:events:...:event-bus/your-bus'
```

When enabled and a matching rule has `on_match.emit_event=true`, Lambda emits an EventBridge event with decision metadata.
Trigger failures are logged but do not change allow/deny results.

## Routing, payloads, and timeouts

`X-GOG-Google-Host` is required and validated against the supported Google API host allowlist, `docs.google.com`, or a valid subdomain of `googleusercontent.com` for Photos Picker media. The original path is preserved, including upload prefixes and API versions. Path-only host inference is obsolete and has been removed. Upgrade clients and the handler together; older clients without the signed host header receive HTTP 400. Proxy authentication/account/routing headers are removed before forwarding, and Google redirects are returned to the client for another signed request.

The request body cap is 4 MiB. `GOG_MAX_REQUEST_BYTES` may lower it, but cannot raise it. This leaves room for ordinary API Gateway event/base64 overhead below Lambda's 6 MiB invocation limit. Gmail's upstream 35 MiB raw-message limit does not apply to this proxy; the proxy body cap includes MIME/base64/JSON content transmitted to Google.

Responses have both a streamed raw-body cap (`GOG_MAX_RESPONSE_BYTES`, default 5,500,000 bytes) and a hard 6 MiB cap on the serialized Lambda response, including headers, base64 expansion, JSON escaping, and the response envelope. Oversized responses return HTTP 502, and upstream response streams are always closed.

Token minting and forwarding share a request budget capped at 27 seconds, or two seconds less than the Lambda invocation's remaining time if shorter. Connect/read timeouts use each phase's remaining budget, token refresh retries are bounded by its refresh deadline, and streamed chunks are checked against the request deadline. Expired budgets return HTTP 504 before starting another phase.

Run the offline Lambda contract and policy checks with the bundled runtime dependencies:

```bash
uv run --python 3.11 --with boto3 --with-requirements infra/lambda/google_proxy/requirements.txt python -m unittest discover -s infra/lambda/google_proxy -v
```

Maps/Places and public YouTube API-key modes also retain upstream project credentials and do not consume managed-account Google OAuth tokens. These ancillary API-key requests go directly to their provider; account-authenticated Google API requests use the proxy.
