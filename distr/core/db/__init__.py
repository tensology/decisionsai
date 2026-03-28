from sqlalchemy import create_engine, Column, Integer, String, Float, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, backref
from sqlalchemy.dialects.sqlite import CHAR
from uuid import uuid4
import hashlib
import os
import logging
from distr.core.paths import DB_DIR
from datetime import datetime
import atexit

Base = declarative_base()

class Settings(Base):
    __tablename__ = 'settings'

    id = Column(Integer, primary_key=True)

    last_chat_id = Column(Integer, default=1)
    # Chat ID currently loaded in the voice agent (desktop only). Web uses this to show "In agent" state.
    agent_current_chat_id = Column(Integer, default=None, nullable=True)

    # General Settings
    language = Column(String, default='English')
    theme = Column(String)
    load_splash_sound = Column(Boolean, default=True)
    show_about = Column(Boolean, default=True)
    welcome_greet_me = Column(Boolean, default=True)
    always_confirm_file_operations = Column(Boolean, default=True)  # Always show confirmation dialog for file operations
    startup_listening_state = Column(String, default='remember')  # values: 'remember', 'stop', 'start'

    restore_position = Column(Boolean, default=True)
    oracle_position = Column(String, default='Middle Right')

    selected_oracle = Column(String, default='0.gif')

    sphere_size = Column(Integer, default=120)

    consent_given = Column(Boolean, default=False)
    
    # EULA Acceptance
    accepted_eula = Column(Boolean, default=False)

    # Audio Settings
    input_device = Column(String, default=None)  # None means user hasn't selected yet
    output_device = Column(String, default=None)  # None means user hasn't selected yet
    play_output = Column(String, default='System Default')
    play_translation = Column(String, default='System Default')
    translation_device = Column(String, default='System Default')  # Alias for play_translation
    lock_sound = Column(Boolean, default=False)
    locked_input = Column(String, default='System Default')
    locked_output = Column(String, default='System Default')
    locked_input_list = Column(Text)  # JSON list of all detected input devices (no System Default)
    locked_output_list = Column(Text)  # JSON list of all detected output devices (no System Default)
    speech_volume = Column(Integer, default=50)
    playback_speed = Column(Float, default=1.0)
    volume = Column(Integer, default=50)
    vad_threshold = Column(Integer, default=50)

    # AI Settings
    ai_model = Column(String)
    temperature = Column(Float, default=0.7)
    speechmatics_key = Column(String, default='')
    openai_key = Column(String, default='')
    anthropic_key = Column(String, default='')
    aws_polly_key = Column(String, default='')
    ollama_url = Column(String, default='http://localhost:11434/')
    tts_provider = Column(String, default='Kokoro (Offline)')
    tts_voice = Column(String, default='')
    agent_provider = Column(String, default='Ollama')
    agent_model = Column(String, default='qwen3:8b')
    code_provider = Column(String, default='Ollama')
    code_model = Column(String, default='qwen2.5-coder:7b')
    input_speech = Column(String, default='Vosk')
    transcription_model = Column(String, default='Whisper.cpp (Local & Offline)')
    
    # Multiple LLM Settings (Conversational, Coding, Vision, Image)
    # llm_provider/model: Standardized names used by ChatManager for hot-reload
    llm_provider = Column(String, default='Ollama')
    llm_model = Column(String, default='qwen3:8b')
    conversational_llm_provider = Column(String, default='Ollama')
    conversational_llm_model = Column(String, default='qwen3:8b')
    coding_llm_provider = Column(String, default='Ollama')
    coding_llm_model = Column(String, default='qwen2.5-coder:7b')
    vision_llm_provider = Column(String, default='Ollama')
    vision_llm_model = Column(String, default='qwen3-vl:2b')
    image_llm_provider = Column(String, default='Ollama')
    image_llm_model = Column(String, default='x/flux2-klein:latest')

    # Advanced Settings
    excluded_files = Column(String, default='')
    indexed_folders = Column(String, default='[]')  # JSON string of folders
    connected_accounts = Column(String, default='[]')  # JSON string of connected accounts

    # Provider States
    assemblyai_enabled = Column(Boolean, default=False)
    speechmatics_enabled = Column(Boolean, default=False)
    openai_enabled = Column(Boolean, default=False)
    replicate_enabled = Column(Boolean, default=False)
    anthropic_enabled = Column(Boolean, default=False)
    elevenlabs_enabled = Column(Boolean, default=False)
    ollama_enabled = Column(Boolean, default=True)  # Default to True for Ollama
    openrouter_enabled = Column(Boolean, default=False)
    groq_enabled = Column(Boolean, default=False)
    kilo_enabled = Column(Boolean, default=False)

    # Provider Keys/URLs
    assemblyai_key = Column(String, default='')
    speechmatics_key = Column(String, default='')
    openai_key = Column(String, default='')
    anthropic_key = Column(String, default='')
    ollama_url = Column(String, default='http://localhost:11434/')
    elevenlabs_key = Column(String, default='')
    openrouter_key = Column(String, default='')
    groq_key = Column(String, default='')
    kilo_key = Column(String, default='')

    last_listening_state = Column(Boolean, default=True)
    hands_free_mode = Column(Boolean, default=True)

    voice_provider = Column(String, default='kokoro')
    kokoro_voice = Column(String, default='af_heart')
    elevenlabs_voice = Column(String, default='')
    # ElevenLabs API voice_settings (0-1 floats; only used when voice_provider is ElevenLabs)
    elevenlabs_stability = Column(Float, default=0.5)
    elevenlabs_similarity_boost = Column(Float, default=0.6)
    elevenlabs_style = Column(Float, default=0.25)
    elevenlabs_use_speaker_boost = Column(Boolean, default=True)
    openai_voice = Column(String, default='alloy')
    qwen3_voice = Column(String, default='aiden')
    replicate_api_token = Column(String, default='')

    # Rube Settings
    rube_enabled = Column(Boolean, default=False)
    rube_token = Column(String, default='')
    
    # Note: Jira and Trello accounts are now stored in connected_accounts JSON field
    # Individual columns removed to support multiple accounts
    
    # Initiative Settings
    initiative_level = Column(String, default='assist')
    initiative_allow_telegram = Column(Boolean, default=False)
    initiative_allow_routine_tasks = Column(Boolean, default=False)
    initiative_ask_external_comms = Column(Boolean, default=True)
    initiative_ask_file_changes = Column(Boolean, default=True)
    initiative_ask_sensitive = Column(Boolean, default=True)
    
    # Chat voice/speaker toggle
    chat_voice_enabled = Column(Boolean, default=True)

    # Kanban Agent Settings (global, migrated from per-board)
    kanban_agent_enabled = Column(Boolean, default=False)
    kanban_agent_frequency = Column(String, default='daily')
    kanban_agent_time = Column(String, default='09:00')
    kanban_agent_hours = Column(String, default='[]')
    kanban_agent_days = Column(String, default='[]')
    kanban_agent_monthly_day = Column(Integer, default=1)
    kanban_agent_source_lane = Column(String, default='')
    kanban_agent_done_lane = Column(String, default='')
    kanban_agent_orchestrator_provider = Column(String, default='')
    kanban_agent_orchestrator_model = Column(String, default='')
    kanban_agent_coder_provider = Column(String, default='')
    kanban_agent_coder_model = Column(String, default='')
    kanban_agent_sub_provider = Column(String, default='')
    kanban_agent_sub_model = Column(String, default='')
    kanban_cli_tool = Column(String, default='')
    kanban_cli_auth = Column(String, default='')
    _kanban_migration_done = Column(Boolean, default=False)


