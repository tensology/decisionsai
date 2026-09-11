from dataclasses import dataclass
from app.models.base import Entity


@dataclass(frozen=True)
class Ticket(Entity):
    board_id: int
    title: str
    position: int
    archived: bool = False
