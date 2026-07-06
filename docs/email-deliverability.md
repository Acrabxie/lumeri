# Email Deliverability

Lumeri email-code login uses SMTP from local config or environment variables.
Outlook is strict about sender identity, so the default is conservative:

- SMTP envelope sender is always `smtp.username`.
- Visible `From:` is also aligned to `smtp.username` unless a verified alias is explicitly enabled.
- One-time-code emails include transactional auto-response suppression headers.

Example local config:

```json
{
  "smtp": {
    "host": "smtp.gmail.com",
    "port": 587,
    "username": "sender@example.com",
    "password": "app-specific-password",
    "from_name": "Lumeri",
    "from_addr": "sender@example.com",
    "starttls": true
  }
}
```

Only use a branded sender such as `login@lumeri.dev` after the SMTP provider has verified that alias or domain and DNS has passing SPF, DKIM, and DMARC:

```json
{
  "smtp": {
    "from_addr": "login@lumeri.dev",
    "allow_from_alias": true
  }
}
```

If Outlook still sends codes to junk, check the message's authentication results in Outlook. SPF, DKIM, and DMARC should pass and align with the visible sender domain.
