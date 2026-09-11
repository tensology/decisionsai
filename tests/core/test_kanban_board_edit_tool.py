from distr.core.agent.tools.integrations.kanban_ticket import KanbanTicketTool
from distr.core.db import get_session
from distr.core.db.kanban import KanbanBoard


def test_orchestrator_can_edit_a_board_by_explicit_id():
    with get_session() as db:
        board = KanbanBoard(name="Original board", description="Old", source="database")
        db.add(board)
        db.commit()
        board_id = int(board.id)

    result = KanbanTicketTool()._run(
        action="update_board",
        board_id=board_id,
        title="Renamed board",
        description="Updated by the orchestrator",
    )

    assert "Updated board" in result
    with get_session() as db:
        board = db.get(KanbanBoard, board_id)
        assert board.name == "Renamed board"
        assert board.description == "Updated by the orchestrator"
