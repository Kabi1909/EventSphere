from datetime import datetime, timedelta
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from app.models import Venue, Event, VenueHireRequest
from app.agents.base import BaseAgent
from app.agents.protocol import AgentMessage, Performative

class VenueAgent(BaseAgent):
    name = "VenueAgent"
    BUFFER_MINUTES = 60
    MAX_AUTO_GUESTS = 150
    MAX_AUTO_HOURS = 4.0

    def _check_conflicts(
        self, db: Session, venue_id: int, start: datetime, end: datetime, ignore_event_id: int = None
    ) -> List[str]:
        buf = timedelta(minutes=self.BUFFER_MINUTES)
        req_start = start - buf
        req_end = end + buf
        conflicts = []

        events = db.query(Event).filter(
            Event.venue_id == venue_id,
            Event.status != "cancelled",
        ).all()
        for ev in events:
            if ignore_event_id and ev.id == ignore_event_id:
                continue
            if req_start < ev.end_time and req_end > ev.start_time:
                conflicts.append(
                    f"Event '{ev.title}' ({ev.start_time.strftime('%Y-%m-%d %H:%M')} - {ev.end_time.strftime('%H:%M')} + 60m buffer)"
                )

        hires = db.query(VenueHireRequest).filter(
            VenueHireRequest.venue_id == venue_id,
            VenueHireRequest.status.in_(["confirmed", "escalated"]),
        ).all()
        for h in hires:
            if req_start < h.end_time and req_end > h.start_time:
                conflicts.append(
                    f"Private Hire #{h.id} ({h.start_time.strftime('%Y-%m-%d %H:%M')} - {h.end_time.strftime('%H:%M')} + 60m buffer)"
                )
        return conflicts

    def _calculate_price(self, venue: Venue, start: datetime, end: datetime) -> tuple[float, float]:
        hours = max(1.0, (end - start).total_seconds() / 3600.0)
        # Weekend (Fri=4, Sat=5, Sun=6) or evening (>=17:00) peak pricing: 1.25x
        is_peak = start.weekday() in (4, 5, 6) or start.hour >= 17
        multiplier = 1.25 if is_peak else 1.0
        return round(venue.hourly_rate * hours * multiplier, 2), multiplier

    def _generate_alternatives(
        self, db: Session, venue: Venue, start: datetime, end: datetime, guest_count: int
    ) -> List[Dict[str, Any]]:
        alternatives = []
        duration = end - start

        # Alternative 1 & 2: Shift time (+3 hours, +1 day) on same venue
        for delta in [timedelta(hours=3), timedelta(days=1)]:
            alt_start = start + delta
            alt_end = alt_start + duration
            if not self._check_conflicts(db, venue.id, alt_start, alt_end):
                price, mult = self._calculate_price(venue, alt_start, alt_end)
                alternatives.append({
                    "type": "alternative_time",
                    "venue_id": venue.id,
                    "venue_name": venue.name,
                    "start_time": alt_start.strftime("%Y-%m-%d %H:%M"),
                    "end_time": alt_end.strftime("%Y-%m-%d %H:%M"),
                    "quoted_price": price,
                    "note": f"Same venue at a conflict-free time ({'Peak 1.25x' if mult > 1 else 'Standard rate'})"
                })

        # Alternative 3: Different open venue with sufficient capacity at requested time
        other_venues = db.query(Venue).filter(
            Venue.id != venue.id,
            Venue.is_private_hire_open == True,
            Venue.capacity >= guest_count
        ).all()
        for ov in other_venues:
            if not self._check_conflicts(db, ov.id, start, end):
                price, mult = self._calculate_price(ov, start, end)
                alternatives.append({
                    "type": "alternative_venue",
                    "venue_id": ov.id,
                    "venue_name": ov.name,
                    "start_time": start.strftime("%Y-%m-%d %H:%M"),
                    "end_time": end.strftime("%Y-%m-%d %H:%M"),
                    "quoted_price": price,
                    "note": f"Available at your exact requested time (Capacity: {ov.capacity})"
                })
                break

        return alternatives

    def handle_message(self, db: Session, msg: AgentMessage, bus: Any) -> AgentMessage:
        if msg.action == "CHECK_EVENT_SLOT":
            venue_id = int(msg.content["venue_id"])
            start = datetime.fromisoformat(msg.content["start_time"])
            end = datetime.fromisoformat(msg.content["end_time"])
            ignore_id = msg.content.get("ignore_event_id")

            venue = db.query(Venue).filter_by(id=venue_id).first()
            if not venue:
                return self.reply(msg, Performative.REFUSE, {"reason": "Venue does not exist."})
            if end <= start:
                return self.reply(msg, Performative.REFUSE, {"reason": "End time must be after start time."})

            conflicts = self._check_conflicts(db, venue_id, start, end, ignore_id)
            if conflicts:
                self.increment_belief(db, "slots_refused")
                return self.reply(
                    msg,
                    Performative.REFUSE,
                    {"reason": f"Venue clash detected (includes 60-min turnaround buffer): {'; '.join(conflicts)}"},
                )

            self.increment_belief(db, "slots_approved")
            return self.reply(msg, Performative.AGREE, {"reason": "Venue slot is free with 60-minute buffer."})

        elif msg.action == "REQUEST_PRIVATE_HIRE":
            venue_id = int(msg.content["venue_id"])
            start = datetime.fromisoformat(msg.content["start_time"])
            end = datetime.fromisoformat(msg.content["end_time"])
            guest_count = int(msg.content["guest_count"])

            venue = db.query(Venue).filter_by(id=venue_id).first()
            if not venue or not venue.is_private_hire_open:
                return self.reply(msg, Performative.REFUSE, {"reason": "Selected venue is not open for private hire."})
            if end <= start:
                return self.reply(msg, Performative.REFUSE, {"reason": "End time must be after start time."})
            if guest_count > venue.capacity:
                alts = self._generate_alternatives(db, venue, start, end, guest_count)
                return self.reply(
                    msg,
                    Performative.PROPOSE,
                    {
                        "reason": f"Guest count ({guest_count}) exceeds {venue.name} capacity ({venue.capacity}).",
                        "alternatives": alts,
                    },
                )

            conflicts = self._check_conflicts(db, venue_id, start, end)
            if conflicts:
                self.increment_belief(db, "hires_counter_proposed")
                alts = self._generate_alternatives(db, venue, start, end, guest_count)
                return self.reply(
                    msg,
                    Performative.PROPOSE,
                    {
                        "reason": f"Requested slot conflicts with: {'; '.join(conflicts)}.",
                        "alternatives": alts,
                    },
                )

            price, multiplier = self._calculate_price(venue, start, end)
            hours = (end - start).total_seconds() / 3600.0
            rate_label = "Peak/Weekend rate (1.25x)" if multiplier > 1.0 else "Standard rate (1.0x)"

            # Decision rule: Routine hires (<= 150 guests and <= 4 hours) auto-confirm; larger/longer escalate to Admin
            if guest_count <= self.MAX_AUTO_GUESTS and hours <= self.MAX_AUTO_HOURS:
                self.increment_belief(db, "hires_auto_confirmed")
                return self.reply(
                    msg,
                    Performative.AGREE,
                    {
                        "status": "confirmed",
                        "quoted_price": price,
                        "reason": f"Auto-confirmed routine private hire ({hours:.1f} hrs @ {rate_label}).",
                    },
                )
            else:
                self.increment_belief(db, "hires_escalated")
                return self.reply(
                    msg,
                    Performative.PROPOSE,
                    {
                        "status": "escalated",
                        "quoted_price": price,
                        "reason": f"Large/extended hire ({guest_count} guests, {hours:.1f} hrs) escalated for Admin approval. Quoted at {rate_label}.",
                        "alternatives": [],
                    },
                )

        return self.reply(msg, Performative.FAILURE, {"reason": f"Unsupported action {msg.action}"})
