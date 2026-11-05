# Publish the existing Google app for personal use

Goal: External audience, **In production** publishing status, using Google's personal-use verification exemption. Keep the existing `gogcli-proxy` project, Web client, and permanent callback. Personal use concerns who uses the application; organization ownership does not require an Internal audience. Internal would exclude the personal Gmail account and accounts outside the owning organization.

Google describes personal-use applications as fewer than 100 users known personally to the developer. This instance is for the operator's own accounts and personally known users, not public self-service registration. Do not submit for full verification solely to leave Testing. Publishing and verification are separate states; an unverified-app warning and user cap may remain.

## Public pages and Branding

Release verified September 30, 2026 from commit `29daa2f8`: both public URLs below return HTTP 200 without authentication, exact committed assets and both Lambda ZIPs match, and stack/Lambda updates completed successfully. Account API sign-in and permanent OAuth callbacks remain unchanged. The operator must still save Branding and switch Google publishing to **In production**; AWS deployment does not change Google publishing status.

Deploy the public page change before supplying these URLs to Google. Pages must return HTTP 200 without authentication.

| Branding field | Value |
| --- | --- |
| App name | `gogcli-proxy` |
| User support email | `jay@kraecollective.com` |
| Homepage | `https://gp-039168171bf05570eadba6a8.k-rae.com/prd/onboarding/about` |
| Privacy policy | `https://gp-039168171bf05570eadba6a8.k-rae.com/prd/onboarding/privacy` |
| Authorized domain | `k-rae.com` |
| Developer contact email | `jay@kraecollective.com` |
| Logo / terms URL | Leave blank |

Open https://console.cloud.google.com/auth/branding?project=gogcli-proxy, enter the fields, and save. Then open https://console.cloud.google.com/auth/audience?project=gogcli-proxy. Keep **External**, choose **Publish app**, and confirm the resulting status is **In production**. Do not change project or create another client.

If Google still reports incomplete Branding, inspect the actual field validation and saved values. If Google requires a verification submission, read the specific message before proceeding: publishing for personal use does not itself require full verification.

## Reconnect after publishing

After status is **In production**, start fresh consent from the account connection page. Existing Testing-issued refresh tokens should not be assumed to become durable merely by changing publishing status. Confirm the connection and selected service checks; then continue other accounts.

In Production, the Testing-specific seven-day authorization/refresh-token expiry no longer applies. This does not promise perpetual credentials: ordinary revocation and other Google token-expiration rules still apply.

## References

- Audience/publishing and seven-day Testing expiry: https://support.google.com/cloud/answer/15549945
- Personal-use verification exception: https://developers.google.com/identity/protocols/oauth2/production-readiness/brand-verification#exceptions
- Branding: https://support.google.com/cloud/answer/15549049
