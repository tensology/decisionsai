from app.repositories.tickets import TicketRepository


class TicketService:
    def __init__(self, repository: TicketRepository):
        self.repository = repository

    def list_board_tickets(self, board_id, include_archived=False):
        board_id_int = int(board_id)
        return self.repository.list_for_board(board_id_int, include_archived=include_archived)
