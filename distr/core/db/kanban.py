"""
Kanban board database models.
Supports local (database) boards with full CRUD, plus read-only Trello/Jira board viewing.
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from . import Base
from datetime import datetime


class KanbanBoard(Base):
    __tablename__ = 'kanban_boards'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text)
    source = Column(String, default='database')  # 'database', 'trello', 'jira'
    external_board_id = Column(String, nullable=True)  # Trello/Jira board ID
    external_url = Column(String, nullable=True)  # Link to external board

    # Agent check-in settings
    agent_enabled = Column(Boolean, default=False)
    agent_frequency = Column(String, default='daily')  # 'daily', 'weekly', 'monthly'
    agent_time = Column(String, default='09:00')  # HH:MM
    agent_days = Column(Text, default='[]')  # JSON list of day indices (0=Sun..6=Sat) for weekly
    agent_monthly_day = Column(Integer, default=1)  # Day of month for monthly
    agent_orchestrator_provider = Column(String, default='')  # defaults to chat conversational setting
    agent_orchestrator_model = Column(String, default='')
    agent_coder_provider = Column(String, default='')  # defaults to chat coding setting
    agent_coder_model = Column(String, default='')
    agent_sub_provider = Column(String, default='')  # sub-agent provider
    agent_sub_model = Column(String, default='')
    agent_source_lane = Column(String, default='')  # lane name the agent picks tickets from (e.g. "Current")
    agent_done_lane = Column(String, default='')  # lane name to move tickets into when done (e.g. "QA / Assess" or "Done")
    default_workflow_id = Column(Integer, nullable=True)  # FK to auto_workflows.id (default step-runner workflow)
    default_project_id = Column(Integer, nullable=True)  # default project for new tickets
    default_snippet_id = Column(Integer, nullable=True)  # default snippet for new tickets
    default_action_id = Column(Integer, nullable=True)  # default action for new tickets
    send_to_cli = Column(Boolean, default=False)  # if True, agent sends tickets to Kiro CLI instead of running a workflow
    archived = Column(Boolean, default=False)  # archived boards are hidden from the sidebar
    in_use = Column(Boolean, default=False)  # only one board can be in_use at a time (default board for agent)
    color = Column(String, nullable=True)  # board accent color (hex, e.g. '#f97316')
    position = Column(Integer, default=0)  # sidebar display order

    created_date = Column(DateTime, default=datetime.utcnow)
    modified_date = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    lanes = relationship("KanbanLane", back_populates="board", cascade="all, delete-orphan",
                         order_by="KanbanLane.position")


class KanbanLane(Base):
    __tablename__ = 'kanban_lanes'

    id = Column(Integer, primary_key=True)
    board_id = Column(Integer, ForeignKey('kanban_boards.id'), nullable=False)
    name = Column(String, nullable=False)
    position = Column(Integer, default=0)

    created_date = Column(DateTime, default=datetime.utcnow)
    modified_date = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    board = relationship("KanbanBoard", back_populates="lanes")
    tickets = relationship("KanbanTicket", back_populates="lane", cascade="all, delete-orphan",
                           order_by="KanbanTicket.position")


class KanbanTicket(Base):
    __tablename__ = 'kanban_tickets'

    id = Column(Integer, primary_key=True)
    lane_id = Column(Integer, ForeignKey('kanban_lanes.id'), nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text)
    priority = Column(String, default='medium')  # low, medium, high, critical
    position = Column(Integer, default=0)

    # External source (for copied Trello/Jira tickets)
    external_source = Column(String, nullable=True)  # 'trello', 'jira'
    external_id = Column(String, nullable=True)
    external_url = Column(String, nullable=True)

    # Linking to other entities
    linked_workflow_id = Column(Integer, nullable=True)
    linked_project_id = Column(Integer, nullable=True)
    linked_snippet_id = Column(Integer, nullable=True)
    linked_action_id = Column(Integer, nullable=True)

    send_to_cli = Column(Boolean, default=False)  # if True, send directly to project CLI instead of workflow

    created_date = Column(DateTime, default=datetime.utcnow)
    modified_date = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    lane = relationship("KanbanLane", back_populates="tickets")
    files = relationship("KanbanTicketFile", back_populates="ticket", cascade="all, delete-orphan")
    links = relationship("KanbanTicketLink", back_populates="ticket", cascade="all, delete-orphan")
    todos = relationship("KanbanTicketTodo", back_populates="ticket", cascade="all, delete-orphan",
                         order_by="KanbanTicketTodo.position")


class KanbanTicketFile(Base):
    __tablename__ = 'kanban_ticket_files'

    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey('kanban_tickets.id'), nullable=False)
    filename = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    description = Column(String)

    created_date = Column(DateTime, default=datetime.utcnow)

    ticket = relationship("KanbanTicket", back_populates="files")


class KanbanTicketLink(Base):
    __tablename__ = 'kanban_ticket_links'

    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey('kanban_tickets.id'), nullable=False)
    title = Column(String, nullable=False)
    url = Column(String, nullable=False)

    created_date = Column(DateTime, default=datetime.utcnow)

    ticket = relationship("KanbanTicket", back_populates="links")


class KanbanTicketTodo(Base):
    __tablename__ = 'kanban_ticket_todos'

    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey('kanban_tickets.id'), nullable=False)
    text = Column(String, nullable=False)
    done = Column(Boolean, default=False)
    position = Column(Integer, default=0)

    created_date = Column(DateTime, default=datetime.utcnow)

    ticket = relationship("KanbanTicket", back_populates="todos")
