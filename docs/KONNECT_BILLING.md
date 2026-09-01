# Konnect billing deployment

CreditFlow uses Konnect only through the generic billing provider adapter. Configure secrets in the site's private `site_config.json` or environment-backed Frappe config; never expose them to browser code:

- `creditflow_konnect_api_key`
- `creditflow_konnect_receiver_wallet_id`
- `creditflow_konnect_sandbox` (`1` for sandbox, `0` for production)
- `creditflow_public_base_url` (public HTTPS origin, without a trailing slash)

The webhook registered during checkout is:
`https://YOUR_HOST/api/method/creditflow.billing.api.konnect_webhook`

Sandbox uses `https://api.sandbox.konnect.network/api/v2`; production uses `https://api.konnect.network/api/v2`. Complete Konnect's merchant KYC/KYB and obtain the matching API key and receiver wallet before production use.

## Sandbox smoke test

1. Configure sandbox credentials and an HTTPS public URL, migrate, build, and restart workers/web.
2. Sign in as a CreditFlow OWNER, open `/creditflow-subscription`, select an enabled plan, and start checkout.
3. Complete one successful and one failed hosted sandbox payment. Confirm that only the server-verified successful payment activates the subscription.
4. Confirm the webhook reaches the URL above, then replay it and verify the paid period is unchanged.
5. Temporarily block the webhook, finish another payment, and use the payment-history refresh action to verify reconciliation recovers it.

Automatic recurring billing is intentionally not enabled. The public Konnect material describes recurring profiles but does not provide a sufficiently precise endpoint contract here; CreditFlow therefore uses idempotent manual monthly/annual renewal checkout until that contract is available and validated.
