"""User-created schedule blocks for the automations calendar."""

from sqlalchemy import Boolean, Column, DateTime, Integer, String

from . import Base
from .time import utc_now_naive


class ScheduleBlock(Base):
    __tablename__ = "schedule_blocks"

    id = Column(Integer, primary_key=True)
    title = Column(String, default="")
    start_at = Column(DateTime, nullable=False)
    end_at = Column(DateTime, nullable=False)
    board_id = Column(Integer, nullable=True)
    board_provider = Column(String, default="local")
    external_board_id = Column(String, nullable=True)
    ticket_id = Column(Integer, nullable=True)
    external_ticket_key = Column(String, nullable=True)
    project_id = Column(Integer, nullable=True)
    is_timer_running = Column(Boolean, default=False)
    is_timer_entry = Column(Boolean, default=False)
    created_date = Column(DateTime, default=utc_now_naive)
    modified_date = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)
