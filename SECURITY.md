# Security policy

`qbo-ar` can post payments to QuickBooks, so treat credentials and statements as sensitive.

## Reporting a vulnerability
Use GitHub's **Report a vulnerability** button on the Security tab (private advisory).
Please do not open a public issue for security problems. Expect an acknowledgement within
7 days.

## Handling secrets
- Never commit `.env` or anything in `~/.qbo_ar/`.
- Develop against an Intuit **sandbox** company with Development keys.
- Rotate the client secret immediately if it is ever pasted, screenshotted or logged.
- Every write is a dry run unless `--apply` or `--send` is passed.
