from typing import Any 
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from sqlalchemy import update
from app.models import WaitlistEntry, TicketTier
from app.agents.base import BaseAgent
from app.agents.protocol import AgentMessage, Performative

class WaitlistAgent(BaseAgent):
    name = "WaitlistAgent"
    HOLD_MINUTES = 15

    def _utcnow(self):
        return datetime.now(timezone.utc).replace(tzinfo=None)

    def _allocate_freed_seats(self, db: Session, tier_id: int, bus: Any, conv_id: str) -> int:
        tier = db.query(TicketTier).filter_by(id=tier_id).first()
        if not tier or tier.remaining_qty <= 0:
            return 0

        waiting_list = (
            db.query(WaitlistEntry)
            .filter_by(tier_id=tier_id, status="waiting")
            .order_by(WaitlistEntry.created_at.asc())
            .all()
        )
        offers_made = 0
        for entry in waiting_list:
            if tier.remaining_qty >= entry.quantity:
                # Reserve seats by decrementing remaining_qty during the hold window
                stmt = (
                    update(TicketTier)
                    .where(TicketTier.id == tier_id, TicketTier.remaining_qty >= entry.quantity)
                    .values(remaining_qty=TicketTier.remaining_qty - entry.quantity)
                )
                res = db.execute(stmt)
                if res.rowcount == 1:
                    db.refresh(tier)
                    entry.status = "offered"
                    entry.hold_expires_at = self._utcnow() + timedelta(minutes=self.HOLD_MINUTES)
                    offers_made += 1
                    self.increment_belief(db, "holds_offered")

                    bus.send(
                        db,
                        AgentMessage(
                            conversation_id=conv_id,
                            sender=self.name,
                            receiver="ALL",
                            performative=Performative.INFORM,
                            action="WAITLIST_SEAT_OFFERED",
                            content={
                                "user_id": entry.user_id,
                                "event_title": entry.event.title,
                                "tier_name": tier.name,
                                "quantity": entry.quantity,
                                "expires_at": entry.hold_expires_at.strftime("%H:%M:%S UTC"),
                            },
                        ),
                    )
        db.flush()
        return offers_made

    def run_expiry_sweep(self, db: Session, bus: Any) -> int:
        """Proactive timer-driven behavior: expire unclaimed holds and re-offer to next in queue."""
        now = self._utcnow()
        expired_entries = (
            db.query(WaitlistEntry)
            .filter(WaitlistEntry.status == "offered", WaitlistEntry.hold_expires_at < now)
            .all()
        )
        if not expired_entries:
            return 0

        tiers_to_reoffer = set()
        for entry in expired_entries:
            entry.status = "expired"
            # Return held seats back to tier inventory
            db.execute(
                update(TicketTier)
                .where(TicketTier.id == entry.tier_id)
                .values(remaining_qty=TicketTier.remaining_qty + entry.quantity)
            )
            tiers_to_reoffer.add(entry.tier_id)
            self.increment_belief(db, "holds_expired")

            msg = AgentMessage(
                sender=self.name,
                receiver="ALL",
                performative=Performative.INFORM,
                action="WAITLIST_HOLD_EXPIRED",
                content={
                    "user_id": entry.user_id,
                    "waitlist_id": entry.id,
                    "event_title": entry.event.title,
                    "released_qty": entry.quantity,
                },
            )
            bus.send(db, msg)

        db.flush()
        for t_id in tiers_to_reoffer:
            self._allocate_freed_seats(db, t_id, bus, f"sweep-{int(now.timestamp())}")

        db.commit()
        return len(expired_entries)

    def handle_message(self, db: Session, msg: AgentMessage, bus: Any) -> AgentMessage:
        if msg.action == "JOIN_WAITLIST":
            user_id = int(msg.content["user_id"])
            event_id = int(msg.content["event_id"])
            tier_id = int(msg.content["tier_id"])
            qty = int(msg.content.get("quantity", 1))

            tier = db.query(TicketTier).filter_by(id=tier_id, event_id=event_id).first()
            if not tier:
                return self.reply(msg, Performative.REFUSE, {"reason": "Ticket category not found."})
            if tier.remaining_qty >= qty:
                return self.reply(
                    msg,
                    Performative.REFUSE,
                    {"reason": "Tickets are currently available! Book directly without joining the waitlist."},
                )

            existing = (
                db.query(WaitlistEntry)
                .filter(
                    WaitlistEntry.user_id == user_id,
                    WaitlistEntry.tier_id == tier_id,
                    WaitlistEntry.status.in_(["waiting", "offered"]),
                )
                .first()
            )
            if existing:
                return self.reply(msg, Performative.REFUSE, {"reason": "You are already on the waitlist for this category."})

            entry = WaitlistEntry(user_id=user_id, event_id=event_id, tier_id=tier_id, quantity=qty, status="waiting")
            db.add(entry)
            db.flush()
            self.increment_belief(db, "waitlist_joins")
            return self.reply(msg, Performative.AGREE, {"waitlist_id": entry.id, "reason": "Added to waitlist queue."})

        elif msg.performative == Performative.INFORM and msg.action == "SEATS_RELEASED":
            tier_id = int(msg.content["tier_id"])
            offers = self._allocate_freed_seats(db, tier_id, bus, msg.conversation_id)
            return self.reply(msg, Performative.INFORM, {"offers_made": offers})

        return self.reply(msg, Performative.FAILURE, {"reason": f"Unsupported action {msg.action}"})
