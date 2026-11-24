import asyncio
import io
import base64
import json
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager

import qrcode
from fastapi import FastAPI, Depends, Request, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import engine, Base, get_db, SessionLocal
from app.models import (
    User, Venue, Event, TicketTier, Booking, Ticket,
    VenueHireRequest, Notification, AgentMessageLog, AgentBelief
)
from app.security import SECRET_KEY, hash_password, verify_password, get_csrf_token, validate_csrf
from app.agents.protocol import AgentMessage, Performative
from app.agents.registry import bus
from app.seed import seed_database


async def waitlist_expiry_worker():
    """Proactive background loop: periodically runs WaitlistAgent expiry sweep without user input."""
    while True:
        await asyncio.sleep(30)
        db = SessionLocal()
        try:
            waitlist_agent = bus.agents.get("WaitlistAgent")
            if waitlist_agent:
                waitlist_agent.run_expiry_sweep(db, bus)
        except Exception:
            db.rollback()
        finally:
            db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_database(db)
    finally:
        db.close()

    sweep_task = asyncio.create_task(waitlist_expiry_worker())
    try:
        yield
    finally:
        sweep_task.cancel()
        try:
            await sweep_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="EventSphere Multi-Agent Platform", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax")
templates = Jinja2Templates(directory="app/templates")


def make_qr_base64(data: str) -> str:
    img = qrcode.make(data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def get_current_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    uid = request.session.get("user_id")
    if not uid:
        return None
    return db.query(User).filter_by(id=uid).first()


def require_role(user: Optional[User], roles: list[str]):
    if not user:
        raise HTTPException(status_code=401, detail="Please sign in first.")
    if user.role not in roles:
        raise HTTPException(status_code=403, detail=f"Access denied. Required role: {', '.join(roles)}")


def ctx(request: Request, db: Session, user: Optional[User], **kwargs):
    unread = 0
    if user:
        unread = db.query(Notification).filter_by(user_id=user.id, is_read=False).count()
    return {
        "user": user,
        "csrf_token": get_csrf_token(request),
        "unread_count": unread,
        "flash": request.session.pop("flash", None),
        **kwargs,
    }


def set_flash(request: Request, msg: str, category: str = "info"):
    request.session["flash"] = {"msg": msg, "category": category}


# -------------------- PUBLIC & AUTH ROUTES --------------------
@app.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    q: str = "",
    category: str = "",
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    query = db.query(Event).filter(Event.status == "published")
    if q:
        query = query.filter(Event.title.ilike(f"%{q}%") | Event.description.ilike(f"%{q}%"))
    if category:
        query = query.filter(Event.category == category)
    events = query.order_by(Event.start_time.asc()).all()
    categories = ["Concert", "Workshop", "Seminar", "Conference"]
    return templates.TemplateResponse(request, "index.html", ctx(request, db, user, events=events, q=q, category=category, categories=categories))


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db), user: Optional[User] = Depends(get_current_user)):
    return templates.TemplateResponse(request, "login.html", ctx(request, db, user))


@app.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    user = db.query(User).filter_by(email=email.strip().lower()).first()
    if not user or not verify_password(password, user.password_hash):
        set_flash(request, "Invalid email or password.", "danger")
        return RedirectResponse("/login", status_code=303)
    request.session["user_id"] = user.id
    set_flash(request, f"Welcome back, {user.name}!", "success")
    if user.role == "admin":
        return RedirectResponse("/admin", status_code=303)
    if user.role == "staff":
        return RedirectResponse("/staff/scanner", status_code=303)
    return RedirectResponse("/", status_code=303)


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request, db: Session = Depends(get_db), user: Optional[User] = Depends(get_current_user)):
    return templates.TemplateResponse(request, "register.html", ctx(request, db, user))


