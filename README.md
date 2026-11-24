# EventSphere — Multi-Agent Venue Booking & Digital Ticketing Platform

A mobile-friendly, full-stack **FastAPI** web application built for **IT4123 Agent Based Computing (ICA 02)** where every venue allocation, ticket booking, cancellation refund, waitlist offer, gate check-in, and user notification is governed by autonomous software agents communicating through structured JSON messages (`REQUEST`, `AGREE`, `REFUSE`, `PROPOSE`, `INFORM`, `FAILURE`).

**Live Deployment (HTTPS):** https://eventsphere-uo13.onrender.com

---

## Demo Accounts

All demo accounts, sample venues, and starter events are seeded automatically on startup.

| Role | Email | Password | Primary Access |
| :--- | :--- | :--- | :--- |
| **Admin / Organiser** | `admin@eventsphere.edu` | `Admin@123` | `/admin` (Control Center, Venues, Events, Agent Beliefs & Logs) |
| **Gate Staff** | `staff@eventsphere.edu` | `Staff@123` | `/staff/scanner` (Live Camera QR Scanner & Manual Code Entry) |
| **Attendee** | `student@eventsphere.edu` | `Student@123` | `/` (Events), `/my-bookings` (QR Passes), `/venue-hire`, `/notifications` |

---

## Key Features

### Organiser & Multi-Agent Control Center (`/admin`)
- **Hero & Floating KPI Strip:** Real-time summary of Active Events, Tickets Sold / Capacity, Gate Check-Ins, and Total Simulated Revenue (`LKR`).
- **Full Event Lifecycle Management:** Create, publish, edit (via modal), and cancel events with banner image uploads, live capacity validation, estimated max revenue preview, and confirmation/success modals.
- **Venue Management (Create & Edit):** Built-in **Add New Venue / Edit Venue** switcher with Faculty Location selection, capacity, flat hourly hire rate, and private-hire availability toggle.
- **Escalated Private Hire Review:** One-click Admin approval or rejection of large/long private hire requests with automatic attendee notification broadcasts.
- **Persistent Agent Beliefs & Fraud Highlighting:** Live belief cards for each agent with automatic alert highlighting for suspicious repeated ticket scans (`>= 3` attempts).
- **Searchable Message Bus Traceability Log:** Filter agent messages by FIPA performative (`REQUEST`, `AGREE`, `REFUSE / FAILURE`, `INFORM`), search by keyword, and copy `conversation_id` traces.

### Attendee & Gate Staff Experience
- **Public Events Catalog (`/`):** Mobile-responsive event listing with keyword search, category pills, starting-price badges, and live seat availability indicators.
- **Digital QR Passes (`/my-bookings`):** Unique, cryptographically generated QR codes per ticket with simulated payment references (`SIM-...`) and tiered cancellation refunds.
- **Mobile Gate Check-In Scanner (`/staff/scanner`):** Browser-based live camera QR scanning over HTTPS with manual code entry fallback and explicit admission/rejection reasons from `TicketAgent`.
- **Private Venue Hire (`/venue-hire`):** Automated clash checks with a 60-minute turnaround buffer, flat hourly quotes, auto-confirmation for routine bookings, Admin escalation for large/long bookings, and counter-proposed alternative venues/time slots on conflicts.

---

## Multi-Agent Architecture

Agents own independent goals, persist internal beliefs in the database (`AgentBelief`), enforce documented decision rules, standardize timestamps on **Sri Lanka Standard Time (`UTC+05:30`)**, and communicate exclusively through the **MessageBus**:

- **MessageBus (`app/agents/bus.py` & `app/agents/registry.py`):** Dispatches structured JSON `AgentMessage` envelopes, routes broadcast `INFORM` (`receiver="ALL"`) events to subscribed listeners (`WaitlistAgent` and `NotificationAgent`), and logs every message with its `conversation_id` to `AgentMessageLog`.
- **VenueAgent (`app/agents/venue_agent.py`):**
  - Enforces a **60-minute turnaround buffer** around all events and confirmed private hires.
  - Quotes hires at standard flat hourly rates (`1.0x`), auto-confirms routine hires (`<= 4.0 hours` and `<= 150 guests`), and escalates larger or longer hires to Admin.
  - Returns `PROPOSE` with usable alternative venues and shifted time slots whenever a private hire request clashes with an existing booking.
- **BookingAgent (`app/agents/booking_agent.py`):**
  - Enforces a maximum of **6 tickets per booking** and uses an **atomic conditional SQL `UPDATE`** on `TicketTier.remaining_qty` to prevent overselling under concurrent requests.
  - Applies tiered attendee cancellation refunds (`100%` `> 48h`, `50%` `24–48h`, `0%` `< 24h` before start; blocked if a ticket is already used) and issues `100%` refunds when an Admin cancels an event (`EVENT_CANCELLED_REFUNDS`).
- **TicketAgent (`app/agents/ticket_agent.py`):**
  - Generates unguessable 128-bit QR codes (`EVT-...`) and enforces single-entry gate admission (`3 hours` before start until event end).
  - Tracks persistent `scan_attempts` per ticket code to detect and flag potential shared-screenshot fraud (`>= 3` scans).
- **WaitlistAgent (`app/agents/waitlist_agent.py` & `app/main.py`):**
  - **Reactive:** Listens for `SEATS_RELEASED` `INFORM` broadcasts and automatically places freed seats on a **15-minute timed hold** for the next queued attendee.
  - **Proactive:** Runs a background `asyncio` sweep every **30 seconds** without user input (`run_expiry_sweep`) to expire unclaimed holds, return seats to inventory, and re-offer them to the next person in line.
- **NotificationAgent (`app/agents/notification_agent.py`):** Subscribes to all `INFORM` broadcasts (`BOOKING_CONFIRMED`, `SEATS_RELEASED`, `EVENT_CANCELLED_USER_REFUND`, `WAITLIST_SEAT_OFFERED`, `WAITLIST_HOLD_EXPIRED`, `HIRE_DECISION`) and creates persistent in-app user notifications (`/notifications`).

---

## Tech Stack

- **Backend:** Python 3.11+, FastAPI, Uvicorn, SQLAlchemy, Pydantic
- **Database:** SQLite (local development) / PostgreSQL (production on Render)
- **Frontend:** Jinja2, HTML5, CSS3, Vanilla JavaScript, Bootstrap 5, Bootstrap Icons
- **Security:** Hashed passwords, signed session cookies, role-based access control (`admin`, `staff`, `attendee`), and CSRF token validation on all `POST` routes
- **QR & Testing:** `qrcode` (Python), browser QR scanning, Pytest

---

## Local Setup

1. **Clone the Repository & Create a Virtual Environment**
   ```bash
   git clone https://github.com/Kabi1909/EventSphere.git 
   cd EventSphere_IT4123_Project
   python -m venv venv 