class Chat(Base):
    __tablename__ = 'chats'

    id = Column(Integer, primary_key=True)
    parent_id = Column(Integer, ForeignKey('chats.id'), nullable=True)
    title = Column(String)
    input = Column(Text)
    response = Column(Text)
    params = Column(Text)  # Store as JSON string
    additional_context = Column(Text)
    image = Column(String)  # Store image path
    code = Column(Text)
    is_archived = Column(Boolean, default=False)
    is_new = Column(Boolean, default=False)  # Flag to indicate if chat is new and needs title enhancement
    is_hidden = Column(Boolean, default=False)  # Flag to hide message from UI but keep in database/memory
    model_name = Column(String, nullable=True)  # Store the model name used for this chat
    provider = Column(String, nullable=True)  # Store the provider used for this chat (e.g., 'Ollama', 'OpenAI')
    voice_provider = Column(String, nullable=True)  # Store the TTS provider (e.g., 'Kokoro', 'OpenAI', 'ElevenLabs')
    voice_model = Column(String, nullable=True)  # Store the voice/speaker used (e.g., 'af_sky', 'alloy')
    created_date = Column(DateTime, default=datetime.utcnow)
    modified_date = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    children = relationship("Chat", 
                          backref=backref("parent", remote_side=[id]),
                          cascade="all, delete-orphan")