@app.post("/register")
def register_submit(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    clean_email = email.strip().lower()
    if db.query(User).filter_by(email=clean_email).first():
        set_flash(request, "Email already registered.", "danger")
        return RedirectResponse("/register", status_code=303)
    new_user = User(name=name.strip(), email=clean_email, password_hash=hash_password(password), role="attendee")
    db.add(new_user)
    db.commit()
    request.session["user_id"] = new_user.id
    set_flash(request, "Account created! Welcome to EventSphere.", "success")
    return RedirectResponse("/", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


# -------------------- EVENT DETAILS & BOOKING --------------------
@app.get("/events/{event_id}", response_class=HTMLResponse)
def event_detail(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    event = db.query(Event).filter_by(id=event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return templates.TemplateResponse(request, "event_detail.html", ctx(request, db, user, event=event, user_offers=[]))


@app.post("/events/{event_id}/book")
def book_tickets(
    event_id: int,
    request: Request,
    tier_id: int = Form(...),
    quantity: int = Form(...),
    waitlist_id: Optional[int] = Form(None),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    validate_csrf(request, csrf_token)
    if not user:
        return RedirectResponse("/login", status_code=303)

    msg = AgentMessage(
        sender=f"User:{user.id}",
        receiver="BookingAgent",
        performative=Performative.REQUEST,
        action="BOOK_TICKETS",
        content={
            "user_id": user.id,
            "event_id": event_id,
            "tier_id": tier_id,
            "quantity": quantity,
            "waitlist_id": waitlist_id,
        },
    )
    reply = bus.send(db, msg)
    db.commit()

    if reply.performative == Performative.AGREE:
        set_flash(request, f"Booking confirmed! Payment Ref: {reply.content['payment_ref']}", "success")
        return RedirectResponse("/my-bookings", status_code=303)
    else:
        set_flash(request, f"BookingAgent Refused: {reply.content.get('reason')}", "danger")
        return RedirectResponse(f"/events/{event_id}", status_code=303)


@app.get("/my-bookings", response_class=HTMLResponse)
def my_bookings(request: Request, db: Session = Depends(get_db), user: Optional[User] = Depends(get_current_user)):
    if not user:
        return RedirectResponse("/login", status_code=303)
    require_role(user, ["attendee"])
    bookings = db.query(Booking).filter_by(user_id=user.id).order_by(Booking.created_at.desc()).all()
    qr_map = {}
    for b in bookings:
        for t in b.tickets:
            qr_map[t.id] = make_qr_base64(t.code)
    return templates.TemplateResponse(request, "my_bookings.html", ctx(request, db, user, bookings=bookings, waitlists=[], qr_map=qr_map))


@app.post("/bookings/{booking_id}/cancel")
def cancel_booking(
    booking_id: int,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    validate_csrf(request, csrf_token)
    if not user:
        return RedirectResponse("/login", status_code=303)
    msg = AgentMessage(
        sender=f"User:{user.id}",
        receiver="BookingAgent",
        performative=Performative.REQUEST,
        action="CANCEL_BOOKING",
        content={"booking_id": booking_id, "user_id": user.id},
    )
    reply = bus.send(db, msg)
    db.commit()
    if reply.performative == Performative.AGREE:
        set_flash(request, f"Booking cancelled. Refund: LKR {reply.content['refund_amount']:,.2f} ({reply.content['policy_note']})", "info")
    else:
        set_flash(request, f"Cancellation Refused: {reply.content.get('reason')}", "danger")
    return RedirectResponse("/my-bookings", status_code=303)


# -------------------- PRIVATE VENUE HIRE --------------------
@app.get("/venue-hire", response_class=HTMLResponse)
def venue_hire_page(request: Request, db: Session = Depends(get_db), user: Optional[User] = Depends(get_current_user)):
    if not user:
        return RedirectResponse("/login", status_code=303)
    require_role(user, ["attendee"])
    venues = db.query(Venue).filter_by(is_private_hire_open=True).all()
    my_hires = db.query(VenueHireRequest).filter_by(user_id=user.id).order_by(VenueHireRequest.created_at.desc()).all()
    parsed_hires = []
    for h in my_hires:
        parsed_hires.append((h, json.loads(h.alternatives_json or "[]")))
    return templates.TemplateResponse(request, "venue_hire.html", ctx(request, db, user, venues=venues, hires=parsed_hires))


@app.post("/venue-hire")
def submit_venue_hire(
    request: Request,
    venue_id: int = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    purpose: str = Form(...),
    guest_count: int = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    validate_csrf(request, csrf_token)
    if not user:
        return RedirectResponse("/login", status_code=303)

    msg = AgentMessage(
        sender=f"User:{user.id}",
        receiver="VenueAgent",
        performative=Performative.REQUEST,
        action="REQUEST_PRIVATE_HIRE",
        content={
            "user_id": user.id,
            "venue_id": venue_id,
            "start_time": start_time,
            "end_time": end_time,
            "purpose": purpose,
            "guest_count": guest_count,
        },
    )
    reply = bus.send(db, msg)
    venue = db.query(Venue).filter_by(id=venue_id).first()

    if reply.performative == Performative.REFUSE:
        db.commit()
        set_flash(request, f"VenueAgent Refused: {reply.content.get('reason')}", "danger")
        return RedirectResponse("/venue-hire", status_code=303)

    status = reply.content.get("status", "counter_proposed")
    quoted_price = reply.content.get("quoted_price", 0.0)
    alts = reply.content.get("alternatives", [])

    hire = VenueHireRequest(
        user_id=user.id,
        venue_id=venue_id,
        start_time=datetime.fromisoformat(start_time),
        end_time=datetime.fromisoformat(end_time),
        purpose=purpose,
        guest_count=guest_count,
        quoted_price=quoted_price,
        status=status,
        agent_notes=reply.content.get("reason", ""),
        alternatives_json=json.dumps(alts),
    )
    db.add(hire)
    db.flush()

    bus.send(
        db,
        AgentMessage(
            conversation_id=msg.conversation_id,
            sender="VenueAgent",
            receiver="ALL",
            performative=Performative.INFORM,
            action="HIRE_DECISION",
            content={
                "user_id": user.id,
                "venue_name": venue.name if venue else "Venue",
                "status": status,
                "reason": reply.content.get("reason", ""),
            },
        ),
    )
    db.commit()

    if status == "confirmed":
        set_flash(request, f"Private hire auto-confirmed! Total: LKR {quoted_price:,.2f}", "success")
    elif status == "escalated":
        set_flash(request, "Request escalated to Admin for approval due to size/duration.", "warning")
    else:
        set_flash(request, "Slot unavailable — VenueAgent proposed alternative times/venues below!", "warning")
    return RedirectResponse("/venue-hire", status_code=303)


@app.get("/notifications", response_class=HTMLResponse)
def notifications_page(request: Request, db: Session = Depends(get_db), user: Optional[User] = Depends(get_current_user)):
    if not user:
        return RedirectResponse("/login", status_code=303)
    notes = db.query(Notification).filter_by(user_id=user.id).order_by(Notification.created_at.desc()).all()
    for n in notes:
        n.is_read = True
    db.commit()
    return templates.TemplateResponse(request, "notifications.html", ctx(request, db, user, notes=notes))


# -------------------- STAFF GATE SCANNER --------------------
@app.get("/staff/scanner", response_class=HTMLResponse)
def scanner_page(
    request: Request,
    event_id: Optional[int] = None,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    require_role(user, ["staff"])
    events = db.query(Event).filter(Event.status == "published").order_by(Event.start_time.asc()).all()
    selected_id = event_id or (events[0].id if events else None)
    return templates.TemplateResponse(request, "scanner.html", ctx(request, db, user, events=events, selected_id=selected_id, scan_result=None))


@app.post("/staff/scanner", response_class=HTMLResponse)
def scan_ticket_submit(
    request: Request,
    event_id: int = Form(...),
    code: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    validate_csrf(request, csrf_token)
    require_role(user, ["staff"])
    events = db.query(Event).filter(Event.status == "published").order_by(Event.start_time.asc()).all()

    msg = AgentMessage(
        sender=f"Staff:{user.id}",
        receiver="TicketAgent",
        performative=Performative.REQUEST,
        action="SCAN_TICKET",
        content={"event_id": event_id, "code": code},
    )
    reply = bus.send(db, msg)
    db.commit()

    scan_result = {
        "admitted": reply.performative == Performative.AGREE,
        "performative": reply.performative.value,
        **reply.content,
    }
    return templates.TemplateResponse(request, "scanner.html", ctx(request, db, user, events=events, selected_id=event_id, scan_result=scan_result))


# -------------------- ADMIN DASHBOARD, VENUES, EVENTS & HIRES --------------------
@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request, db: Session = Depends(get_db), user: Optional[User] = Depends(get_current_user)):
    require_role(user, ["admin"])
    # Exclude cancelled events from the active dashboard list
    events = db.query(Event).filter(Event.status != "cancelled").order_by(Event.start_time.desc()).all()
    venues = db.query(Venue).all()
    escalated_hires = db.query(VenueHireRequest).filter_by(status="escalated").all()
    logs = db.query(AgentMessageLog).order_by(AgentMessageLog.id.desc()).limit(40).all()
    beliefs = db.query(AgentBelief).order_by(AgentBelief.agent_name.asc()).all()

    event_stats = []
    for ev in events:
        total_cap = sum(t.total_qty for t in ev.ticket_tiers)
        sold = sum(t.total_qty - t.remaining_qty for t in ev.ticket_tiers)
        revenue = db.query(func.coalesce(func.sum(Booking.total_paid), 0.0)).filter(
            Booking.event_id == ev.id, Booking.status == "confirmed"
        ).scalar()
        checkins = db.query(Ticket).filter_by(event_id=ev.id, status="used").count()
        event_stats.append({"event": ev, "capacity": total_cap, "sold": sold, "revenue": revenue, "checkins": checkins})

    return templates.TemplateResponse(
        request,
        "admin_dashboard.html",
        ctx(request, db, user, event_stats=event_stats, venues=venues, escalated_hires=escalated_hires, logs=logs, beliefs=beliefs),
    )


@app.post("/admin/venues")
def admin_create_venue(
    request: Request,
    name: str = Form(...),
    location: str = Form(...),
    capacity: int = Form(...),
    hourly_rate: float = Form(...),
    description: str = Form(""),
    image_url: str = Form("https://images.unsplash.com/photo-1517457373958-b7bdd4587205?w=800"),
    is_private_hire_open: Optional[str] = Form(None),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    validate_csrf(request, csrf_token)
    require_role(user, ["admin"])
    v = Venue(
        name=name,
        location=location,
        capacity=capacity,
        hourly_rate=hourly_rate,
        description=description,
        image_url=image_url,
        is_private_hire_open=bool(is_private_hire_open),
    )
    db.add(v)
    db.commit()
    set_flash(request, f"Venue '{name}' created.", "success")
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/events")
async def admin_create_event(
    request: Request,
    title: str = Form(...),
    category: str = Form(...),
    venue_id: int = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    description: str = Form(""),
    status: str = Form("published"),
    tier_name: str = Form(...),
    tier_price: float = Form(...),
    tier_qty: int = Form(...),
    image_file: Optional[UploadFile] = File(None),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    validate_csrf(request, csrf_token)
    require_role(user, ["admin"])
    venue = db.query(Venue).filter_by(id=venue_id).first()
    if not venue:
        set_flash(request, "Venue not found.", "danger")
        return RedirectResponse("/admin", status_code=303)
    if tier_qty > venue.capacity:
        set_flash(request, f"Ticket quantity ({tier_qty}) cannot exceed venue capacity ({venue.capacity}).", "danger")
        return RedirectResponse("/admin", status_code=303)

    msg = AgentMessage(
        sender=f"Admin:{user.id}",
        receiver="VenueAgent",
        performative=Performative.REQUEST,
        action="VALIDATE_EVENT_VENUE",
        content={"venue_id": venue_id, "start_time": start_time, "end_time": end_time},
    )
    reply = bus.send(db, msg)
    if reply.performative != Performative.AGREE:
        db.commit()
        set_flash(request, f"VenueAgent Refused: {reply.content.get('reason')}", "danger")
        return RedirectResponse("/admin", status_code=303)

    uploaded_image_uri = None
    if image_file and image_file.filename:
        raw_bytes = await image_file.read()
        if raw_bytes:
            b64_str = base64.b64encode(raw_bytes).decode("utf-8")
            mime = image_file.content_type or "image/jpeg"
            uploaded_image_uri = f"data:{mime};base64,{b64_str}"

    ev = Event(
        title=title,
        category=category,
        venue_id=venue_id,
        start_time=datetime.fromisoformat(start_time),
        end_time=datetime.fromisoformat(end_time),
        description=description,
        image_url=uploaded_image_uri,
        status=status,
    )
    db.add(ev)
    db.flush()
    db.add(TicketTier(event_id=ev.id, name=tier_name, price=tier_price, total_qty=tier_qty, remaining_qty=tier_qty))
    db.commit()
    set_flash(request, f"Event '{title}' created ({status}).", "success")
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/events/{event_id}/status")
def admin_update_event_status(
    event_id: int,
    request: Request,
    new_status: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    validate_csrf(request, csrf_token)
    require_role(user, ["admin"])
    ev = db.query(Event).filter_by(id=event_id).first()
    if not ev:
        raise HTTPException(status_code=404)
    ev.status = new_status
    if new_status == "cancelled":
        msg = AgentMessage(
            sender=f"Admin:{user.id}",
            receiver="BookingAgent",
            performative=Performative.REQUEST,
            action="EVENT_CANCELLED_REFUNDS",
            content={"event_id": ev.id},
        )
        bus.send(db, msg)
    db.commit()
    set_flash(request, f"Event status updated to {new_status}.", "info")
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/events/{event_id}/edit")
def admin_edit_event(
    event_id: int,
    request: Request,
    title: str = Form(...),
    category: str = Form(...),
    venue_id: int = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    description: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    validate_csrf(request, csrf_token)
    require_role(user, ["admin"])
    ev = db.query(Event).filter_by(id=event_id).first()
    if not ev:
        raise HTTPException(status_code=404)
    ev.title = title.strip()
    ev.category = category
    ev.venue_id = venue_id
    ev.start_time = datetime.fromisoformat(start_time)
    ev.end_time = datetime.fromisoformat(end_time)
    ev.description = description.strip()
    db.commit()
    set_flash(request, f"Event '{ev.title}' updated successfully.", "success")
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/hires/{hire_id}/decide")
def admin_decide_hire(
    hire_id: int,
    request: Request,
    decision: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    validate_csrf(request, csrf_token)
    require_role(user, ["admin"])
    hire = db.query(VenueHireRequest).filter_by(id=hire_id).first()
    if not hire:
        raise HTTPException(status_code=404)
    hire.status = "confirmed" if decision == "approve" else "rejected"
    bus.send(
        db,
        AgentMessage(
            sender=f"Admin:{user.id}",
            receiver="ALL",
            performative=Performative.INFORM,
            action="HIRE_DECISION",
            content={
                "user_id": hire.user_id,
                "venue_name": hire.venue.name,
                "status": hire.status,
                "reason": "Reviewed by University Admin.",
            },
        ),
    )
    db.commit()
    set_flash(request, f"Hire request #{hire.id} marked {hire.status}.", "success")
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/venues/{venue_id}/edit")
def admin_edit_venue(
    venue_id: int,
    request: Request,
    csrf_token: str = Form(...),
    name: str = Form(...),
    location: str = Form(...),
    capacity: int = Form(...),
    hourly_rate: float = Form(...),
    description: str = Form(""),
    image_url: str = Form(""),
    is_private_hire_open: str = Form("1"),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    validate_csrf(request, csrf_token)
    require_role(user, ["admin"])
    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    if not venue:
        set_flash(request, "Venue not found.", "danger")
        return RedirectResponse("/admin", status_code=303)

    venue.name = name.strip()
    venue.location = location.strip()
    venue.capacity = capacity
    venue.hourly_rate = hourly_rate
    venue.description = description.strip()
    if image_url.strip():
        venue.image_url = image_url.strip()
    venue.is_private_hire_open = is_private_hire_open == "1"
    db.commit()

    set_flash(request, f"Venue '{venue.name}' updated successfully.", "success")
    return RedirectResponse("/admin", status_code=303) 
