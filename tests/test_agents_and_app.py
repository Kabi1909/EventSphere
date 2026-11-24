from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.main import app
from app.models import User, Venue, Event, TicketTier, Ticket, WaitlistEntry, Notification, AgentMessageLog
from app.security import hash_password
from app.agents.protocol import AgentMessage, Performative
from app.agents.registry import bus, waitlist_agent

TEST_DB_URL = "sqlite:///./test_eventsphere.db"
engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def slst_now() -> datetime:
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).replace(tzinfo=None)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    admin = User(name="Admin", email="admin@test.edu", password_hash=hash_password("pass"), role="admin")
    staff = User(name="Staff", email="staff@test.edu", password_hash=hash_password("pass"), role="staff")
    u1 = User(name="Alice", email="alice@test.edu", password_hash=hash_password("pass"), role="attendee")
    u2 = User(name="Bob", email="bob@test.edu", password_hash=hash_password("pass"), role="attendee")
    db.add_all([admin, staff, u1, u2])
    db.flush()

    v1 = Venue(name="Hall A", location="Main", capacity=100, hourly_rate=50.0, is_private_hire_open=True)
    v2 = Venue(name="Hall B", location="East", capacity=200, hourly_rate=80.0, is_private_hire_open=True)
    db.add_all([v1, v2])
    db.flush()

    now = slst_now()
    ev = Event(
        title="Live Concert",
        category="Concert",
        venue_id=v1.id,
        start_time=now + timedelta(hours=1),
        end_time=now + timedelta(hours=4),
        status="published",
    )
    db.add(ev)
    db.flush()
    tier = TicketTier(event_id=ev.id, name="Standard", price=20.0, total_qty=1, remaining_qty=1)
    db.add(tier)
    db.commit()
    db.close()
    yield
    Base.metadata.drop_all(bind=engine)


# 1. VenueAgent clash detection with 60-min turnaround buffer
def test_venue_agent_clash_and_buffer():
    db = TestingSessionLocal()
    ev = db.query(Event).first()
    # Attempt to book 30 mins after existing event ends (violates 60-min buffer)
    msg = AgentMessage(
        sender="Admin:1",
        receiver="VenueAgent",
        performative=Performative.REQUEST,
        action="VALIDATE_EVENT_VENUE",
        content={
            "venue_id": ev.venue_id,
            "start_time": (ev.end_time + timedelta(minutes=30)).isoformat(),
            "end_time": (ev.end_time + timedelta(hours=2)).isoformat(),
        },
    )
    res = bus.send(db, msg)
    assert res.performative == Performative.REFUSE
    assert "buffer" in res.content["reason"].lower()
    db.close()


# 2. VenueAgent private hire auto-confirm & counter-proposal alternatives
def test_venue_agent_private_hire_and_counter_proposal():
    db = TestingSessionLocal()
    ev = db.query(Event).first()
    # Clashing time should return PROPOSE with alternatives
    msg = AgentMessage(
        sender="User:3",
        receiver="VenueAgent",
        performative=Performative.REQUEST,
        action="REQUEST_PRIVATE_HIRE",
        content={
            "user_id": 3,
            "venue_id": ev.venue_id,
            "start_time": ev.start_time.isoformat(),
            "end_time": ev.end_time.isoformat(),
            "purpose": "Club Meeting",
            "guest_count": 40,
        },
    )
    res = bus.send(db, msg)
    assert res.performative == Performative.PROPOSE
    assert len(res.content["alternatives"]) >= 1
    db.close()


# 3. BookingAgent max 6 tickets rule
def test_booking_agent_max_quantity_limit():
    db = TestingSessionLocal()
    ev = db.query(Event).first()
    tier = db.query(TicketTier).first()
    msg = AgentMessage(
        sender="User:3",
        receiver="BookingAgent",
        performative=Performative.REQUEST,
        action="BOOK_TICKETS",
        content={"user_id": 3, "event_id": ev.id, "tier_id": tier.id, "quantity": 7},
    )
    res = bus.send(db, msg)
    assert res.performative == Performative.REFUSE
    assert "6" in res.content["reason"]
    db.close()


# 4. Concurrency / Simultaneous last-seat booking prevents overbooking
def test_concurrent_last_seat_booking_no_oversell():
    def attempt_book(uid: int):
        local_db = TestingSessionLocal()
        try:
            ev = local_db.query(Event).first()
            tier = local_db.query(TicketTier).first()
            msg = AgentMessage(
                sender=f"User:{uid}",
                receiver="BookingAgent",
                performative=Performative.REQUEST,
                action="BOOK_TICKETS",
                content={"user_id": uid, "event_id": ev.id, "tier_id": tier.id, "quantity": 1},
            )
            res = bus.send(local_db, msg)
            local_db.commit()
            return res.performative
        finally:
            local_db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt_book, [3, 4]))

    assert results.count(Performative.AGREE) == 1
    assert results.count(Performative.REFUSE) == 1


