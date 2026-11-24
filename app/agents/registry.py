from app.agents.bus import MessageBus
from app.agents.booking_agent import BookingAgent
from app.agents.ticket_agent import TicketAgent
from app.agents.venue_agent import VenueAgent
from app.agents.waitlist_agent import WaitlistAgent
from app.agents.notification_agent import NotificationAgent


def create_bus() -> MessageBus:
    bus = MessageBus()
    bus.register(BookingAgent())
    bus.register(TicketAgent())
    bus.register(VenueAgent())
    bus.register(WaitlistAgent(), subscribe_inform=True)
    bus.register(NotificationAgent(), subscribe_inform=True)
    return bus


bus = create_bus()
waitlist_agent = bus.agents["WaitlistAgent"] 
