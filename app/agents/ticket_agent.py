from typing import Any 
from datetime import datetime, timedelta, timezone
import secrets
from sqlalchemy.orm import Session
from app.models import Ticket, Event
from app.agents.base import BaseAgent
from app.agents.protocol import AgentMessage, Performative

class TicketAgent(BaseAgent):
    name = "TicketAgent"
    CHECKIN_EARLY_HOURS = 3

    def _utcnow(self):
         return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).replace(tzinfo=None)

    def handle_message(self, db: Session, msg: AgentMessage, bus: Any) -> AgentMessage:
        if msg.action == "ISSUE_TICKETS":
            booking_id = int(msg.content["booking_id"])
            event_id = int(msg.content["event_id"])
            tier_id = int(msg.content["tier_id"])
            owner_id = int(msg.content["owner_id"])
            qty = int(msg.content["quantity"])

            codes = []
            for _ in range(qty):
                # Cryptographically unguessable 128-bit hex token
                code = f"EVT-{secrets.token_hex(8).upper()}"
                ticket = Ticket(
                    booking_id=booking_id,
                    event_id=event_id,
                    tier_id=tier_id,
                    owner_id=owner_id,
                    code=code,
                    status="valid",
                )
                db.add(ticket)
                codes.append(code)
            db.flush()
            self.increment_belief(db, "tickets_issued", qty)
            return self.reply(msg, Performative.AGREE, {"codes": codes})

        elif msg.action == "SCAN_TICKET":
            code = msg.content.get("code", "").strip().upper()
            expected_event_id = int(msg.content["event_id"])

            # Track persistent scan attempts per code in agent beliefs
            scan_history = self.get_belief(db, "scan_attempts", {})
            scan_history[code] = scan_history.get(code, 0) + 1
            self.set_belief(db, "scan_attempts", scan_history)

            ticket = db.query(Ticket).filter_by(code=code).first()
            if not ticket:
                self.increment_belief(db, "rejected_invalid")
                return self.reply(
                    msg,
                    Performative.REFUSE,
                    {"status": "invalid", "reason": "Invalid QR code: ticket not found in system."},
                )

            ticket.scan_count += 1
            db.flush()

            # Rule 1: Wrong event check
            if ticket.event_id != expected_event_id:
                self.increment_belief(db, "rejected_wrong_event")
                return self.reply(
                    msg,
                    Performative.REFUSE,
                    {
                        "status": "wrong_event",
                        "reason": f"Wrong Event: this ticket is for '{ticket.event.title}', not the selected event.",
                    },
                )

            # Rule 2: Cancelled ticket check
            if ticket.status == "cancelled" or ticket.event.status == "cancelled":
                self.increment_belief(db, "rejected_cancelled")
                return self.reply(
                    msg,
                    Performative.REFUSE,
                    {"status": "cancelled", "reason": "Ticket Rejected: this booking/event has been cancelled."},
                )

            # Rule 3: Duplicate scan & suspicious screenshot sharing detection
            if ticket.status == "used":
                self.increment_belief(db, "rejected_already_used")
                suspicious = ticket.scan_count >= 3
                reason = (
                    f"ALREADY USED at {ticket.checked_in_at.strftime('%H:%M:%S')}! "
                    + ("ALERT: Repeated scan attempts detected (possible shared screenshot)." if suspicious else "Duplicate entry denied.")
                )
                return self.reply(
                    msg,
                    Performative.REFUSE,
                    {"status": "already_used", "suspicious": suspicious, "reason": reason},
                )

            # Rule 4: Check-in time window (opens 3 hours before start until event end)
            now = self._utcnow()
            window_open = ticket.event.start_time - timedelta(hours=self.CHECKIN_EARLY_HOURS)
            if now < window_open:
                return self.reply(
                    msg,
                    Performative.REFUSE,
                    {
                        "status": "outside_window",
                        "reason": f"Too Early: check-in opens at {window_open.strftime('%Y-%m-%d %H:%M')} (3h before start).",
                    },
                )
            if now > ticket.event.end_time:
                return self.reply(
                    msg,
                    Performative.REFUSE,
                    {"status": "outside_window", "reason": "Event has already ended; check-in closed."},
                )

            # Admit ticket exactly once
            ticket.status = "used"
            ticket.checked_in_at = now
            db.flush()
            self.increment_belief(db, "tickets_admitted")

            return self.reply(
                msg,
                Performative.AGREE,
                {
                    "status": "admitted",
                    "attendee": ticket.owner.name,
                    "tier": ticket.tier.name,
                    "code": ticket.code,
                    "reason": f"ADMITTED: {ticket.owner.name} ({ticket.tier.name})",
                },
            )

        return self.reply(msg, Performative.FAILURE, {"reason": f"Unsupported action {msg.action}"})
