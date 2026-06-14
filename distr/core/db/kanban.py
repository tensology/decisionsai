"""
Ticket Board database models.
Supports local (database) boards with full CRUD, plus read-only Trello/Jira board viewing.
"""
from sqlalchemy import Column, Index, Integer, String, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from . import Base
from .time import utc_now_naive
import json


class KanbanBoard(Base):
    __tablename__ = 'kanban_boards'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text)
    source = Column(String, default='database')  # 'database', 'trello', 'jira'
    external_board_id = Column(String, nullable=True)  # Trello/Jira board ID
    external_url = Column(String, nullable=True)  # Link to external board

    default_workflow_id = Column(Integer, nullable=True)  # FK to auto_workflows.id (default workflow)
    default_project_id = Column(Integer, nullable=True)  # default project for new tickets
    default_snippet_id = Column(Integer, nullable=True)  # default snippet for new tickets
    default_action_id = Column(Integer, nullable=True)  # default action for new tickets
    send_to_cli = Column(Boolean, default=False)  # if True, agent sends tickets to pi coding agent instead of running a workflow
    archived = Column(Boolean, default=False)  # archived boards are hidden from the sidebar
    in_use = Column(Boolean, default=False)  # only one board can be in_use at a time (default board for agent)
    color = Column(String, nullable=True)  # board accent color (hex, e.g. '#f97316')
    position = Column(Integer, default=0)  # sidebar display order
    orchestrator_policy = Column(Text, nullable=True)  # JSON board overrides for routing/corrections

    created_date = Column(DateTime, default=utc_now_naive)
    modified_date = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    lanes = relationship("KanbanLane", back_populates="board", cascade="all, delete-orphan",
                         order_by="KanbanLane.position")

    def _policy_dict(self):
        try:
            data = json.loads(self.orchestrator_policy or "{}")
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _set_policy_value(self, key: str, value: str) -> None:
        data = self._policy_dict()
        data[key] = value or ""
        self.orchestrator_policy = json.dumps(data, sort_keys=True)

    @property
    def agent_source_lane(self):
        return self._policy_dict().get("agent_source_lane", "")

    @agent_source_lane.setter
    def agent_source_lane(self, value):
        self._set_policy_value("agent_source_lane", value)

    @property
    def agent_done_lane(self):
        return self._policy_dict().get("agent_done_lane", "")

    @agent_done_lane.setter
    def agent_done_lane(self, value):
        self._set_policy_value("agent_done_lane", value)


class KanbanLane(Base):
    __tablename__ = 'kanban_lanes'

    id = Column(Integer, primary_key=True)
    board_id = Column(Integer, ForeignKey('kanban_boards.id'), nullable=False)
    name = Column(String, nullable=False)
    position = Column(Integer, default=0)

    created_date = Column(DateTime, default=utc_now_naive)
    modified_date = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

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
    complexity = Column(String, default='medium')  # low, medium, high
    time_estimate = Column(String, nullable=True)  # Initial estimate (e.g. "2h", "1d")
    time_spent = Column(String, nullable=True)  # Actual duration spent (e.g. "45m", "3h")
    position = Column(Integer, default=0)

    # External source (for copied Trello/Jira tickets)
    external_source = Column(String, nullable=True)  # 'trello', 'jira'
    external_id = Column(String, nullable=True)
    external_url = Column(String, nullable=True)

    # Linking to other entities
    linked_workflow_id = Column(Integer, nullable=True)
    workflow_queue_position = Column(Integer, default=0)
    linked_project_id = Column(Integer, nullable=True)
    linked_snippet_id = Column(Integer, nullable=True)
    linked_action_id = Column(Integer, nullable=True)

    send_to_cli = Column(Boolean, default=False)  # if True, send directly to project CLI instead of workflow

    # WhatsApp source (if ticket was created from a WhatsApp message)
    whatsapp_message_id = Column(Integer, nullable=True)  # FK to whatsapp_messages.id
    whatsapp_message_wa_id = Column(String, nullable=True)  # WhatsApp's own message ID

    # Durable origin metadata for routing follow-ups back to the right surface.
    source_provider = Column(String, nullable=True)  # whatsapp, gmail, telegram, jira, trello, web, manual
    source_external_id = Column(String, nullable=True)
    source_thread_id = Column(String, nullable=True)
    source_contact = Column(String, nullable=True)
    source_url = Column(String, nullable=True)
    source_label = Column(String, nullable=True)

    # Workflow execution status — mirrors the latest linked AutoWorkflowRun status
    workflow_status = Column(String, nullable=True)  # None | running | completed | failed | cancelled | waiting

    # Chat that created or owns this ticket for UX (lane-move notices, etc.)
    source_chat_id = Column(Integer, ForeignKey('chats.id'), nullable=True)

    # Subagent hierarchy — parent ticket that spawned this one (if any)
    parent_ticket_id = Column(Integer, ForeignKey('kanban_tickets.id'), nullable=True)

    created_date = Column(DateTime, default=utc_now_naive)
    modified_date = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    lane = relationship("KanbanLane", back_populates="tickets")
    files = relationship("KanbanTicketFile", back_populates="ticket", cascade="all, delete-orphan")
    links = relationship("KanbanTicketLink", back_populates="ticket", cascade="all, delete-orphan")
    todos = relationship("KanbanTicketTodo", back_populates="ticket", cascade="all, delete-orphan",
                         order_by="KanbanTicketTodo.position")


