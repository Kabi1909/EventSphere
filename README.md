# EventSphere — Multi-Agent Venue Booking & Digital Ticketing Platform

A mobile-friendly, full-stack **FastAPI** web application built for **IT4123 Agent Based Computing (ICA 02)** where every venue allocation, ticket booking, cancellation refund, gate check-in, and user notification is governed by autonomous software agents communicating through structured JSON messages (`REQUEST`, `AGREE`, `REFUSE`, `PROPOSE`, `INFORM`, `FAILURE`).

**Live Deployment (HTTPS):** https://eventsphere-uo13.onrender.com

---

## Demo Accounts

All demo accounts, sample venues, and starter events are seeded automatically on startup.

| Role | Email | Password | Primary Access |
| :--- | :--- | :--- | :--- |
| **Admin / Organiser** | `admin@eventsphere.edu` | `Admin@123` | `/admin` (Control Center, Venues, Events, Agent Beliefs & Logs) |
| **Gate Staff** | `staff@eventsphere.edu` | `Staff@123` | `/staff/scanner` (Live Camera QR Scanner & Manual Code Entry) |
| **Attendee** | `student@eventsphere.edu` | `Student@123` | `/` (Events), `/my-bookings` (QR Passes), `/venue-hire` |

---

## Key Features & Recent Updates

### Organiser & Multi-Agent Control Center (`/admin`)
- **Modern Hero & Floating KPI Strip:** Real-time overview of Active Events, Tickets Sold / Capacity, Gate Check-Ins, and Total Simulated Revenue (`LKR`).
- **Sticky Quick-Jump Navigation:** Instant anchor links to Event Sales, Escalated Hires, Venue/Event Management, Persistent Agent Beliefs, and Message Bus Logs.
- **Full Event Lifecycle Management:** Create, publish, edit (via modal), and cancel events with thumbnails, category pills, slim occupancy progress bars, optional banner image uploads, live capacity validation, and an estimated max tier revenue (`Price × Seats`) calculator.
- **Venue Management (Create & Edit):** Built-in **Add New Venue / Edit Venue** switcher with a Faculty Location dropdown (`Faculty of Applied Science`, `Faculty of Business Studies`, `Faculty of Technology`), capacity, hourly hire rate, and facilities description.
- **Confirmation & Success Modals:** Dedicated confirmation modal before cancelling an event, plus tailored success modals after creating/editing/cancelling events or creating/editing venues.
- **Interactive Agent Beliefs & Fraud Highlighting:** Filterable belief cards by agent (`BookingAgent`, `TicketAgent`, `VenueAgent`, `NotificationAgent`) with automatic red alert highlighting for suspicious repeated ticket scans (`>= 3` attempts).
- **Searchable Message Bus Traceability Log:** Live filtering by FIPA performative (`REQUEST`, `AGREE`, `REFUSE / FAILURE`, `INFORM`), real-time keyword search, and click-to-copy Conversation ID badges.

### Attendee & Gate Staff Experience
- **Public Events Catalog (`/`):** Responsive hero banner, floating search & quick-browse category pills, starting-price overlay badges, live seat availability progress bars (`Available`, `Selling Fast`, `Sold Out`), and a role-aware Private Venue Hire banner (hidden for Admin/Staff).
- **Digital QR Passes (`/my-bookings`):** Unique, cryptographically generated QR codes per ticket with simulated payment references and policy-based booking cancellations.
- **Mobile Gate Check-In Scanner (`/staff/scanner`):** Browser-based live camera QR scanning (over HTTPS) with manual ticket-code entry fallback and immediate admission/rejection reasons from `TicketAgent`.
- **Private Venue Hire (`/venue-hire`):** Automated slot clash checks, flat hourly quoting, auto-confirmation for routine requests, and automatic Admin escalation for large or long bookings.

---

## Multi-Agent Architecture

Agents own independent goals, persist internal beliefs in the database (`AgentBelief`), apply documented decision rules, and communicate strictly through the **MessageBus** rather than direct method calls:

- **MessageBus (`app/agents/bus.py`):** Dispatches structured JSON `AgentMessage` envelopes and logs every performative, sender, receiver, action, payload, and `conversation_id` to `AgentMessageLog` for end-to-end traceability.
- **VenueAgent (`app/agents/venue_agent.py`):**
  - Validates event schedules (`VALIDATE_EVENT_VENUE`) and private hire requests (`REQUEST_PRIVATE_HIRE`) using a **60-minute turnaround buffer** to prevent venue clashes.
  - Calculates quotes using standard flat hourly rates (`1.0x`).
  - Auto-confirms routine private hires (`<= 4.0 hours` and `<= 150 guests`) and escalates larger/longer hires for Admin approval.
  - Tracks persistent beliefs (`clashes_prevented`, `hires_auto_confirmed`, `hires_escalated`).
- **BookingAgent (`app/agents/booking_agent.py`):**
  - Enforces per-booking quantity limits and atomic seat allocation to prevent overselling under concurrent requests.
  - Calculates tiered cancellation refunds and processes full refunds + `INFORM` broadcasts when an Admin cancels an event (`EVENT_CANCELLED_REFUNDS`).
- **TicketAgent (`app/agents/ticket_agent.py`):**
  - Issues unique, unguessable QR codes and enforces single-entry gate admission (`SCAN_TICKET`) with explicit reasons for invalid, already-used, cancelled, wrong-event, or out-of-window scans.
  - **Part C — Suspicious Repeated Scan Detection:** Tracks persistent scan attempt counters per ticket code to flag potential shared-screenshot fraud and alert staff/admins.
- **WaitlistAgent (`app/agents/waitlist_agent.py`):** Handles seat-release events and timed seat-hold expiry logic.
- **NotificationAgent (`app/agents/notification_agent.py`):** Listens to broadcast `INFORM` events across all agents and creates persistent in-app user notifications (`/notifications`).

---

## Tech Stack

- **Backend:** Python 3.11+, FastAPI, Uvicorn, SQLAlchemy, Pydantic
- **Database:** SQLite (local development) / PostgreSQL (production on Render)
- **Frontend:** Jinja2, HTML5, CSS3, Vanilla JavaScript, Bootstrap 5, Bootstrap Icons
- **Security:** Hashed passwords, session-based authentication, strict role-based access control (`admin`, `staff`, `attendee`), and CSRF token validation on all `POST` forms
- **QR & Testing:** `qrcode` (Python), browser QR scanning, Pytest

---

## Local Setup

1. **Clone the Repository & Create a Virtual Environment**
   ```bash
   git clone <your-repo-url>
   cd EventSphere_IT4123_Project
   python -m venv venv