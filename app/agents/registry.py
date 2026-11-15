from app.agents.bus import MessageBus
from app.agents.venue_agent import VenueAgent
from app.agents.booking_agent import BookingAgent
from app.agents.ticket_agent import TicketAgent
from app.agents.waitlist_agent import WaitlistAgent
from app.agents.notification_agent import NotificationAgent

bus = MessageBus()
venue_agent = VenueAgent()
booking_agent = BookingAgent()
ticket_agent = TicketAgent()
waitlist_agent = WaitlistAgent()
notification_agent = NotificationAgent()

bus.register(venue_agent, subscribe_inform=False)
bus.register(booking_agent, subscribe_inform=False)
bus.register(ticket_agent, subscribe_inform=False)
bus.register(waitlist_agent, subscribe_inform=True)
bus.register(notification_agent, subscribe_inform=True)