# Indexes for high-frequency query patterns on the kanban board agent
Index('ix_kanban_tickets_lane_id', KanbanTicket.lane_id)
Index('ix_kanban_tickets_position', KanbanTicket.position)
Index('ix_kanban_lanes_board_id', KanbanLane.board_id)


class KanbanTicketFile(Base):
    __tablename__ = 'kanban_ticket_files'

    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey('kanban_tickets.id'), nullable=False)
    filename = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    description = Column(String)

    created_date = Column(DateTime, default=utc_now_naive)

    ticket = relationship("KanbanTicket", back_populates="files")


class KanbanTicketLink(Base):
    __tablename__ = 'kanban_ticket_links'

    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey('kanban_tickets.id'), nullable=False)
    title = Column(String, nullable=False)
    url = Column(String, nullable=False)

    created_date = Column(DateTime, default=utc_now_naive)

    ticket = relationship("KanbanTicket", back_populates="links")


class KanbanTicketTodo(Base):
    __tablename__ = 'kanban_ticket_todos'

    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey('kanban_tickets.id'), nullable=False)
    text = Column(String, nullable=False)
    done = Column(Boolean, default=False)
    position = Column(Integer, default=0)

    created_date = Column(DateTime, default=utc_now_naive)

    ticket = relationship("KanbanTicket", back_populates="todos")


class KanbanTicketAuditEntry(Base):
    __tablename__ = "kanban_ticket_audit_entries"

    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("kanban_tickets.id"), nullable=False)
    run_id = Column(Integer, ForeignKey("auto_workflow_runs.id"), nullable=True)
    step_id = Column(Integer, ForeignKey("auto_workflow_steps.id"), nullable=True)
    step_result_id = Column(Integer, ForeignKey("auto_workflow_step_results.id"), nullable=True)
    execution_lane = Column(String, nullable=False, default="cursor")  # cursor | cli
    status = Column(String, nullable=False, default="pending")
    final_verdict = Column(String, nullable=True)
    summary = Column(Text, nullable=True)
    details = Column(Text, nullable=True)
    created_date = Column(DateTime, default=utc_now_naive)


class ProjectExecutionSession(Base):
    __tablename__ = "project_execution_sessions"

    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("kanban_tickets.id"), nullable=True)
    project_id = Column(Integer, nullable=False)
    workflow_id = Column(Integer, ForeignKey("auto_workflows.id"), nullable=True)
    run_id = Column(Integer, ForeignKey("auto_workflow_runs.id"), nullable=True)
    step_id = Column(Integer, ForeignKey("auto_workflow_steps.id"), nullable=True)
    audit_id = Column(Integer, nullable=True)

    route_type = Column(String, nullable=False, default="project_cli")
    route_backend = Column(String, nullable=False, default="")
    selected_model = Column(String, nullable=True)
    selection_reason = Column(Text, nullable=True)
    complexity = Column(String, nullable=True)
    origin = Column(String, nullable=True)

    status = Column(String, nullable=False, default="queued")
    input_packet = Column(Text, nullable=True)
    output_packet = Column(Text, nullable=True)
    error = Column(Text, nullable=True)

    started_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)
    completed_at = Column(DateTime, nullable=True)

    events = relationship(
        "ProjectExecutionEvent",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ProjectExecutionEvent.created_at",
    )


class ProjectExecutionEvent(Base):
    __tablename__ = "project_execution_events"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("project_execution_sessions.id"), nullable=False)
    event_type = Column(String, nullable=False, default="event")
    status = Column(String, nullable=True)
    message = Column(Text, nullable=True)
    payload = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)

    session = relationship("ProjectExecutionSession", back_populates="events")


Index("ix_project_execution_sessions_ticket_id", ProjectExecutionSession.ticket_id)
Index("ix_project_execution_sessions_project_id", ProjectExecutionSession.project_id)
Index("ix_project_execution_sessions_status", ProjectExecutionSession.status)
Index("ix_project_execution_events_session_id", ProjectExecutionEvent.session_id)
