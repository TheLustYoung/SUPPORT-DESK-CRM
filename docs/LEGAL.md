# Legal design of the support desk
How the platform handles personal data and the duties of the operator. For operators and reviewers; not legal advice.

## Personal data the platform keeps
- From Telegram: user id, @username, first and last name, Telegram language, chosen language.
- From the conversation: ticket messages and attachments, FAQ choices, delivery statuses.
- Contacts (e-mail, phone, country, time zone) **only when the user gives them** (bot → «My contacts») or a staff
  member with the `users.sensitive` permission writes down what the user said. Viewing them needs that permission and
  is written to the audit log.
- Staff accounts: username, password hash (Argon2), optional 2FA secret, sign-in history.

## Rights of the user
- **Access / export**: Users → profile → Export (CSV/XLSX), audited.
- **Erasure**: Users → profile → Delete or Anonymise; tickets keep their statistics without personal data.
- **Opt-out of announcements**: `/stop` in the bot; banned and opted-out users never receive campaigns.
- **Retention**: closed tickets, error logs and staff notifications are cleaned after the configured periods
  (Settings → General); the audit log is kept.

## First contact
On the first `/start` the bot shows a privacy notice (Settings → Bot content). Replace the template text with the
operator's own notice and a link to the full privacy policy before going live.

## Operator checklist before going live
- Publish a privacy policy: who the operator is, what is stored, why, for how long, how to request access/erasure,
  and the contact for privacy requests.
- Check the rules that apply to the operator's users: GDPR/UK GDPR (EU/UK), CCPA/CPRA (California), other local laws.
  Sign a data processing agreement with the hosting provider when required.
- Keep backups encrypted and access-limited (see docs/BACKUP_RESTORE.md); restoring a backup must respect erasures.
- Follow Telegram's Bot Platform rules: no unsolicited messages to users who did not start the bot; announcements
  only to users who did not opt out.
- Use HTTPS for the dashboard and the webhook (docs/DEPLOYMENT.md).