class ScreenPosition(Base):
    __tablename__ = 'screen_positions'
    
    screens_id = Column(CHAR(32), primary_key=True)
    screen_name = Column(String)
    pos_x = Column(Float)
    pos_y = Column(Float)


class Action(Base):
    __tablename__ = 'actions'

    id = Column(Integer, primary_key=True)

    title = Column(String) #also a trigger word
    description = Column(String)

    additional_trigger_words = Column(Text)  # Store as JSON string

    is_instruction = Column(Boolean, default=False)  # True = instruction to LLM, False = recorded mouse/keyboard action
    instruction_text = Column(Text)  # Store instruction text for LLM actions
    action = Column(Text)  # Store as JSON string
    recording_filename = Column(String)  # Filename of the recording JSON file
    last_run_date = Column(DateTime)  # When the action was last played/run (for list ordering)

    created_date = Column(DateTime, default=datetime.utcnow)
    modified_date = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Snippet(Base):
    __tablename__ = 'snippets'

    id = Column(Integer, primary_key=True)

    title = Column(String) #also a trigger word
    description = Column(String)

    additional_trigger_words = Column(Text)  # Store as JSON string
    snippet = Column(Text)  # Store as JSON string

    created_date = Column(DateTime, default=datetime.utcnow)
    modified_date = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CustomVoice(Base):
    """User-created custom voices for TTS providers that support voice cloning."""
    __tablename__ = 'custom_voices'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)                # Display name
    provider = Column(String, nullable=False)             # 'qwen3' or 'elevenlabs'
    system_prompt = Column(Text, default='')              # Audio transcription (ref_text for Qwen3 cloning)
    personality = Column(Text, default='')                # Agent personality/persona appended to LLM system prompt
    audio_dir = Column(String, default='')                # Path to uploaded audio files directory
    # Provider-specific ID returned after cloning (e.g. ElevenLabs voice_id)
    provider_voice_id = Column(String, default='')
    status = Column(String, default='pending')            # pending | processing | ready | failed
    error_message = Column(Text, default='')              # Error details if status == failed
    gender = Column(String, default='female')             # 'female' or 'male' — picks Kokoro base voice for cloning
    created_date = Column(DateTime, default=datetime.utcnow)
    modified_date = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class WorkflowProject(Base):
    __tablename__ = 'workflow_projects'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text)
    created_date = Column(DateTime, default=datetime.utcnow)
    modified_date = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationship to workflows (job cards)
    workflows = relationship("Workflow", back_populates="project")


