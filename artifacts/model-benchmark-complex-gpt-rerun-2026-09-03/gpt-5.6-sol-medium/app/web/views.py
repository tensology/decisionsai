from app.services.tickets import TicketService
from app.utils.query import query_flag


def board_tickets_view(service: TicketService, board_id: object, query: dict[str, object]):
    include_archived = query_flag(query.get("include_archived"))
    tickets = service.list_board_tickets(
        board_id, include_archived=include_archived
    )
    return {
        "items": [{"id": ticket.id, "title": ticket.title, "archived": ticket.archived} for ticket in tickets],
        "meta": {"count": len(tickets), "include_archived": include_archived},
    }
