from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from . import Base
from datetime import datetime

class Project(Base):
    __tablename__ = 'projects'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(String)
    folder_location = Column(String)  # Path to the project folder
    additional_trigger_words = Column(Text)  # Store as JSON string
    startup_instructions = Column(Text)  # Startup commands, one per line, each runs in a new terminal
    in_use = Column(Boolean, default=False)  # Only one project can be in use at a time
    provider = Column(String, nullable=True)  # 'trello' or 'jira'
    board_id = Column(String, nullable=True)  # Board ID from provider
    board_name = Column(String, nullable=True)  # Board name for display
    kanban_board_id = Column(Integer, nullable=True)  # FK to kanban_boards.id for local database boards
    coding_backend = Column(String, nullable=False, default="pi")  # Project coding CLI backend
    coding_backend_model = Column(String, nullable=True)  # Optional per-project model/alias for the selected CLI backend

    created_date = Column(DateTime, default=datetime.utcnow)
    modified_date = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    context_items = relationship("ProjectContextItem", back_populates="project", cascade="all, delete-orphan")
    files = relationship("ProjectFile", back_populates="project", cascade="all, delete-orphan")
    board_columns = relationship("BoardColumn", back_populates="project", cascade="all, delete-orphan")


class ProjectContextItem(Base):
    __tablename__ = 'project_context_items'

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False)
    title = Column(String, nullable=False)
    content = Column(Text)  # The text blob content
    
    created_date = Column(DateTime, default=datetime.utcnow)
    modified_date = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    project = relationship("Project", back_populates="context_items")


class ProjectFile(Base):
    __tablename__ = 'project_files'

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False)
    filename = Column(String, nullable=False)
    description = Column(String)
    file_path = Column(String, nullable=False)  # Full path to the file
    
    created_date = Column(DateTime, default=datetime.utcnow)
    modified_date = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    project = relationship("Project", back_populates="files")


class BoardColumn(Base):
    __tablename__ = 'board_columns'

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False)
    name = Column(String, nullable=False)  # e.g., "Backlog", "In Progress"
    position = Column(Integer, default=0)  # For ordering columns
    trello_list_id = Column(String, nullable=True)  # If synced with Trello

    created_date = Column(DateTime, default=datetime.utcnow)
    modified_date = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    project = relationship("Project", back_populates="board_columns")
    tickets = relationship("BoardTicket", back_populates="column", cascade="all, delete-orphan")


class BoardTicket(Base):
    __tablename__ = 'board_tickets'

    id = Column(Integer, primary_key=True)
    column_id = Column(Integer, ForeignKey('board_columns.id'), nullable=False)

    title = Column(String, nullable=False)
    description = Column(Text)
    assignee = Column(String)  # Free text or Trello member name
    due_date = Column(DateTime, nullable=True)
    priority = Column(String)  # 'low', 'medium', 'high'
    tags = Column(Text)  # JSON string of labels/tags
    time_estimate = Column(String, nullable=True)  # Time estimate (e.g., "2h", "1d", "3h 30m")

    position = Column(Integer, default=0)  # For ordering within column
    trello_card_id = Column(String, nullable=True)  # If synced with Trello

    created_date = Column(DateTime, default=datetime.utcnow)
    modified_date = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    column = relationship("BoardColumn", back_populates="tickets")