class Workflow(Base):
    __tablename__ = 'workflows'

    id = Column(Integer, primary_key=True)

    title = Column(String)  # also a trigger word
    description = Column(String)
    desired_outcome = Column(Text)  # What the workflow is intended to achieve
    
    # Template/Job Card support
    workflow_type = Column(String, default='template')  # 'template' or 'job_card'
    template_id = Column(Integer, ForeignKey('workflows.id'), nullable=True)  # For job cards: link to template
    version = Column(String, default='1.0.0')  # Version for templates
    is_template = Column(Boolean, default=True)  # True for templates, False for job cards
    
    # Project relationship (for job cards)
    project_id = Column(Integer, ForeignKey('workflow_projects.id'), nullable=True)
    
    # Job Card specific fields
    ticket_id = Column(String)  # External ticket/spec identifier
    ticket_text = Column(Text)  # Ticket/spec content
    repo_workspace = Column(String)  # Repository/workspace path
    constraints = Column(Text)  # JSON string: time, environment, forbidden actions
    definition_of_done = Column(Text)  # Done criteria
    qa_policy = Column(Text)  # JSON string: approval gates, QA requirements
    overrides = Column(Text)  # JSON string: node/edge overrides from template

    additional_trigger_words = Column(Text)  # Store as JSON string
    workflow_instructions = Column(Text)  # Store as JSON string - list of instruction dicts

    created_date = Column(DateTime, default=datetime.utcnow)
    modified_date = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Self-referential relationship for template -> job cards
    job_cards = relationship("Workflow", backref=backref("template", remote_side=[id]))
    # Project relationship
    project = relationship("WorkflowProject", back_populates="workflows")


class TrelloTicket(Base):
    __tablename__ = 'trello_tickets'

    id = Column(Integer, primary_key=True)
    trello_card_id = Column(String, unique=True, nullable=False)
    title = Column(String)
    description = Column(Text)
    
    # Local allocations
    chat_id = Column(Integer, ForeignKey('chats.id'), nullable=True)
    workflow_id = Column(Integer, ForeignKey('workflows.id'), nullable=True)
    
    # Cached Trello Data
    members = Column(Text)  # JSON string of member names/IDs
    attachments = Column(Text)  # JSON string of attachment URLs/names
    status = Column(String)  # e.g., list name or card status
    
    created_date = Column(DateTime, default=datetime.utcnow)
    modified_date = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    chat = relationship("Chat")
    workflow = relationship("Workflow")


class TelegramGroupMessage(Base):
    """Store Telegram group/channel messages that are received but not acted on."""
    __tablename__ = 'telegram_group_messages'

    id = Column(Integer, primary_key=True)
    
    # Telegram message identifiers
    telegram_message_id = Column(Integer, nullable=False)  # Telegram's message ID
    chat_id = Column(String, nullable=False)  # Group/channel chat ID (negative number)
    chat_type = Column(String)  # 'group', 'supergroup', 'channel'
    chat_title = Column(String)  # Group/channel title
    chat_username = Column(String)  # Group/channel username
    chat_description = Column(Text)  # Group/channel description
    
    # Message content
    text = Column(Text)  # Message text
    caption = Column(Text)  # Caption for media messages
    media_type = Column(String)  # 'voice', 'audio', 'photo', 'video', etc.
    media_data = Column(Text)  # JSON string of media information
    
    # Sender information (stored as JSON)
    sender_data = Column(Text)  # JSON string: {id, first_name, last_name, username, is_bot, language_code}
    
    # Message metadata
    telegram_date = Column(Integer)  # Unix timestamp from Telegram
    edit_date = Column(Integer)  # Unix timestamp if edited
    reply_to_message_id = Column(Integer, nullable=True)
    forward_from = Column(Text)  # JSON string
    forward_from_chat = Column(Text)  # JSON string
    entities = Column(Text)  # JSON string of entities (mentions, hashtags, etc.)
    
    # Full raw data for later processing
    raw_data = Column(Text)  # JSON string of complete raw message data
    
    # Processing status
    processed = Column(Boolean, default=False)  # Whether this message has been processed
    processed_date = Column(DateTime, nullable=True)  # When it was processed
    
    # Timestamps
    created_date = Column(DateTime, default=datetime.utcnow)  # When stored in database
    modified_date = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Index for faster lookups
    __table_args__ = (
        {'sqlite_autoincrement': True},
    )


# Create the database file if it doesn't exist
if not os.path.exists(DB_DIR):
    os.makedirs(DB_DIR)

db_path = os.path.join(DB_DIR, 'settings.db')
# Add connection pooling limits to prevent connection leaks
# pool_size=5: max 5 connections in pool
# max_overflow=10: allow up to 10 additional connections beyond pool_size
# pool_pre_ping=True: verify connections before using them
# pool_recycle=3600: recycle connections after 1 hour
# echo=False: set to True for SQL query logging (useful for debugging)
engine = create_engine(
    f'sqlite:///{db_path}',
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={'check_same_thread': False},  # SQLite allows multi-threaded access
    echo=False  # Set to True to log all SQL queries
)

