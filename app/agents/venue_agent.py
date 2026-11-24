from typing import Any
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models import Event, Venue, VenueHireRequest
from app.agents.base import BaseAgent
from app.agents.protocol import AgentMessage, Performative


class VenueAgent(BaseAgent):
    name = "VenueAgent"
    TURNAROUND_MINUTES = 60
    AUTO_CONFIRM_MAX_HOURS = 4.0
    AUTO_CONFIRM_MAX_GUESTS = 150

    def _has_clash(self, db: Session, venue_id: int, start_time: datetime, end_time: datetime) -> str | None:
        buffer = timedelta(minutes=self.TURNAROUND_MINUTES)
        req_start = start_time - buffer
        req_end = end_time + buffer

        events = db.query(Event).filter(
            Event.venue_id == venue_id,
            Event.status != "cancelled",
        ).all()
        for ev in events:
            if ev.start_time < req_end and ev.end_time > req_start:
                return (
                    f"Venue clash detected (includes {self.TURNAROUND_MINUTES}-min turnaround buffer): "
                    f"Event '{ev.title}' ({ev.start_time.strftime('%Y-%m-%d %H:%M')} - "
                    f"{ev.end_time.strftime('%H:%M')} + {self.TURNAROUND_MINUTES}m buffer)"
                )

        hires = db.query(VenueHireRequest).filter(
            VenueHireRequest.venue_id == venue_id,
            VenueHireRequest.status == "confirmed",
        ).all()
        for hr in hires:
            if hr.start_time < req_end and hr.end_time > req_start:
                return (
                    f"Venue clash detected with confirmed private hire ({hr.start_time.strftime('%Y-%m-%d %H:%M')} - "
                    f"{hr.end_time.strftime('%H:%M')} + {self.TURNAROUND_MINUTES}m buffer)"
                )
        return None

    def _find_alternatives(
        self,
        db: Session,
        requested_venue: Venue,
        start_time: datetime,
        end_time: datetime,
        guest_count: int,
    ) -> list[dict[str, Any]]:
        alternatives: list[dict[str, Any]] = []
        duration = end_time - start_time
        duration_hours = round(duration.total_seconds() / 3600.0, 2)

        # 1. Alternative venues available at the exact requested time
        other_venues = (
            db.query(Venue)
            .filter(
                Venue.id != requested_venue.id,
                Venue.is_private_hire_open == True,
                Venue.capacity >= guest_count,
            )
            .all()
        )
        for alt_v in other_venues:
            if self._has_clash(db, alt_v.id, start_time, end_time) is None:
                alternatives.append({
                    "type": "alternative_venue",
                    "venue_id": alt_v.id,
                    "venue_name": alt_v.name,
                    "start_time": start_time.strftime("%Y-%m-%d %H:%M"),
                    "end_time": end_time.strftime("%Y-%m-%d %H:%M"),
                    "quoted_price": round(duration_hours * float(alt_v.hourly_rate), 2),
                })
                if len(alternatives) >= 2:
                    break

        # 2. Alternative time slots at the requested venue
        time_alts_added = 0
        for offset in (timedelta(hours=2), timedelta(hours=4), timedelta(days=1), timedelta(days=2)):
            alt_start = start_time + offset
            alt_end = alt_start + duration
            if self._has_clash(db, requested_venue.id, alt_start, alt_end) is None:
                alternatives.append({
                    "type": "alternative_time",
                    "venue_id": requested_venue.id,
                    "venue_name": requested_venue.name,
                    "start_time": alt_start.strftime("%Y-%m-%d %H:%M"),
                    "end_time": alt_end.strftime("%Y-%m-%d %H:%M"),
                    "quoted_price": round(duration_hours * float(requested_venue.hourly_rate), 2),
                })
                time_alts_added += 1
                if time_alts_added >= 2:
                    break

        return alternatives

    def handle_message(self, db: Session, msg: AgentMessage, bus: Any) -> AgentMessage:
        if msg.action == "VALIDATE_EVENT_VENUE":
            venue_id = int(msg.content["venue_id"])
            start_time = datetime.fromisoformat(msg.content["start_time"])
            end_time = datetime.fromisoformat(msg.content["end_time"])

            venue = db.query(Venue).filter_by(id=venue_id).first()
            if not venue:
                return AgentMessage(
                    conversation_id=msg.conversation_id,
                    sender=self.name,
                    receiver=msg.sender,
                    performative=Performative.REFUSE,
                    action="VALIDATE_EVENT_VENUE",
                    content={"reason": "Selected venue does not exist."},
                )

            if end_time <= start_time:
                return AgentMessage(
                    conversation_id=msg.conversation_id,
                    sender=self.name,
                    receiver=msg.sender,
                    performative=Performative.REFUSE,
                    action="VALIDATE_EVENT_VENUE",
                    content={"reason": "End time must be after start time."},
                )

            clash = self._has_clash(db, venue_id, start_time, end_time)
            if clash:
                self.set_belief(db, "clashes_prevented", self.get_belief(db, "clashes_prevented", 0) + 1)
                return AgentMessage(
                    conversation_id=msg.conversation_id,
                    sender=self.name,
                    receiver=msg.sender,
                    performative=Performative.REFUSE,
                    action="VALIDATE_EVENT_VENUE",
                    content={"reason": clash},
                )

            return AgentMessage(
                conversation_id=msg.conversation_id,
                sender=self.name,
                receiver=msg.sender,
                performative=Performative.AGREE,
                action="VALIDATE_EVENT_VENUE",
                content={"venue_name": venue.name, "reason": "Venue available with 60-min turnaround buffer."},
            )

        elif msg.action == "REQUEST_PRIVATE_HIRE":
            user_id = int(msg.content["user_id"])
            venue_id = int(msg.content["venue_id"])
            start_time = datetime.fromisoformat(msg.content["start_time"])
            end_time = datetime.fromisoformat(msg.content["end_time"])
            purpose = msg.content.get("purpose", "").strip()
            guest_count = int(msg.content.get("guest_count", 1))

            venue = db.query(Venue).filter_by(id=venue_id).first()
            if not venue or not venue.is_private_hire_open:
                return AgentMessage(
                    conversation_id=msg.conversation_id,
                    sender=self.name,
                    receiver=msg.sender,
                    performative=Performative.REFUSE,
                    action="REQUEST_PRIVATE_HIRE",
                    content={"reason": "Venue is not available for private hire."},
                )

            if end_time <= start_time:
                return AgentMessage(
                    conversation_id=msg.conversation_id,
                    sender=self.name,
                    receiver=msg.sender,
                    performative=Performative.REFUSE,
                    action="REQUEST_PRIVATE_HIRE",
                    content={"reason": "End time must be after start time."},
                )

            if guest_count > venue.capacity:
                return AgentMessage(
                    conversation_id=msg.conversation_id,
                    sender=self.name,
                    receiver=msg.sender,
                    performative=Performative.REFUSE,
                    action="REQUEST_PRIVATE_HIRE",
                    content={"reason": f"Guest count ({guest_count}) exceeds venue capacity ({venue.capacity})."},
                )

            clash = self._has_clash(db, venue_id, start_time, end_time)
            if clash:
                self.set_belief(db, "clashes_prevented", self.get_belief(db, "clashes_prevented", 0) + 1)
                alternatives = self._find_alternatives(db, venue, start_time, end_time, guest_count)
                if alternatives:
                    return AgentMessage(
                        conversation_id=msg.conversation_id,
                        sender=self.name,
                        receiver=msg.sender,
                        performative=Performative.PROPOSE,
                        action="REQUEST_PRIVATE_HIRE",
                        content={
                            "status": "counter_proposed",
                            "quoted_price": 0.0,
                            "reason": clash,
                            "alternatives": alternatives,
                        },
                    )
                return AgentMessage(
                    conversation_id=msg.conversation_id,
                    sender=self.name,
                    receiver=msg.sender,
                    performative=Performative.REFUSE,
                    action="REQUEST_PRIVATE_HIRE",
                    content={"reason": clash, "alternatives": []},
                )

            # Standard flat hourly pricing (1.0x — peak/weekend surge pricing removed)
            duration_hours = round((end_time - start_time).total_seconds() / 3600.0, 2)
            quoted_price = round(duration_hours * float(venue.hourly_rate), 2)

            if duration_hours <= self.AUTO_CONFIRM_MAX_HOURS and guest_count <= self.AUTO_CONFIRM_MAX_GUESTS:
                status = "confirmed"
                reason = f"Auto-confirmed routine private hire ({duration_hours} hrs @ Standard rate)."
                self.set_belief(db, "hires_auto_confirmed", self.get_belief(db, "hires_auto_confirmed", 0) + 1)
            else:
                status = "escalated"
                reason = f"Escalated to Admin for approval ({duration_hours} hrs, {guest_count} guests)."
                self.set_belief(db, "hires_escalated", self.get_belief(db, "hires_escalated", 0) + 1)

            hire = VenueHireRequest(
                user_id=user_id,
                venue_id=venue_id,
                start_time=start_time,
                end_time=end_time,
                purpose=purpose,
                guest_count=guest_count,
                quoted_price=quoted_price,
                status=status,
                agent_notes=reason,
            )
            db.add(hire)
            db.flush()

            bus.send(
                db,
                AgentMessage(
                    conversation_id=msg.conversation_id,
                    sender=self.name,
                    receiver="ALL",
                    performative=Performative.INFORM,
                    action="HIRE_DECISION",
                    content={
                        "user_id": user_id,
                        "venue_name": venue.name,
                        "status": status,
                        "reason": reason,
                    },
                ),
            )

            return AgentMessage(
                conversation_id=msg.conversation_id,
                sender=self.name,
                receiver=msg.sender,
                performative=Performative.AGREE,
                action="REQUEST_PRIVATE_HIRE",
                content={
                    "status": status,
                    "quoted_price": quoted_price,
                    "reason": reason,
                },
            )

        return AgentMessage(
            conversation_id=msg.conversation_id,
            sender=self.name,
            receiver=msg.sender,
            performative=Performative.FAILURE,
            action=msg.action,
            content={"reason": f"Unknown action: {msg.action}"},
        ) 
    