# Roles and permissions
Access is role-based: every staff account has one role, a role is a set of permissions, and every
dashboard route requires a permission. Nothing is decided in the browser — the backend checks every request.

## Permissions
| Permission | Allows |
|---|---|
| `tickets.view` | See tickets of their departments, unassigned tickets and tickets assigned to them |
| `tickets.view_all` | See every ticket |
| `tickets.reply` | Answer users in tickets — in the dashboard and from Telegram |
| `tickets.notes` | Read and write internal notes — in the dashboard and from Telegram |
| `tickets.update` | Change ticket status, priority and category |
| `tickets.assign` | Assign and transfer tickets between agents and departments; receive escalations |
| `tickets.reopen` | Reopen resolved or closed tickets |
| `users.view` | See user profiles |
| `users.sensitive` | See and write down the contact details users supplied (phone, e-mail, country, time zone) |
| `users.edit` | Edit user tags and notes |
| `users.ban` | Ban and unban users |
| `users.message` | Send direct messages to users |
| `users.export` | Export all data of a user (JSON) |
| `users.delete` | Anonymize a user |
| `faq.edit` | Edit the FAQ tree (draft) |
| `faq.publish` | Publish the FAQ draft to the bot, restore old versions |
| `content.edit` | Welcome message, main menu, bot texts and translations, media library |
| `templates.edit` | Response templates |
| `campaigns.edit` | Prepare announcements |
| `campaigns.send` | Send, schedule, pause and retry announcements |
| `analytics.view` | See reports |
| `analytics.export` | Export reports (CSV, Excel, PDF) |
| `settings.edit` | Departments, categories, statuses and transitions, SLA, business hours, routing rules, languages, general settings |
| `team.manage` | Staff accounts and roles |
| `audit.view` | Read the audit log |
| `system.manage` | Backups, monitoring, system errors |

The source of truth is `backend/services/permissions.py`; the route → permission table is `RULES` in
`backend/services/team.py`. A test (`tests/integration/test_auth_and_rules.py`) fails if any dashboard route has
no rule, so a new endpoint cannot be added open by mistake.

## Built-in roles
| Role | Permissions |
|---|---|
| **Super Administrator** | everything |
| **Support Supervisor** | `tickets.*` (all seven), `users.view`, `users.sensitive`, `users.edit`, `users.ban`, `users.message`, `templates.edit`, `analytics.view`, `analytics.export`, `audit.view` |
| **Support Agent** | `tickets.view`, `tickets.reply`, `tickets.notes`, `tickets.update`, `users.view`, `users.edit`, `users.message` |
| **Content Manager** | `faq.edit`, `faq.publish`, `content.edit`, `templates.edit`, `campaigns.edit`, `analytics.view` |
| **Reporting Administrator** | `analytics.view`, `analytics.export` |

Built-in roles are restored to this definition on every start, so an upgrade can never silently widen them. For a
different set, create a **custom role** (Team → Roles) with any permissions.

## Rules that always apply
* **Minimum access.** An agent sees only tickets of their departments, unassigned tickets and their own — in lists,
  search, the user profile, reports and the overview. Agents do not see users' contact details.
* **No escalation of privileges.** Nobody can create a role or give an account permissions they do not hold
  themselves, change their own role or block themselves. The last active Super Administrator cannot be removed.
* **Sessions end on change.** A new password, role or block signs that person out everywhere at once.
* **Telegram has the same rules.** A staff member who linked Telegram (Profile → Telegram notifications) answers
  from the bot only on tickets they can see and only with `tickets.reply` / `tickets.notes`. Unlinking or blocking
  the account stops it at once.
* **Deleted accounts keep history.** An account that already acted is blocked and renamed instead of deleted, so
  the audit log and ticket history keep showing who did what.

## Audit log (Audit page, `audit.view`)
Recorded with the person, time, IP address and details:

| Area | Actions |
|---|---|
| Sign-in | successful and failed sign-ins, password change, two-factor on/off, Telegram account linked |
| Team | account created, changed (role, block, unlock, password or 2FA reset, departments), deleted; role saved or deleted (permission changes) |
| Tickets | every status, priority, department, category and assignment change (also in the ticket's own history) |
| Users | ban, unban, export, anonymization, contact details written by staff |
| Content | FAQ published or restored, welcome message, main menu, bot texts |
| Announcements | start, schedule, pause, resume, cancel, retry |
| Reports | every export (format and filters) |
| Settings | every change of general settings, languages, departments, categories, statuses, transitions, SLA, business hours and routing rules |
| System | backup requested or downloaded, error log cleared |

How long the log is kept, and the other retention rules, are described in [SECURITY.md](SECURITY.md).

## Typical set-ups
* **Small team.** One Super Administrator, everyone else Support Agent without departments: every agent sees every
  unassigned ticket; whoever answers first takes it.
* **Departments.** Agents are members of departments (Team → account → Departments). Routing rules (Settings →
  Routing) send tickets to a department and pick an agent (least loaded or round robin, by language). A Support
  Supervisor sees everything and receives escalations and overdue alerts.
* **Outsourced content.** A Content Manager edits the FAQ and announcements but cannot see tickets or users.