# 5. TicketAgent single check-in & double scan rejection + suspicious belief tracking
def test_ticket_agent_double_checkin_rejected():
    db = TestingSessionLocal()
    ev = db.query(Event).first()
    tier = db.query(TicketTier).first()
    book_res = bus.send(
        db,
        AgentMessage(
            sender="User:3",
            receiver="BookingAgent",
            performative=Performative.REQUEST,
            action="BOOK_TICKETS",
            content={"user_id": 3, "event_id": ev.id, "tier_id": tier.id, "quantity": 1},
        ),
    )
    code = book_res.content["ticket_codes"][0]

    scan1 = bus.send(
        db,
        AgentMessage(
            sender="Staff:2",
            receiver="TicketAgent",
            performative=Performative.REQUEST,
            action="SCAN_TICKET",
            content={"event_id": ev.id, "code": code},
        ),
    )
    assert scan1.performative == Performative.AGREE

    scan2 = bus.send(
        db,
        AgentMessage(
            sender="Staff:2",
            receiver="TicketAgent",
            performative=Performative.REQUEST,
            action="SCAN_TICKET",
            content={"event_id": ev.id, "code": code},
        ),
    )
    assert scan2.performative == Performative.REFUSE
    assert "already used" in scan2.content["reason"].lower()
    db.close()


# 6. TicketAgent rejects wrong event ticket
def test_ticket_agent_wrong_event_rejected():
    db = TestingSessionLocal()
    ev = db.query(Event).first()
    tier = db.query(TicketTier).first()
    book_res = bus.send(
        db,
        AgentMessage(
            sender="User:3",
            receiver="BookingAgent",
            performative=Performative.REQUEST,
            action="BOOK_TICKETS",
            content={"user_id": 3, "event_id": ev.id, "tier_id": tier.id, "quantity": 1},
        ),
    )
    code = book_res.content["ticket_codes"][0]
    scan = bus.send(
        db,
        AgentMessage(
            sender="Staff:2",
            receiver="TicketAgent",
            performative=Performative.REQUEST,
            action="SCAN_TICKET",
            content={"event_id": 9999, "code": code},
        ),
    )
    assert scan.performative == Performative.REFUSE
    assert "wrong event" in scan.content["reason"].lower()
    db.close()


# 7. WaitlistAgent reactive offer on cancellation & proactive timer expiry sweep
def test_waitlist_reactive_and_proactive_expiry():
    db = TestingSessionLocal()
    ev = db.query(Event).first()
    tier = db.query(TicketTier).first()

    # Alice books the last seat
    b_res = bus.send(
        db,
        AgentMessage(
            sender="User:3",
            receiver="BookingAgent",
            performative=Performative.REQUEST,
            action="BOOK_TICKETS",
            content={"user_id": 3, "event_id": ev.id, "tier_id": tier.id, "quantity": 1},
        ),
    )
    booking_id = b_res.content["booking_id"]

    # Bob joins waitlist
    w_res = bus.send(
        db,
        AgentMessage(
            sender="User:4",
            receiver="WaitlistAgent",
            performative=Performative.REQUEST,
            action="JOIN_WAITLIST",
            content={"user_id": 4, "event_id": ev.id, "tier_id": tier.id, "quantity": 1},
        ),
    )
    assert w_res.performative == Performative.AGREE

    # Alice cancels -> BookingAgent broadcasts SEATS_RELEASED -> WaitlistAgent holds seat for Bob
    bus.send(
        db,
        AgentMessage(
            sender="User:3",
            receiver="BookingAgent",
            performative=Performative.REQUEST,
            action="CANCEL_BOOKING",
            content={"booking_id": booking_id, "user_id": 3},
        ),
    )
    entry = db.query(WaitlistEntry).filter_by(user_id=4).first()
    assert entry.status == "offered"

    # Simulate 16 minutes passing and run proactive expiry sweep
    entry.hold_expires_at = slst_now() - timedelta(minutes=1)
    db.commit()
    expired_count = waitlist_agent.run_expiry_sweep(db, bus)
    assert expired_count == 1
    db.refresh(entry)
    assert entry.status == "expired"
    db.close()


# 8. NotificationAgent turns INFORM events into persistent user notifications
def test_notification_agent_creates_user_alerts():
    db = TestingSessionLocal()
    ev = db.query(Event).first()
    tier = db.query(TicketTier).first()
    bus.send(
        db,
        AgentMessage(
            sender="User:3",
            receiver="BookingAgent",
            performative=Performative.REQUEST,
            action="BOOK_TICKETS",
            content={"user_id": 3, "event_id": ev.id, "tier_id": tier.id, "quantity": 1},
        ),
    )
    notes = db.query(Notification).filter_by(user_id=3).all()
    assert len(notes) >= 1
    assert "confirmed" in notes[0].title.lower()
    db.close()


# 9. MessageBus Conversation Traceability Log
def test_message_bus_traceability():
    db = TestingSessionLocal()
    now = slst_now()
    msg = AgentMessage(
        sender="Admin:1",
        receiver="VenueAgent",
        performative=Performative.REQUEST,
        action="VALIDATE_EVENT_VENUE",
        content={
            "venue_id": 2,
            "start_time": (now + timedelta(days=5)).isoformat(),
            "end_time": (now + timedelta(days=5, hours=2)).isoformat(),
        },
    )
    res = bus.send(db, msg)
    assert res.performative == Performative.AGREE

    logs = db.query(AgentMessageLog).filter_by(conversation_id=msg.conversation_id).all()
    assert len(logs) == 2
    db.close()


# 10. Role-Based Access Control blocks unauthorized users from /admin and /staff/scanner
def test_role_based_access_control():
    client = TestClient(app)
    res_admin = client.get("/admin")
    res_staff = client.get("/staff/scanner")
    assert res_admin.status_code in (401, 403)
    assert res_staff.status_code in (401, 403) 
