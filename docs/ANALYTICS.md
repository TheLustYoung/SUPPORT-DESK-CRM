# Analytics — calculation rules
Every number in **Analytics** and on the **Overview** page is computed by `backend/services/analytics.py` exactly as
described here (every figure has one documented rule). The integration tests
(`tests/integration/test_users_and_reports.py`) build known data and check these rules.

## Common rules
| Rule | Value |
|---|---|
| Time zone | All moments are stored and compared in **UTC**. A day in a report is a UTC calendar day. |
| Period | `From`–`To` are inclusive calendar days: `[From 00:00 UTC, To + 1 day 00:00 UTC)`. Without dates: the last 30 days, today included. |
| Which tickets | Ticket reports take tickets **created** in the period. A ticket created on 1 May and resolved on 3 May belongs to a report for May 1. |
| Filters | Combine with AND. **Tickets** and **Agents**: language (the ticket's language), department, agent (current assignee), priority, status (exact status key). **Users**, **FAQ**, **Announcements**: period and language only (the other filters describe tickets and are hidden there). In those reports the language is the user's chosen bot language, else their Telegram language (`pt` also matches `pt-br`); for announcements, the language each recipient received. |
| Durations | Seconds in exports, shown as minutes/hours/days in the dashboard. |
| Average / median | Arithmetic mean and median of the tickets that **have** the measured moment. Tickets still waiting are not counted as zero and not counted at all — the `count` next to each value says how many tickets were measured. Both values are rounded to whole seconds. |
| Stages | Reports use the **stage** of a status (new, open, pending agent, pending user, escalated, resolved, closed, reopened), so renamed or custom statuses are counted correctly. *Final* stages: resolved and closed. |

## Ticket analytics
| Metric | Rule |
|---|---|
| Total tickets created | Tickets created in the period that match the filters. |
| By status / priority / category / department | Count of those tickets per current status key, priority, category, department (empty = none). |
| Open | Tickets of the period whose current stage is **not** resolved or closed. |
| Overdue | Open tickets (as above) with a breached first-response **or** resolution target. |
| Resolved | Tickets of the period that have a resolution moment (`resolved_at`), even if later closed or reopened. |
| Reopened | Tickets of the period reopened at least once (`reopen_count > 0`). |
| First-response time | `first_response_at − created_at`. `first_response_at` is the moment of the **first agent reply sent to the user** (from the dashboard or from Telegram). Internal notes and automatic system messages do not count. |
| Resolution time | `resolved_at − created_at`. `resolved_at` is set when the ticket enters a *resolved* stage, or when it is closed without being resolved first; reopening clears it, and the next resolution sets it again — so the time always measures the latest resolution. |
| SLA compliance | `targets met ÷ targets measured × 100`, one decimal. Each ticket contributes up to two targets: first response and resolution. A target is **measured** when the ticket has that deadline and either reached the moment or is already breached; a target still running is not measured. **Met** = measured and not breached. |
| Customer satisfaction (CSAT) | Mean of the 1–5 star ratings of the period's tickets, two decimals; `ratings` = how many tickets were rated. |
| Volume trend | For every day of the period: tickets created that day and tickets (of the period) resolved that day. |

### How SLA deadlines are computed
* The targets per priority are set in **Business hours & SLA** (minutes to first response and to resolution).
* With **Count business hours only** on, the deadline adds the target minutes inside the working hours of the
  configured time zone (nights, days off are skipped). Off: calendar minutes.
* With **Pause while waiting for the user** on, the time a ticket spends in a *pending user* stage is added to both
  deadlines when the user answers; a paused ticket is never marked overdue.
* The scheduler checks deadlines every minute; a target is *breached* when its deadline passes before the moment
  is reached. A breach is permanent for that ticket, also after it is answered.
* **Early warning:** when `sla_warning_percent` (80 % by default) of a target's time has passed and the deadline is
  still ahead, the ticket's agent (or its department) is warned once per target, with the minutes left.
* Changing the hours or the targets recalculates the deadlines of open tickets.

## User analytics
| Metric | Rule |
|---|---|
| Total users | Every user who ever started the bot (all time, not limited by the period). |
| New users | Users whose first contact (`first_seen`) is in the period. |
| Active users | Users with any recorded activity in the period: a bot event (start, FAQ question opened, search, language change, rating, contacts…) **or** a message written to support. |
| Returning users | Active users who are not new in the period. |
| Activity by language | Active users grouped by their chosen bot language, else their Telegram language. |
| Tickets per user | Tickets of the period ÷ users who created at least one of them (two decimals). |
| Most common issues | Top 10 categories of the period's tickets. |

## FAQ analytics
| Metric | Rule |
|---|---|
| Most visited questions | Count of *FAQ question opened* events per question in the period (every opening counts, also repeated ones by the same user). |
| Most selected categories | The same events counted by the top-level branch of the opened question. |
| Navigation paths | Full path (Category › Sub-category › Question) of every opened **answer** (a question without sub-questions), counted per path. |
| Questions that lead to tickets | Tickets of the period created with «Contact support» under an FAQ answer, counted per that answer. |
| Missing content | Free-text questions for which the FAQ search found nothing (*search miss*), grouped case-insensitively by text (first 100 characters). |

## Agent and campaign analytics
| Metric | Rule |
|---|---|
| Tickets handled per agent | Tickets of the period by current assignee (unassigned shown separately). |
| Replies | Messages sent to users by the agent in the period (dashboard and Telegram). |
| Open now | Tickets currently assigned to the agent in a non-final stage, any creation date. |
| Response / resolution performance | Average and median as in the ticket report, per agent and per department (team). |
| SLA compliance by agent or team | First-response targets met ÷ measured for that agent's / department's tickets. |
| Workload distribution | *Open now* per agent, also on the Overview page. |
| Campaign delivery statistics | Per announcement created in the period: recipients and deliveries per status (sent, failed, blocked, skipped, pending). |
| Failed messages and error categories | Failed or blocked replies, direct messages and announcement deliveries of the period, grouped by the Telegram error text. |

## Overview page
Limited to the tickets the signed-in staff member may see (their role and departments).

* Ticket counters by stage — all visible tickets, any age. *Overdue* — visible non-final tickets with a breach.
* First response, resolution, SLA and CSAT — the ticket report for the last 7 days.
* Users: total, and active in the last 7 days (`last_seen` in the last 7 days).
* Messages in the last 24 hours: incoming (from users) and outgoing (agent replies, direct and system messages).
* Delivery failures and system errors in the last 24 hours; messages still in the delivery queue.

## Exports
CSV (UTF-8 with BOM, opens correctly in Excel for Arabic, Hebrew and Cyrillic), Excel (one sheet per table) and PDF
(one table per section). Exports contain exactly the numbers shown on screen for the same filters, with categories,
departments and FAQ questions by name in the dashboard's language, and are recorded in the audit log. Exporting
requires the `analytics.export` permission.

## Weekly summary
Every Monday from 09:00 (time zone of Business hours; on Tuesday if the server was down on Monday), staff with
`analytics.view` get the ticket report of the previous seven days (Monday–Sunday) — tickets created, resolved, open,
overdue, SLA compliance and CSAT, computed exactly as above — in the dashboard bell and in Telegram. Settings →
General → Reminders and reports → *Weekly summary*.
