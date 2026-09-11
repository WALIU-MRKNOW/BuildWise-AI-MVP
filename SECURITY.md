# BuildWise AI Security Policy

BuildWise AI treats security reports as confidential engineering issues.

## Reporting a vulnerability

Do not post exploit details, credentials, private user data, or proof-of-concept payloads in a public issue.

Prefer GitHub's private vulnerability reporting / Security Advisory flow for this repository when available. If that channel is unavailable, contact the repository owner privately and include only the minimum information needed to reproduce the issue safely.

A useful report includes:

- the affected BuildWise component or endpoint;
- the security impact;
- safe reproduction steps;
- whether credentials or user data may have been exposed;
- suggested mitigations, if known.

## Security boundaries

Production secrets belong only in the deployment provider's encrypted environment-variable store. They must never be committed to the repository, placed in client-side code, printed in logs, or copied into support messages.

The Telegram webhook must remain authenticated with Telegram's secret-token header. Internal cron endpoints must remain protected by a separate high-entropy secret. Supabase service-role credentials are server-only. BuildWise Pro and admin access controls must remain deny-by-default.

## Dependency and code scanning

Pull requests and `main` are scanned with dependency auditing and static security analysis. Dependabot checks Python and GitHub Actions dependencies weekly.

## Incident response

If a production credential is suspected to be exposed, rotate/revoke it immediately, review deployment and application logs for suspicious activity, invalidate affected sessions where applicable, and document the incident and remediation before restoring normal access.
