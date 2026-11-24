from datetime import datetime, timedelta, timezone
from typing import Any
import uuid

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.agents.base import BaseAgent
from app.agents.protocol import AgentMessage, Performative
from app.models import Booking, Event, Ticket, TicketTier, WaitlistEntry


class BookingAgent(BaseAgent):
    name = "BookingAgent"
    MAX_TICKETS_PER_BOOKING = 6

    def _utcnow(self):
        return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).replace(tzinfo=None)

    def _calculate_refund(self, event_start: datetime, total_paid: float) -> tuple[float, str]:
        hours_until = (event_start - self._utcnow()).total_seconds() / 3600.0
        if hours_until > 48:
            return round(total_paid, 2), "100% refund (>48 hours before event)"
        elif hours_until >= 24:
            return round(total_paid * 0.5, 2), "50% refund (24-48 hours before event)"
        else:
            return 0.0, "0% refund (<24 hours before event)"

    def handle_message(self, db: Session, msg: AgentMessage, bus: Any) -> AgentMessage:
        if msg.action == "BOOK_TICKETS":
            user_id = int(msg.content["user_id"])
            event_id = int(msg.content["event_id"])
            tier_id = int(msg.content["tier_id"])
            qty = int(msg.content["quantity"])
            waitlist_id = msg.content.get("waitlist_id")

            # Rule 1: Quantity bounds
            if qty < 1 or qty > self.MAX_TICKETS_PER_BOOKING:
                self.increment_belief(db, "bookings_refused")
                return self.reply(
                    msg,
                    Performative.REFUSE,
                    {"reason": f"Booking quantity must be between 1 and {self.MAX_TICKETS_PER_BOOKING} tickets."},
                )

            event = db.query(Event).filter_by(id=event_id).first()
            if not event or event.status != "published":
                return self.reply(msg, Performative.REFUSE, {"reason": "Event is not open for booking."})

            if event.end_time <= self._utcnow():
                return self.reply(msg, Performative.REFUSE, {"reason": "Cannot book tickets for an event that has ended."})

            # Check if claiming a valid waitlist hold
            claimed_hold = None
            if waitlist_id:
                claimed_hold = db.query(WaitlistEntry).filter_by(
                    id=waitlist_id, user_id=user_id, tier_id=tier_id, status="offered"
                ).first()
                if not claimed_hold or (claimed_hold.hold_expires_at and claimed_hold.hold_expires_at < self._utcnow()):
                    return self.reply(msg, Performative.REFUSE, {"reason": "Waitlist seat hold has expired or is invalid."})

            if claimed_hold:
                # Seats were already deducted/held when offered by WaitlistAgent
                tier = db.query(TicketTier).filter_by(id=tier_id).first()
                claimed_hold.status = "claimed"
            else:
                # Rule 2: Concurrency-safe atomic stock decrement (prevents overselling under simultaneous requests)
                stmt = (
                    update(TicketTier)
                    .where(
                        TicketTier.id == tier_id,
                        TicketTier.event_id == event_id,
                        TicketTier.remaining_qty >= qty,
                    )
                    .values(remaining_qty=TicketTier.remaining_qty - qty)
                )
                result = db.execute(stmt)
                if result.rowcount == 0:
                    self.increment_belief(db, "bookings_refused_sold_out")
                    return self.reply(
                        msg,
                        Performative.REFUSE,
                        {"reason": "Not enough tickets remaining in this category. You can join the waitlist!"},
                    )
                tier = db.query(TicketTier).filter_by(id=tier_id).first()

            total_paid = round(tier.price * qty, 2)
            payment_ref = f"SIM-{uuid.uuid4().hex[:10].upper()}"
            booking = Booking(
                user_id=user_id,
                event_id=event_id,
                tier_id=tier_id,
                quantity=qty,
                total_paid=total_paid,
                payment_ref=payment_ref,
                status="confirmed",
            )
            db.add(booking)
            db.flush()

            # Delegate QR ticket issuance to TicketAgent via MessageBus
            issue_req = AgentMessage(
                conversation_id=msg.conversation_id,
                sender=self.name,
                receiver="TicketAgent",
                performative=Performative.REQUEST,
                action="ISSUE_TICKETS",
                content={
                    "booking_id": booking.id,
                    "event_id": event_id,
                    "tier_id": tier_id,
                    "owner_id": user_id,
                    "quantity": qty,
                },
            )
            ticket_res = bus.send(db, issue_req)
            if ticket_res.performative != Performative.AGREE:
                return self.reply(msg, Performative.FAILURE, {"reason": "TicketAgent failed to issue QR tickets."})

            self.increment_belief(db, "bookings_confirmed")

            # Broadcast INFORM to NotificationAgent
            bus.send(
                db,
                AgentMessage(
                    conversation_id=msg.conversation_id,
                    sender=self.name,
                    receiver="ALL",
                    performative=Performative.INFORM,
                    action="BOOKING_CONFIRMED",
                    content={
                        "user_id": user_id,
                        "event_title": event.title,
                        "quantity": qty,
                        "tier_name": tier.name,
                        "payment_ref": payment_ref,
                    },
                ),
            )

            return self.reply(
                msg,
                Performative.AGREE,
                {
                    "booking_id": booking.id,
                    "payment_ref": payment_ref,
                    "total_paid": total_paid,
                    "ticket_codes": ticket_res.content["codes"],
                },
            )

        elif msg.action == "CANCEL_BOOKING":
            booking_id = int(msg.content["booking_id"])
            user_id = int(msg.content["user_id"])

            booking = db.query(Booking).filter_by(id=booking_id, user_id=user_id).first()
            if not booking:
                return self.reply(msg, Performative.REFUSE, {"reason": "Booking not found."})
            if booking.status == "cancelled":
                return self.reply(msg, Performative.REFUSE, {"reason": "Booking is already cancelled."})

            # Prevent cancellation if any ticket has already been scanned at the gate
            used_tickets = [t for t in booking.tickets if t.status == "used"]
            if used_tickets:
                return self.reply(
                    msg,
                    Performative.REFUSE,
                    {"reason": "Cannot cancel a booking after a ticket has already been checked in."},
                )

            refund, policy_note = self._calculate_refund(booking.event.start_time, booking.total_paid)
            booking.status = "cancelled"
            booking.refund_amount = refund
            for t in booking.tickets:
                t.status = "cancelled"

            # Atomically return seats to inventory
            db.execute(
                update(TicketTier)
                .where(TicketTier.id == booking.tier_id)
                .values(remaining_qty=TicketTier.remaining_qty + booking.quantity)
            )
            db.flush()

            self.increment_belief(db, "bookings_cancelled")

            # Broadcast SEATS_RELEASED so WaitlistAgent & NotificationAgent react!
            bus.send(
                db,
                AgentMessage(
                    conversation_id=msg.conversation_id,
                    sender=self.name,
                    receiver="ALL",
                    performative=Performative.INFORM,
                    action="SEATS_RELEASED",
                    content={
                        "user_id": user_id,
                        "event_id": booking.event_id,
                        "event_title": booking.event.title,
                        "tier_id": booking.tier_id,
                        "released_qty": booking.quantity,
                        "refund_amount": refund,
                        "policy_note": policy_note,
                    },
                ),
            )

            return self.reply(
                msg,
                Performative.AGREE,
                {"refund_amount": refund, "policy_note": policy_note},
            )

        elif msg.action == "EVENT_CANCELLED_REFUNDS":
            event_id = int(msg.content["event_id"])
            event = db.query(Event).filter_by(id=event_id).first()
            bookings = db.query(Booking).filter_by(event_id=event_id, status="confirmed").all()
            refunded_count = 0
            for b in bookings:
                b.status = "cancelled"
                b.refund_amount = b.total_paid  # 100% refund when organiser cancels event
                for t in b.tickets:
                    t.status = "cancelled"
                refunded_count += 1
                bus.send(
                    db,
                    AgentMessage(
                        conversation_id=msg.conversation_id,
                        sender=self.name,
                        receiver="ALL",
                        performative=Performative.INFORM,
                        action="EVENT_CANCELLED_USER_REFUND",
                        content={
                            "user_id": b.user_id,
                            "event_title": event.title,
                            "refund_amount": b.total_paid,
                        },
                    ),
                )
            db.flush()
            return self.reply(msg, Performative.AGREE, {"refunded_bookings": refunded_count})

        return self.reply(msg, Performative.FAILURE, {"reason": f"Unsupported action {msg.action}"}) 
    