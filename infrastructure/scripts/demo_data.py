"""Synthetic demo data for local presentations and screenshots. NEVER for production.

    DEMO_DATA=1 python -m infrastructure.scripts.dev      (fills an empty local database once)
    python -m infrastructure.scripts.demo_data --clear    (removes it again)

Everything is clearly marked: Telegram ids from 900 000 000, usernames ``demo_*``, ticket dedupe keys ``demo:*``.
Tickets go through the real ticket service (routing and SLA are computed as usual); agents' answers are stored as
already delivered, so nothing is ever sent to Telegram. A fixed random seed keeps screenshots reproducible.
"""
import random
import sys
from datetime import timedelta
from sqlalchemy import delete, select
from backend.database import Session
from backend.models import Message, Staff, Ticket, TgUser, now
from backend.services import tickets

DEMO_ID = 900_000_000
PEOPLE = [("Emma", "Johnson"), ("Liam", "Garcia"), ("Olivia", "Martinez"), ("Noah", "Brown"), ("Ava", "Davis"), ("Lucas", "Silva"),
          ("Mia", "Rossi"), ("Ethan", "Kim"), ("Sofia", "Lopez"), ("Mateo", "Fernandez"), ("Amara", "Okafor"), ("Yuki", "Tanaka"),
          ("Zara", "Khan"), ("Leo", "Novak"), ("Ines", "Moreau"), ("Omar", "Haddad"), ("Chloe", "Dubois"), ("Arjun", "Mehta")]
CASES = [
    ("Payment went through but the order is still pending", "I paid by card 20 minutes ago, the money is gone but the order shows «pending».",
     "Thanks for the details! I can see the payment on our side; the order is confirmed now. You'll get a message within a minute."),
    ("Can't log in after changing my phone", "I moved to a new phone and the app asks for a code I never receive.",
     "We've reset the sign-in for your account. Open the app again and choose «Send code by Telegram»."),
    ("Wrong size delivered", "I ordered M and received XL. How do I exchange it?",
     "Sorry about that! I've created a free exchange. A courier will pick up the parcel tomorrow between 10:00 and 14:00."),
    ("How to change the delivery address?", "Is it possible to change the address for an order that's already been placed?",
     "Yes — as long as it hasn't shipped. I've updated it to the address you sent; you'll see it in «My orders»."),
    ("Refund status", "I returned an item last week. When will the refund arrive?",
     "The refund was issued today. Banks usually show it within 3–5 business days."),
    ("Promo code doesn't work", "The code WELCOME10 says it's invalid.",
     "That code is for first orders only. I've added a 10% code to your account that works on this one: it's applied at checkout."),
    ("App crashes when I open the cart", "Every time I tap the cart icon the app closes. Android 14.",
     "Thanks for the report — our team reproduced it and a fix is in today's update. Please update the app from the store."),
    ("Invoice for my company", "I need an invoice with our company details for the last order.",
     "Here you go — the invoice is attached in «My orders → Documents». Let me know if any details need changing."),
]
LANGS = ["en", "en", "en", "es", "fr", "pt", "ru", "ar"]


def has_demo(db) -> bool:
    return db.scalar(select(TgUser.id).where(TgUser.id >= DEMO_ID).limit(1)) is not None


def clear(db) -> None:
    demo_tickets = select(Ticket.id).where(Ticket.dedupe_key.like("demo:%"))
    db.execute(delete(Message).where(Message.ticket_id.in_(demo_tickets)))
    db.execute(delete(Ticket).where(Ticket.dedupe_key.like("demo:%")))
    db.execute(delete(Message).where(Message.user_id >= DEMO_ID))
    db.execute(delete(TgUser).where(TgUser.id >= DEMO_ID))
    db.commit()


def fill(db, count: int = 42, seed: int = 2026) -> int:
    rnd = random.Random(seed)
    agents = db.scalars(select(Staff).where(Staff.active.is_(True))).all() if hasattr(Staff, "active") else db.scalars(select(Staff)).all()
    stages = {stage: tickets.keys_of(db, {stage}) for stage in ("new", "open", "pending_agent", "pending_user", "resolved", "closed")}
    users = []
    for n, (first, last) in enumerate(PEOPLE):
        lang = rnd.choice(LANGS)
        user = TgUser(id=DEMO_ID + n, username=f"demo_{first.lower()}", first_name=first, last_name=last, tg_language=lang,
                      language=lang, first_seen=now() - timedelta(days=rnd.randint(5, 60)), last_seen=now() - timedelta(hours=rnd.randint(0, 72)),
                      interactions=rnd.randint(3, 40))
        db.add(user)
        users.append(user)
    db.flush()
    made = 0
    for n in range(count):
        user = rnd.choice(users)
        subject, question, answer = rnd.choice(CASES)
        ticket, _ = tickets.create(db, user, language=user.language or "en", subject=subject, dedupe_key=f"demo:{n}",
                                   priority=rnd.choices(["urgent", "high", "normal", "low"], [1, 3, 8, 3])[0])
        age = timedelta(days=rnd.uniform(0, 13), hours=rnd.uniform(0, 10))
        for field in ("created_at", "updated_at", "due_response_at", "due_resolution_at"):
            if getattr(ticket, field):
                setattr(ticket, field, getattr(ticket, field) - age)
        asked = tickets.add_incoming(db, ticket, user, kind="text", text=question, dedupe_key=f"demo:in:{n}", notify_staff=False)
        if asked:
            asked.created_at = ticket.last_user_message_at = ticket.created_at + timedelta(minutes=1)
        ticket.updated_at = ticket.created_at + timedelta(minutes=1)
        ticket.reminded_at = now()  # demo users are not real Telegram chats: no reminders are sent to them
        stage = rnd.choices(["new", "open", "pending_agent", "pending_user", "resolved", "closed"], [3, 3, 2, 2, 3, 4])[0]
        if agents and stage != "new":
            ticket.assignee_id = rnd.choice(agents).id
        if stage != "new" and agents:
            replied = ticket.created_at + timedelta(minutes=rnd.randint(4, 300))
            agent = next((a for a in agents if a.id == ticket.assignee_id), agents[0])
            db.add(Message(user_id=user.id, ticket_id=ticket.id, direction="out", staff_id=agent.id, author=(agent.display_name or agent.username)[:64],
                           text=answer, delivery="sent", created_at=replied, sent_at=replied))
            ticket.first_response_at = ticket.last_staff_message_at = replied
            ticket.response_breached = bool(ticket.due_response_at and replied > ticket.due_response_at)
        if stage in ("resolved", "closed"):
            ticket.resolved_at = ticket.created_at + timedelta(hours=rnd.uniform(1, 30))
            if stage == "closed":
                ticket.closed_at = ticket.resolved_at + timedelta(hours=24)
            if rnd.random() < 0.7:
                ticket.rating = rnd.choices([5, 4, 3, 2], [6, 3, 1, 0.4])[0]
        if stage in ("new", "open") and ticket.due_response_at and ticket.due_response_at < now():
            ticket.response_breached = True
        keys = stages.get(stage) or []
        if keys:
            ticket.status = keys[0]
        made += 1
    db.commit()
    return made


def main():
    with Session() as db:
        if "--clear" in sys.argv:
            clear(db)
            print("Demo data removed.")
        elif has_demo(db):
            print("Demo data already present.")
        else:
            print(f"Demo data: {fill(db)} tickets created.")


if __name__ == "__main__":
    main()
