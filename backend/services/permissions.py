"""Role-based access: the permissions that exist and the built-in roles of the specification.

Built-in roles always match this file (updated on start). Custom roles pick any set of permissions. Nobody can grant
more than they hold themselves (see backend.services.team).
"""

PERMISSIONS: dict[str, str] = {
    # tickets
    "tickets.view": "See tickets of their departments, unassigned tickets and tickets assigned to them",
    "tickets.view_all": "See every ticket",
    "tickets.reply": "Answer users in tickets",
    "tickets.notes": "Read and write internal notes",
    "tickets.update": "Change ticket status, priority and category",
    "tickets.assign": "Assign and transfer tickets between agents and departments; escalations",
    "tickets.reopen": "Reopen closed tickets",
    # users
    "users.view": "See user profiles",
    "users.sensitive": "See contact details users supplied (phone, e-mail)",
    "users.edit": "Edit user tags and notes",
    "users.ban": "Ban and unban users",
    "users.message": "Send direct messages to users",
    "users.export": "Export user data",
    "users.delete": "Delete or anonymize user data",
    # content
    "faq.edit": "Edit the FAQ tree",
    "faq.publish": "Publish FAQ changes to the bot",
    "content.edit": "Edit the welcome message, main menu, bot texts, translations and media",
    "templates.edit": "Manage response templates",
    # campaigns
    "campaigns.edit": "Prepare announcements",
    "campaigns.send": "Send and schedule announcements",
    # reports
    "analytics.view": "See reports",
    "analytics.export": "Export reports",
    # administration
    "settings.edit": "Departments, categories, statuses, SLA, routing rules, languages and general settings",
    "team.manage": "Staff accounts and roles",
    "audit.view": "Read the audit log",
    "system.manage": "Backups, monitoring and system errors",
}
ALL = frozenset(PERMISSIONS)

AGENT = ["tickets.view", "tickets.reply", "tickets.notes", "tickets.update", "users.view", "users.edit", "users.message"]
BUILTIN_ROLES: dict[str, tuple[str, str, list[str]]] = {
    "super_admin": ("Super Administrator", "Full system access; system configuration, users, roles, languages and integrations",
                    list(PERMISSIONS)),
    "supervisor": ("Support Supervisor", "Tickets, assignments, escalations, teams and support performance", [
        "tickets.view", "tickets.view_all", "tickets.reply", "tickets.notes", "tickets.update", "tickets.assign", "tickets.reopen",
        "users.view", "users.sensitive", "users.edit", "users.ban", "users.message", "templates.edit", "analytics.view",
        "analytics.export", "audit.view"]),
    "agent": ("Support Agent", "Permitted tickets: respond to users, update statuses, internal notes", AGENT),
    "content_manager": ("Content Manager", "Welcome messages, FAQ trees, media attachments, translations and response templates", [
        "faq.edit", "faq.publish", "content.edit", "templates.edit", "campaigns.edit", "analytics.view"]),
    "reporting_admin": ("Reporting Administrator", "Authorized analytics and permitted report exports", ["analytics.view", "analytics.export"]),
}
