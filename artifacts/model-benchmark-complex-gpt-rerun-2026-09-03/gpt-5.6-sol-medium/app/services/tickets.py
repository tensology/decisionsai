from app.repositories.tickets import TicketRepository


class TicketService:
    def __init__(self, repository: TicketRepository):
        self.repository = repository

    def list_board_tickets(self, board_id: int, include_archived: bool = False):
        return self.repository.list_for_board(
            int(board_id), include_archived=include_archived
        )
