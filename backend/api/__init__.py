from backend.api import analytics, auth, campaigns, content, health, internal, me, settings, system, team, tickets, users

routers = [health.router, auth.router, me.router, tickets.router, users.router, campaigns.router, content.router, analytics.router,
           settings.router, team.router, system.router, internal.router]
