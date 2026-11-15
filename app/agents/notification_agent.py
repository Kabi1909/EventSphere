from typing import Any
from sqlalchemy.orm import Session
from app.models import Notification
from app.agents.base import BaseAgent
from app.agents.protocol import AgentMessage, Performative

class NotificationAgent(BaseAgent):
    name = "NotificationAgent"

    def handle_message(self, db: Session, msg: AgentMessage, bus: Any) -> AgentMessage:
        if msg.performative != Performative.INFORM:
            return self.reply(msg, Performative.FAILURE, {"reason": "NotificationAgent only processes INFORM messages."})

        c = msg.content
        user_id = c.get("user_id")
        if not user_id:
            return self.reply(msg, Performative.INFORM, {"status": "ignored_no_user"})

        title = "EventSphere Update"
        body = ""

        if msg.action == "BOOKING_CONFIRMED":
            title = "Booking Confirmed"
            body = f"Your {c['quantity']}x {c['tier_name']} ticket(s) for '{c['event_title']}' are confirmed (Ref: {c['payment_ref']})."
        elif msg.action == "SEATS_RELEASED":
            title = "Booking Cancelled & Refunded"
            body = f"Your booking for '{c['event_title']}' was cancelled. Refund: LKR {c['refund_amount']:,.2f} ({c['policy_note']})."
        elif msg.action == "EVENT_CANCELLED_USER_REFUND":
            title = "Event Cancelled — Full Refund Issued"
            body = f"'{c['event_title']}' was cancelled by the organiser. A full refund of LKR {c['refund_amount']:,.2f} has been processed."
        elif msg.action == "WAITLIST_SEAT_OFFERED":
            title = "Waitlist Seat Reserved for You!"
            body = f"{c['quantity']}x {c['tier_name']} seat(s) held for '{c['event_title']}' until {c['expires_at']}. Claim now in My Bookings!"
        elif msg.action == "WAITLIST_HOLD_EXPIRED":
            title = "Waitlist Seat Hold Expired"
            body = f"Your 15-minute seat hold for '{c['event_title']}' expired and was offered to the next person in line."
        elif msg.action == "HIRE_DECISION":
            title = f"Venue Hire Update: {c['status'].replace('_', ' ').title()}"
            body = f"Your private hire request for '{c['venue_name']}' is {c['status']}. {c.get('reason', '')}"
        else:
            body = f"System update ({msg.action}): {c}"

        notif = Notification(user_id=int(user_id), title=title, message=body)
        db.add(notif)
        db.flush()
        self.increment_belief(db, "notifications_sent")
        return self.reply(msg, Performative.INFORM, {"notification_id": notif.id}) 
