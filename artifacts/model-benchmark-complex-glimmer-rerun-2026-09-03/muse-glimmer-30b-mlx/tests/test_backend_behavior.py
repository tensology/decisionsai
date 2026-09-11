import unittest
from datetime import datetime, timezone

from app.models.ticket import Ticket
from app.repositories.tickets import TicketRepository
from app.services.tickets import TicketService
from app.web.views import board_tickets_view


def ticket(ticket_id, board_id, position, *, archived=False):
    return Ticket(ticket_id, datetime.now(timezone.utc), board_id, f"T{ticket_id}", position, archived)


class BackendBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.repository = TicketRepository([
            ticket(4, 7, 20, archived=True),
            ticket(2, 7, 10),
            ticket(3, 8, 1),
            ticket(1, 7, 10, archived=True),
            ticket(5, 7, 5),
        ])
        self.service = TicketService(self.repository)

    def test_repository_hides_archived_by_default(self):
        self.assertEqual([item.id for item in self.repository.list_for_board(7)], [5, 2])

    def test_repository_can_include_archived(self):
        self.assertEqual([item.id for item in self.repository.list_for_board(7, include_archived=True)], [5, 1, 2, 4])

    def test_repository_does_not_leak_other_boards(self):
        self.assertNotIn(3, [item.id for item in self.repository.list_for_board(7, include_archived=True)])

    def test_repository_orders_by_position_then_id(self):
        self.assertEqual([item.id for item in self.repository.list_for_board(7, include_archived=True)], [5, 1, 2, 4])

    def test_service_preserves_default(self):
        self.assertEqual([item.id for item in self.service.list_board_tickets(7)], [5, 2])

    def test_service_forwards_include_archived(self):
        self.assertEqual(len(self.service.list_board_tickets(7, include_archived=True)), 4)

    def test_service_normalizes_numeric_board_id(self):
        self.assertEqual([item.id for item in self.service.list_board_tickets("7")], [5, 2])

    def test_view_default_contract(self):
        payload = board_tickets_view(self.service, "7", {})
        self.assertEqual(payload["meta"], {"count": 2, "include_archived": False})

    def test_view_true_query_spellings(self):
        for value in ("1", "true", "TRUE", " yes ", "on", True):
            with self.subTest(value=value):
                payload = board_tickets_view(self.service, 7, {"include_archived": value})
                self.assertEqual(payload["meta"], {"count": 4, "include_archived": True})

    def test_view_false_query_spellings(self):
        for value in ("0", "false", "no", "off", "", False, None):
            with self.subTest(value=value):
                self.assertEqual(board_tickets_view(self.service, 7, {"include_archived": value})["meta"]["count"], 2)

    def test_view_item_shape_is_stable(self):
        self.assertEqual(set(board_tickets_view(self.service, 7, {})["items"][0]), {"id", "title", "archived"})

    def test_invalid_board_id_is_rejected(self):
        with self.assertRaises((TypeError, ValueError)):
            board_tickets_view(self.service, "not-an-id", {})


if __name__ == "__main__":
    unittest.main()
