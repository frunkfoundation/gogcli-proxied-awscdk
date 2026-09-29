# gogcli-proxy Infra (CDK)

This folder contains a small Python AWS CDK app that deploys:

- 1 Lambda: Google API proxy (injects Google credentials based on `X-GOG-Account`)
- 1 API Gateway REST API: `ANY /{proxy+}` to the Lambda
  - Authorization: IAM (SigV4)
  - API key required (usage plan)

The gogcli client is already wired to:

- send `X-GOG-Account: <email>`
- send signed `X-GOG-Google-Host: <original-google-host>` and preserve the original Google request path
- send `x-api-key: <api-key>`
- SigV4-sign the request for `execute-api`

Managed account API requests, including Gmail history reads during watch processing, use this proxy. `gog gmail watch pull` separately connects to Google Cloud Pub/Sub through its native subscriber client and Application Default Credentials. Those credentials authorize subscription reads and acknowledgements; they do not supply the managed Gmail account's OAuth token. See `internal/cmd/gmail_watch_pull.go` for the subscriber setup.

## Deploy

From `infra/`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cdk bootstrap
cdk deploy
```

Outputs include:

- `ProxyBaseUrl`: set this as `GOG_PROXY_BASE_URL` in the client
- `ApiKeyId`: use AWS CLI to fetch the API key value

Fetch API key value:

```bash
aws apigateway get-api-key --api-key <ApiKeyId> --include-value
```

Client env:

```bash
export GOG_PROXY_BASE_URL='https://abc123.execute-api.us-east-1.amazonaws.com/prod/'
export GOG_PROXY_API_KEY='...value from get-api-key...'
```

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
uv run --python 3.11 --with-requirements infra/lambda/google_proxy/requirements.txt python -m unittest discover -s infra/lambda/google_proxy -v
```

Maps/Places and public YouTube API-key modes also retain upstream project credentials and do not consume managed-account Google OAuth tokens. These ancillary API-key requests go directly to their provider; account-authenticated Google API requests use the proxy.