# Add event listeners to monitor connection usage
from sqlalchemy import event

@event.listens_for(engine, "connect")
def on_connect(dbapi_conn, connection_record):
    """Log when a new connection is created"""
    logger = logging.getLogger(__name__)
    pool = engine.pool
    logger.debug(f"Database connection created. Pool size: {pool.size()}, checked out: {pool.checkedout()}")

@event.listens_for(engine, "checkout")
def on_checkout(dbapi_conn, connection_record, connection_proxy):
    """Log when a connection is checked out from the pool"""
    logger = logging.getLogger(__name__)
    pool = engine.pool
    logger.debug(f"Database connection checked out. Pool size: {pool.size()}, checked out: {pool.checkedout()}")

@event.listens_for(engine, "checkin")
def on_checkin(dbapi_conn, connection_record):
    """Log when a connection is returned to the pool"""
    logger = logging.getLogger(__name__)
    pool = engine.pool
    logger.debug(f"Database connection checked in. Pool size: {pool.size()}, checked out: {pool.checkedout()}")

# Add missing kanban columns to existing settings table before create_all
try:
    with engine.connect() as _conn:
        _result = _conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='settings'"))
        if _result.fetchone():
            _existing = {row[1] for row in _conn.execute(text("PRAGMA table_info(settings)"))}
            for _col, _def in [
                ("kanban_agent_enabled", "BOOLEAN DEFAULT 0"),
                ("kanban_agent_frequency", "VARCHAR DEFAULT 'daily'"),
                ("kanban_agent_time", "VARCHAR DEFAULT '09:00'"),
                ("kanban_agent_hours", "VARCHAR DEFAULT '[]'"),
                ("kanban_agent_days", "VARCHAR DEFAULT '[]'"),
                ("kanban_agent_monthly_day", "INTEGER DEFAULT 1"),
                ("kanban_agent_source_lane", "VARCHAR DEFAULT ''"),
                ("kanban_agent_done_lane", "VARCHAR DEFAULT ''"),
                ("kanban_agent_orchestrator_provider", "VARCHAR DEFAULT ''"),
                ("kanban_agent_orchestrator_model", "VARCHAR DEFAULT ''"),
                ("kanban_agent_coder_provider", "VARCHAR DEFAULT ''"),
                ("kanban_agent_coder_model", "VARCHAR DEFAULT ''"),
                ("kanban_agent_sub_provider", "VARCHAR DEFAULT ''"),
                ("kanban_agent_sub_model", "VARCHAR DEFAULT ''"),
                ("kanban_cli_tool", "VARCHAR DEFAULT ''"),
                ("kanban_cli_auth", "VARCHAR DEFAULT ''"),
                ("_kanban_migration_done", "BOOLEAN DEFAULT 0"),
            ]:
                if _col not in _existing:
                    try:
                        _conn.execute(text(f"ALTER TABLE settings ADD COLUMN {_col} {_def}"))
                        _conn.commit()
                    except Exception:
                        pass
except Exception:
    pass

# Create the table
Base.metadata.create_all(engine)

# Create a session factory
Session = sessionmaker(bind=engine)

class SessionContext:
    """Context manager for database sessions to ensure proper cleanup.
    Also supports direct usage for backward compatibility."""
    def __init__(self):
        self.session = Session()
        self._in_context = False
    
    def __enter__(self):
        self._in_context = True
        return self.session
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            try:
                if exc_type:
                    self.session.rollback()
                else:
                    self.session.commit()
            except Exception as e:
                logging.getLogger(__name__).warning(f"Error during session cleanup: {e}")
            finally:
                self.session.close()
                self.session = None
        self._in_context = False
        return False  # Don't suppress exceptions
    
    def __getattr__(self, name):
        """Delegate attribute access to the underlying session for backward compatibility"""
        if self.session is None:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        return getattr(self.session, name)
    
    def close(self):
        """Close the session (for backward compatibility)"""
        if self.session:
            self.session.close()
            self.session = None

