# EventSphere — Multi-Agent Venue Booking & Digital Ticketing Platform

A full-stack FastAPI web application where every booking, venue allocation, ticket validation, waitlist promotion, and user notification is governed by autonomous software agents communicating via structured JSON messages (`REQUEST`, `AGREE`, `REFUSE`, `PROPOSE`, `INFORM`, `FAILURE`).

## Demo Accounts
| Role | Email | Password |
| :--- | :--- | :--- |
| **Admin / Organiser** | `admin@eventsphere.edu` | `Admin@123` |
| **Gate Staff** | `staff@eventsphere.edu` | `Staff@123` |
| **Attendee** | `student@eventsphere.edu` | `Student@123` |

## Local Setup
1. **Clone & Create Virtual Environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```
2. **Environment Variables**
   Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
   - `SECRET_KEY`: Random string for session cookies & CSRF protection.
   - `DATABASE_URL`: Defaults to `sqlite:///./eventsphere.db` locally. Automatically switches to PostgreSQL on Render.

3. **Run the Server**
   ```bash
   uvicorn app.main:app --reload
   ```
   Open `http://127.0.0.1:8000` in your browser. Demo accounts and sample venues/events are seeded automatically on startup.

## Running Tests
```bash
pytest -v
```
Includes 12 automated tests covering all 5 agents, concurrency/overbooking prevention, duplicate QR scan detection, waitlist auto-expiry sweeps, private hire counter-proposals, and role-based access control.

## Multi-Agent Architecture
- **MessageBus (`app/agents/bus.py`)**: Asynchronous JSON message dispatcher logging every performative and `conversation_id` to the database for live traceability.
- **VenueAgent (`app/agents/venue_agent.py`)**: Enforces 60-minute turnaround buffers, calculates weekend/peak pricing (1.25x multiplier), auto-approves routine hires (<= 150 guests, <= 4 hrs), escalates large hires, and proposes alternative slots/venues on conflict.
- **BookingAgent (`app/agents/booking_agent.py`)**: Enforces max 6 tickets per booking, uses database row locking / atomic stock decrement to prevent overselling, calculates tiered cancellation refunds (100% >48h, 50% 24-48h, 0% <24h), and triggers full refunds + notifications upon event cancellation.
- **TicketAgent (`app/agents/ticket_agent.py`)**: Generates cryptographically random QR codes, tracks persistent scan beliefs (detecting suspicious repeated scans), and enforces single-entry admission rules.
- **WaitlistAgent (`app/agents/waitlist_agent.py`)**: Reacts to `SEATS_RELEASED` `INFORM` events to allocate 15-minute timed seat holds, and runs a proactive background sweep every 30 seconds to expire unclaimed holds and re-offer them.
- **NotificationAgent (`app/agents/notification_agent.py`)**: Subscribes to broadcast `INFORM` messages and generates persistent user notifications.
