from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, UniqueConstraint
)
from sqlalchemy.orm import relationship
from app.database import Base

def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False)
    email = Column(String(150), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), default="attendee", nullable=False)  # admin, staff, attendee
    created_at = Column(DateTime, default=utcnow)

class Venue(Base):
    __tablename__ = "venues"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    location = Column(String(200), nullable=False)
    capacity = Column(Integer, nullable=False)
    description = Column(Text, default="")
    image_url = Column(String(500), default="https://images.unsplash.com/photo-1517457373958-b7bdd4587205?w=800")
    hourly_rate = Column(Float, nullable=False)
    is_private_hire_open = Column(Boolean, default=True)

class Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    category = Column(String(80), nullable=False)  # Concert, Workshop, Seminar, Conference
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    description = Column(Text, default="") 
    image_url = Column(Text, nullable=True) 
    status = Column(String(20), default="draft")  # draft, published, cancelled
    created_at = Column(DateTime, default=utcnow)

    venue = relationship("Venue")
    ticket_tiers = relationship("TicketTier", back_populates="event", cascade="all, delete-orphan")

class TicketTier(Base):
    __tablename__ = "ticket_tiers"
    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    name = Column(String(100), nullable=False)  # General, VIP, Student
    price = Column(Float, nullable=False)
    total_qty = Column(Integer, nullable=False)
    remaining_qty = Column(Integer, nullable=False)

    event = relationship("Event", back_populates="ticket_tiers")

class Booking(Base):
    __tablename__ = "bookings"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    tier_id = Column(Integer, ForeignKey("ticket_tiers.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    total_paid = Column(Float, nullable=False)
    payment_ref = Column(String(64), nullable=False)
    status = Column(String(20), default="confirmed")  # confirmed, cancelled
    refund_amount = Column(Float, default=0.0)
    created_at = Column(DateTime, default=utcnow)

    user = relationship("User")
    event = relationship("Event")
    tier = relationship("TicketTier")
    tickets = relationship("Ticket", back_populates="booking", cascade="all, delete-orphan")

class Ticket(Base):
    __tablename__ = "tickets"
    id = Column(Integer, primary_key=True, index=True)
    booking_id = Column(Integer, ForeignKey("bookings.id"), nullable=False)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    tier_id = Column(Integer, ForeignKey("ticket_tiers.id"), nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    code = Column(String(64), unique=True, index=True, nullable=False)
    status = Column(String(20), default="valid")  # valid, used, cancelled
    scan_count = Column(Integer, default=0)
    checked_in_at = Column(DateTime, nullable=True)

    booking = relationship("Booking", back_populates="tickets")
    event = relationship("Event")
    tier = relationship("TicketTier")
    owner = relationship("User")

class VenueHireRequest(Base):
    __tablename__ = "venue_hire_requests"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    purpose = Column(String(255), nullable=False)
    guest_count = Column(Integer, nullable=False)
    quoted_price = Column(Float, nullable=False)
    status = Column(String(30), default="pending")  # confirmed, escalated, rejected, counter_proposed
    agent_notes = Column(Text, default="")
    alternatives_json = Column(Text, default="[]")
    created_at = Column(DateTime, default=utcnow)

    user = relationship("User")
    venue = relationship("Venue")

class WaitlistEntry(Base):
    __tablename__ = "waitlist_entries"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    tier_id = Column(Integer, ForeignKey("ticket_tiers.id"), nullable=False)
    quantity = Column(Integer, default=1)
    status = Column(String(20), default="waiting")  # waiting, offered, claimed, expired
    hold_expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    user = relationship("User")
    event = relationship("Event")
    tier = relationship("TicketTier")

class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(150), nullable=False)
    message = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)

class AgentBelief(Base):
    __tablename__ = "agent_beliefs"
    id = Column(Integer, primary_key=True, index=True)
    agent_name = Column(String(60), index=True, nullable=False)
    key = Column(String(120), index=True, nullable=False)
    value_json = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("agent_name", "key", name="uq_agent_belief"),)

class AgentMessageLog(Base):
    __tablename__ = "agent_message_logs"
    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(String(64), index=True, nullable=False)
    sender = Column(String(60), nullable=False)
    receiver = Column(String(60), nullable=False)
    performative = Column(String(20), nullable=False)  # REQUEST, AGREE, REFUSE, PROPOSE, INFORM, FAILURE
    action = Column(String(80), nullable=False)
    content_json = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=utcnow, index=True)
