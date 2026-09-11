from collections.abc import Iterable
from app.models.ticket import Ticket
from app.repositories.base import Repository


class TicketRepository(Repository[Ticket]):
    def __init__(self, tickets: Iterable[Ticket]):
        self._tickets = list(tickets)

    def get(self, object_id: int) -> Ticket | None:
        return next((ticket for ticket in self._tickets if ticket.id == object_id), None)

    def list_for_board(self, board_id: int, include_archived: bool = False) -> list[Ticket]:
        tickets = [
            ticket
            for ticket in self._tickets
            if ticket.board_id == board_id and (include_archived or not ticket.archived)
        ]
        return sorted(tickets, key=lambda ticket: (ticket.position, ticket.id))
