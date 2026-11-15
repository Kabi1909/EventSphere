from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from app.models import User, Venue, Event, TicketTier
from app.security import hash_password

def seed_database(db: Session):
    if db.query(User).count() > 0:
        return

    admin = User(name="Dr. Perera (Admin)", email="admin@eventsphere.edu", password_hash=hash_password("Admin@123"), role="admin")
    staff = User(name="Gate Officer Kasun", email="staff@eventsphere.edu", password_hash=hash_password("Staff@123"), role="staff")
    student = User(name="Nimal Silva", email="student@eventsphere.edu", password_hash=hash_password("Student@123"), role="attendee")
    db.add_all([admin, staff, student])
    db.flush()

    v1 = Venue(
        name="Main University Auditorium",
        location="Block A, Central Campus",
        capacity=500,
        description="Tiered seating auditorium with concert-grade acoustics and lighting.",
        image_url="https://images.unsplash.com/photo-1517457373958-b7bdd4587205?w=800",
        hourly_rate=15000.0,
        is_private_hire_open=True,
    )
    v2 = Venue(
        name="Innovation Seminar Hall",
        location="ICT Faculty, 2nd Floor",
        capacity=120,
        description="Modern seminar hall equipped with dual projectors and hybrid conferencing.",
        image_url="https://images.unsplash.com/photo-1505373877841-8d25f7d46678?w=800",
        hourly_rate=6000.0,
        is_private_hire_open=True,
    )
    v3 = Venue(
        name="Lakeside Open Amphitheatre",
        location="South Gardens",
        capacity=300,
        description="Scenic open-air venue ideal for cultural nights, farewells, and concerts.",
        image_url="https://images.unsplash.com/photo-1492684223066-81342ee5ff30?w=800",
        hourly_rate=10000.0,
        is_private_hire_open=True,
    )
    db.add_all([v1, v2, v3])
    db.flush()

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    e1 = Event(
        title="Annual Tech & AI Symposium 2026",
        category="Conference",
        venue_id=v1.id,
        start_time=now + timedelta(hours=1),
        end_time=now + timedelta(hours=5),
        description="Keynotes on Multi-Agent Systems, Autonomous Robotics, and Cloud Engineering.",
        status="published",
    )
    e2 = Event(
        title="Symphony Under the Stars",
        category="Concert",
        venue_id=v3.id,
        start_time=now + timedelta(days=4),
        end_time=now + timedelta(days=4, hours=3),
        description="University orchestra and student bands live at the Lakeside Amphitheatre.",
        status="published",
    )
    e3 = Event(
        title="Hands-on FastAPI & Agent Architectures",
        category="Workshop",
        venue_id=v2.id,
        start_time=now + timedelta(days=2),
        end_time=now + timedelta(days=2, hours=3),
        description="Intensive practical lab building distributed agent communication buses.",
        status="published",
    )
    db.add_all([e1, e2, e3])
    db.flush()

    tiers = [
        TicketTier(event_id=e1.id, name="Student Pass", price=1000.0, total_qty=200, remaining_qty=200),
        TicketTier(event_id=e1.id, name="VIP Front Row", price=3500.0, total_qty=50, remaining_qty=50),
        TicketTier(event_id=e2.id, name="General Lawn", price=1500.0, total_qty=250, remaining_qty=250),
        TicketTier(event_id=e3.id, name="Lab Seat", price=500.0, total_qty=2, remaining_qty=2),
    ]
    db.add_all(tiers)
    db.commit() 
    