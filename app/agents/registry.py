from app.agents.bus import MessageBus
from app.agents.booking_agent import BookingAgent
from app.agents.ticket_agent import TicketAgent
from app.agents.venue_agent import VenueAgent
from app.agents.notification_agent import NotificationAgent


def create_bus() -> MessageBus:
    bus = MessageBus()
    bus.register(BookingAgent())
    bus.register(TicketAgent())
    bus.register(VenueAgent())
    bus.register(NotificationAgent())
    return bus


bus = create_bus() 
