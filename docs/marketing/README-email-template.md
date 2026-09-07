# Augusta Search — welcome email template

## File

`augusta-search-welcome-email.html`

Layout inspired by professional onboarding emails (hero, greeting, 3 feature cards, tips band, footer) — styled for **Augusta Search** (navy + gold).

## Placeholders (replace before sending)

| Placeholder | Example |
|-------------|---------|
| `{{first_name}}` | David |
| `{{cta_url}}` | `https://ausstd.augustasearch.com/login` |
| `{{unsubscribe_url}}` | From your email provider |
| `{{view_in_browser_url}}` | Campaign “view online” link |
| `{{privacy_url}}` | `https://www.augustasearch.com/privacy` |
| `{{terms_url}}` | `https://www.augustasearch.com/terms` |
| `{{support_email}}` | `naajm@augustasearch.com` |
| `{{company_address}}` | Your business address (optional) |

Automated send (production): backend `welcome_email_service` uses `backend/templates/welcome_email.html` via SMTP.
Set `SMTP_*` + legal URLs on VPS; column `profiles.welcome_email_sent_at` prevents duplicates.

## Preview locally

1. Open `augusta-search-welcome-email.html` in Chrome.
2. Replace placeholders with sample text.
3. Send a test from your provider (Brevo, Mailgun, etc.).

## Use with Brevo (like the email you received)

1. Brevo → **Campaigns** or **Automations** → create email.
2. **Import / paste HTML** (or use “Code your own”).
3. Paste contents of this file.
4. Map merge tags: Brevo uses `{FIRSTNAME}` — find/replace `{{first_name}}` to match Brevo syntax if needed.
5. Send test to yourself.

## Use with Mailgun (your DNS already has Mailgun)

Mailgun → **Sending** → **Templates** → create template → paste HTML.

## Logo & hero image

- **Logo:** `https://ausstd.augustasearch.com/img/NewOfficetools.png` (on cream panel in template).
- **Hero photo:** default is a stock Unsplash image — replace the `src=` in the hero `<img>` with your own hosted image (engineering/standards themed).

## Mobile

Uses table layout for Outlook/Gmail. On narrow screens some clients stack columns automatically; for perfect mobile stacking, use your provider’s responsive editor or Brevo’s drag-and-drop blocks built from this design.