def get_session():
    """Get a database session. 
    Can be used as context manager: with get_session() as session:
    Or directly for backward compatibility: session = get_session() (remember to call session.close())"""
    return SessionContext()

def init_db():
    """Initialize the database by creating all tables and adding default settings"""
    # Import project models to ensure they're registered with Base
    from distr.core.db import projects as _projects  # noqa: F401
    from distr.core.db import step_runner as _step_runner  # noqa: F401
    from distr.core.db import workflow as _workflow  # noqa: F401
    from distr.core.db import kanban as _kanban  # noqa: F401

    # Run migrations that need to happen BEFORE create_all (like renaming old tables)
    from distr.core.db.migrations import run_pre_create_migrations
    run_pre_create_migrations()

    Base.metadata.create_all(engine)

    # Run all database migrations
    from distr.core.db.migrations import run_migrations
    run_migrations()
    
    with Session() as session:
        if not session.query(Settings).first():
            # Detect system RAM and pick appropriate models directly
            try:
                from distr.core.system_resources import recommend_ollama_defaults, get_total_ram_gb
                _rec = recommend_ollama_defaults(get_total_ram_gb())
            except Exception:
                _rec = {"conversational": "qwen3:0.6b", "coding": "qwen2.5-coder:0.5b", "vision": "qwen3-vl:2b"}

            # Also consume the setup.py JSON if it exists (may have more accurate values)
            _model_defaults_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'installer', '.model_defaults.json')
            try:
                import json as _json
                if os.path.exists(_model_defaults_path):
                    with open(_model_defaults_path, 'r') as _f:
                        _rec.update(_json.load(_f))
                    os.remove(_model_defaults_path)
            except Exception:
                pass

            default_settings = Settings(
                language='English',
                oracle_position='Middle Right',
                selected_oracle='0.gif',
                startup_listening_state='remember',
                sphere_size=120,
                input_device=None,
                output_device=None,
                play_output='System Default',
                play_translation='System Default',
                speech_volume=50,
                playback_speed=1.0,
                volume=50,
                temperature=0.7,
                ollama_url='http://localhost:11434/',
                tts_provider='Kokoro (Offline)',
                voice_provider='kokoro',
                kokoro_voice='af_heart',
                agent_provider='Ollama',
                agent_model=_rec.get('conversational', 'qwen3:0.6b'),
                llm_provider='Ollama',
                llm_model=_rec.get('conversational', 'qwen3:0.6b'),
                conversational_llm_provider='Ollama',
                conversational_llm_model=_rec.get('conversational', 'qwen3:0.6b'),
                coding_llm_provider='Ollama',
                coding_llm_model=_rec.get('coding', 'qwen2.5-coder:0.5b'),
                vision_llm_provider='Ollama',
                vision_llm_model=_rec.get('vision', 'qwen3-vl:2b'),
                image_llm_provider='Ollama',
                image_llm_model='x/flux2-klein:latest',
                code_provider='Ollama',
                code_model=_rec.get('coding', 'qwen2.5-coder:0.5b'),
                input_speech='Vosk',
                transcription_model='Whisper.cpp (Local & Offline)',
                indexed_folders='[]',
                connected_accounts='[]',
                last_listening_state=True,
                hands_free_mode=False,  # Default to push-to-talk mode
                accepted_eula=False,
                elevenlabs_enabled=False,
                elevenlabs_key='',
                chat_voice_enabled=True
            )
            session.add(default_settings)
            try:
                session.commit()
            except Exception as e:
                session.rollback()
                logging.getLogger(__name__).error(f"Error creating default settings: {str(e)}")
                raise

init_db()

# Register cleanup function to dispose of connections on exit
def _cleanup_connections():
    """Cleanup database connections on application exit"""
    try:
        engine.dispose()
        logging.getLogger(__name__).debug("Database connections disposed on exit")
    except Exception:
        pass  # Ignore errors during shutdown

atexit.register(_cleanup_connections)
