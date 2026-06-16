from sqlalchemy import create_engine, Column, Integer, String, Float, Text, Boolean, DateTime, ForeignKey, text as sa_text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, backref
from sqlalchemy.dialects.sqlite import CHAR
from uuid import uuid4
import hashlib
import os
import logging
from distr.core.paths import DB_DIR
from distr.core.hotkeys import DEFAULTS as HOTKEY_DEFAULTS
import atexit
from distr.core.db.time import utc_now_naive

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
    telegram_send_online_notice = Column(Boolean, default=True)
    load_on_startup = Column(Boolean, default=True)
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
    agent_model = Column(String, default='deepseek-v4-pro:cloud')
    code_provider = Column(String, default='Ollama')
    code_model = Column(String, default='glm-5.1:cloud')
    input_speech = Column(String, default='Vosk')
    transcription_model = Column(String, default='Whisper.cpp (Local & Offline)')
    
    # Multiple LLM Settings (Conversational, Coding, Vision, Image)
    # llm_provider/model: Standardized names used by ChatManager for hot-reload
    llm_provider = Column(String, default='Ollama')
    llm_model = Column(String, default='deepseek-v4-pro:cloud')
    conversational_llm_provider = Column(String, default='Ollama')
    conversational_llm_model = Column(String, default='deepseek-v4-pro:cloud')
    coding_llm_provider = Column(String, default='Ollama')
    coding_llm_model = Column(String, default='glm-5.1:cloud')
    workflow_llm_provider = Column(String, default='')
    workflow_llm_model = Column(String, default='')
    step_runner_llm_provider = Column(String, default='')
    step_runner_llm_model = Column(String, default='')
    vision_llm_provider = Column(String, default='Ollama')
    vision_llm_model = Column(String, default='qwen3-vl:235b-cloud')
    image_llm_provider = Column(String, default='Ollama')
    image_llm_model = Column(String, default='x/flux2-klein:latest')
    computer_use_provider = Column(String, default='')
    computer_use_model = Column(String, default='')
    project_cli_low_backend = Column(String, default='cursor')
    project_cli_low_model = Column(String, default='auto')
    project_cli_medium_backend = Column(String, default='codex')
    project_cli_medium_model = Column(String, default='auto')
    project_cli_high_backend = Column(String, default='codex')
    project_cli_high_model = Column(String, default='gpt-5.3-codex')
    project_cli_low_codex_intelligence = Column(String, default='')
    project_cli_low_codex_speed = Column(String, default='')
    project_cli_medium_codex_intelligence = Column(String, default='')
    project_cli_medium_codex_speed = Column(String, default='')
    project_cli_high_codex_intelligence = Column(String, default='')
    project_cli_high_codex_speed = Column(String, default='')
    project_cli_low_fallback_backend = Column(String, default='')
    project_cli_low_fallback_model = Column(String, default='')
    project_cli_medium_fallback_backend = Column(String, default='')
    project_cli_medium_fallback_model = Column(String, default='')
    project_cli_high_fallback_backend = Column(String, default='')
    project_cli_high_fallback_model = Column(String, default='')
    orchestrator_enabled = Column(Boolean, default=True)
    orchestrator_provider = Column(String, default='')
    orchestrator_model = Column(String, default='')
    orchestrator_validator_provider = Column(String, default='')
    orchestrator_validator_model = Column(String, default='')
    orchestrator_correction_provider = Column(String, default='')
    orchestrator_correction_model = Column(String, default='')
    orchestrator_memory_export_enabled = Column(Boolean, default=False)

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
    cursor_enabled = Column(Boolean, default=False)
    elevenlabs_enabled = Column(Boolean, default=False)
    ollama_enabled = Column(Boolean, default=True)  # Default to True for Ollama
    openrouter_enabled = Column(Boolean, default=False)
    groq_enabled = Column(Boolean, default=False)
    kilo_enabled = Column(Boolean, default=False)
    gemini_enabled = Column(Boolean, default=False)

    # Provider Keys/URLs
    assemblyai_key = Column(String, default='')
    speechmatics_key = Column(String, default='')
    openai_key = Column(String, default='')
    anthropic_key = Column(String, default='')
    cursor_key = Column(String, default='')
    ollama_url = Column(String, default='http://localhost:11434/')
    elevenlabs_key = Column(String, default='')
    openrouter_key = Column(String, default='')
    groq_key = Column(String, default='')
    kilo_key = Column(String, default='')
    gemini_key = Column(String, default='')

    last_listening_state = Column(Boolean, default=True)
    hands_free_mode = Column(Boolean, default=True)
    global_ptt_hotkey_enabled = Column(Boolean, default=True)
    global_ptt_hotkey_combo = Column(String, default=HOTKEY_DEFAULTS['global_ptt_hotkey_combo'])
    oracle_size_hotkey_decrease_modifier = Column(String, default=HOTKEY_DEFAULTS['oracle_size_hotkey_decrease_modifier'])
    oracle_size_hotkey_decrease_key = Column(String, default=HOTKEY_DEFAULTS['oracle_size_hotkey_decrease_key'])
    oracle_size_hotkey_increase_modifier = Column(String, default=HOTKEY_DEFAULTS['oracle_size_hotkey_increase_modifier'])
    oracle_size_hotkey_increase_key = Column(String, default=HOTKEY_DEFAULTS['oracle_size_hotkey_increase_key'])
    recording_hotkey_enabled = Column(Boolean, default=True)
    recording_hotkey_modifier = Column(String, default=HOTKEY_DEFAULTS['recording_hotkey_modifier'])
    recording_hotkey_key = Column(String, default=HOTKEY_DEFAULTS['recording_hotkey_key'])
    dictation_hotkey_enabled = Column(Boolean, default=HOTKEY_DEFAULTS['dictation_hotkey_enabled'])
    dictation_hotkey_modifier = Column(String, default=HOTKEY_DEFAULTS['dictation_hotkey_modifier'])
    dictation_hotkey_key = Column(String, default=HOTKEY_DEFAULTS['dictation_hotkey_key'])
    ticket_dictation_hotkey_enabled = Column(Boolean, default=HOTKEY_DEFAULTS['ticket_dictation_hotkey_enabled'])
    ticket_dictation_hotkey_modifier = Column(String, default=HOTKEY_DEFAULTS['ticket_dictation_hotkey_modifier'])
    ticket_dictation_hotkey_key = Column(String, default=HOTKEY_DEFAULTS['ticket_dictation_hotkey_key'])
    instant_dictation = Column(Boolean, default=True)
    dictation_ticket_use_llm = Column(Boolean, default=True)
    dictation_ticket_model = Column(String, default='qwen2.5:0.5b')
    dictation_ticket_timeout = Column(String, default='1.2')
    dictation_ticket_prompt = Column(Text, default='')
    skin_nav_hotkey_previous_modifier = Column(String, default=HOTKEY_DEFAULTS['skin_nav_hotkey_previous_modifier'])
    skin_nav_hotkey_previous_key = Column(String, default=HOTKEY_DEFAULTS['skin_nav_hotkey_previous_key'])
    skin_nav_hotkey_next_modifier = Column(String, default=HOTKEY_DEFAULTS['skin_nav_hotkey_next_modifier'])
    skin_nav_hotkey_next_key = Column(String, default=HOTKEY_DEFAULTS['skin_nav_hotkey_next_key'])
    skin_select_hotkey_modifier = Column(String, default=HOTKEY_DEFAULTS['skin_select_hotkey_modifier'])
    web_hotkey_chat_modifier = Column(String, default=HOTKEY_DEFAULTS['web_hotkey_chat_modifier'])
    web_hotkey_chat_key = Column(String, default=HOTKEY_DEFAULTS['web_hotkey_chat_key'])
    web_hotkey_projects_modifier = Column(String, default=HOTKEY_DEFAULTS['web_hotkey_projects_modifier'])
    web_hotkey_projects_key = Column(String, default=HOTKEY_DEFAULTS['web_hotkey_projects_key'])
    web_hotkey_actions_modifier = Column(String, default=HOTKEY_DEFAULTS['web_hotkey_actions_modifier'])
    web_hotkey_actions_key = Column(String, default=HOTKEY_DEFAULTS['web_hotkey_actions_key'])
    web_hotkey_snippets_modifier = Column(String, default=HOTKEY_DEFAULTS['web_hotkey_snippets_modifier'])
    web_hotkey_snippets_key = Column(String, default=HOTKEY_DEFAULTS['web_hotkey_snippets_key'])
    web_hotkey_workflows_modifier = Column(String, default=HOTKEY_DEFAULTS['web_hotkey_workflows_modifier'])
    web_hotkey_workflows_key = Column(String, default=HOTKEY_DEFAULTS['web_hotkey_workflows_key'])
    web_hotkey_preferences_modifier = Column(String, default=HOTKEY_DEFAULTS['web_hotkey_preferences_modifier'])
    web_hotkey_preferences_key = Column(String, default=HOTKEY_DEFAULTS['web_hotkey_preferences_key'])

    voice_provider = Column(String, default='kokoro')
    kokoro_voice = Column(String, default='af_heart')
    elevenlabs_voice = Column(String, default='')
    # ElevenLabs API voice_settings (0-1 floats; only used when voice_provider is ElevenLabs)
    elevenlabs_stability = Column(Float, default=0.5)
    elevenlabs_similarity_boost = Column(Float, default=0.6)
    elevenlabs_style = Column(Float, default=0.25)
    elevenlabs_use_speaker_boost = Column(Boolean, default=True)
    openai_voice = Column(String, default='alloy')
    coqui_voice = Column(String, default='p225')
    qwen3_voice = Column(String, default='aiden')
    f5tts_voice = Column(String, default='default')
    voxcpm_voice = Column(String, default='default')
    supertonic_voice = Column(String, default='M1')
    chatterbox_voice = Column(String, default='default')
    replicate_api_token = Column(String, default='')

    # Rube Settings (discontinued — columns kept for migration compatibility)
    rube_enabled = Column(Boolean, default=False)
    rube_token = Column(String, default='')
    
    # Note: Jira and Trello accounts are now stored in connected_accounts JSON field
    # Individual columns removed to support multiple accounts
    
    # Initiative Settings
    initiative_level = Column(String, default='assist')
    initiative_allow_telegram = Column(Boolean, default=False)
    initiative_allow_routine_tasks = Column(Boolean, default=False)
    initiative_scan_boards = Column(Boolean, default=True)
    initiative_scan_external_boards = Column(Boolean, default=False)
    initiative_scan_email = Column(Boolean, default=False)
    initiative_scan_whatsapp = Column(Boolean, default=True)
    initiative_scan_telegram = Column(Boolean, default=True)
    initiative_suggest_backlog_promotion = Column(Boolean, default=True)
    initiative_allow_ticket_lane_moves = Column(Boolean, default=False)
    initiative_allow_workflow_start = Column(Boolean, default=False)
    initiative_allow_project_cli = Column(Boolean, default=False)
    initiative_ask_external_comms = Column(Boolean, default=True)
    initiative_ask_file_changes = Column(Boolean, default=True)
    initiative_ask_sensitive = Column(Boolean, default=True)
    
    # Chat voice/speaker toggle
    chat_voice_enabled = Column(Boolean, default=True)

    # Telegram response format settings
    telegram_text_only_override = Column(Boolean, default=False)
    telegram_auto_match_mode = Column(Boolean, default=False)
    kanban_cli_tool = Column(String, default='')
    kanban_cli_auth = Column(String, default='')

    # Masko (AI skin generation)
    masko_enabled = Column(Boolean, default=False)
    masko_key = Column(String, default='')


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
    created_date = Column(DateTime, default=utc_now_naive)
    modified_date = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

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

    created_date = Column(DateTime, default=utc_now_naive)
    modified_date = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)


class Snippet(Base):
    __tablename__ = 'snippets'

    id = Column(Integer, primary_key=True)

    title = Column(String) #also a trigger word
    description = Column(String)

    additional_trigger_words = Column(Text)  # Store as JSON string
    remote_hotkey = Column(String, default="")
    snippet = Column(Text)  # Store as JSON string

    created_date = Column(DateTime, default=utc_now_naive)
    modified_date = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)


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
    created_date = Column(DateTime, default=utc_now_naive)
    modified_date = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)


class TrelloTicket(Base):
    __tablename__ = 'trello_tickets'

    id = Column(Integer, primary_key=True)
    trello_card_id = Column(String, unique=True, nullable=False)
    title = Column(String)
    description = Column(Text)
    
    # Local allocations
    chat_id = Column(Integer, ForeignKey('chats.id'), nullable=True)

    # Cached Trello Data
    members = Column(Text)  # JSON string of member names/IDs
    attachments = Column(Text)  # JSON string of attachment URLs/names
    status = Column(String)  # e.g., list name or card status
    
    created_date = Column(DateTime, default=utc_now_naive)
    modified_date = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    # Relationships
    chat = relationship("Chat")


class SkinSize(Base):
    """Per-skin size preference — remembers the last size used for each skin slug."""
    __tablename__ = 'skin_sizes'

    id = Column(Integer, primary_key=True)
    skin_slug = Column(String, nullable=False, unique=True)  # e.g. 'oracle', 'clippy', 'hayley'
    size_px = Column(Integer, nullable=False, default=180)   # pixel size


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
    created_date = Column(DateTime, default=utc_now_naive)  # When stored in database
    modified_date = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)
    
    # Index for faster lookups
    __table_args__ = (
        {'sqlite_autoincrement': True},
    )


class WhatsAppPhoneLink(Base):
    """Link a WhatsApp phone JID to a Ticket Board.
    When a number is linked, its messages show up in the board's WhatsApp tab,
    and Automations can create approved intake tickets from linked messages.
    """
    __tablename__ = 'whatsapp_phone_links'

    id = Column(Integer, primary_key=True)
    board_id = Column(Integer, nullable=False)  # References kanban_boards.id (no FK to avoid circular import)
    phone_jid = Column(String, nullable=False)  # e.g. '27634103646@s.whatsapp.net'
    phone_number = Column(String)  # e.g. '27634103646'
    contact_name = Column(String)  # display name from WhatsApp contacts
    auto_snapshot = Column(Boolean, default=False)  # auto-create ticket on new message?
    created_date = Column(DateTime, default=utc_now_naive)


class WhatsAppComposeDraft(Base):
    """Unsent WhatsApp reply text for a chat (user typing or agent-prepared)."""
    __tablename__ = 'whatsapp_compose_drafts'

    jid_phone = Column(String, primary_key=True)
    jid = Column(String)
    chat_type = Column(String, default='private')
    contact_name = Column(String)
    draft_text = Column(Text, nullable=False, default='')
    source = Column(String, default='user')
    board_id = Column(Integer, nullable=True)
    updated_date = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)


class WhatsAppMessage(Base):
    """Store WhatsApp messages received via Baileys relay."""
    __tablename__ = 'whatsapp_messages'

    id = Column(Integer, primary_key=True)

    # WhatsApp message identifiers
    message_id = Column(String, nullable=False, unique=True)  # WhatsApp's message ID (e.g. '3EB0...')
    jid = Column(String, nullable=False)  # Chat JID (e.g. '27634103646@s.whatsapp.net' or group JID)
    jid_phone = Column(String)  # Phone number extracted from JID
    chat_type = Column(String)  # 'private', 'group'

    # Sender information
    sender_jid = Column(String)  # Sender's JID (differs from chat JID in groups)
    sender_phone = Column(String)  # Sender's phone number
    sender_push_name = Column(String)  # WhatsApp display name (push name)

    # Message content
    text = Column(Text)  # Message text
    caption = Column(Text)  # Caption for media messages
    media_type = Column(String)  # 'photo', 'voice', 'audio', 'video', 'document', 'sticker', or None
    media_mime_type = Column(String)  # MIME type of media
    media_filename = Column(String)  # Original filename (for documents)
    media_local_path = Column(String)  # Local path where media was saved
    media_file_length = Column(Integer)  # File size in bytes
    media_duration = Column(Integer)  # Duration in seconds (voice/audio/video)

    # Metadata
    whatsapp_timestamp = Column(Integer)  # Unix timestamp from WhatsApp
    from_me = Column(Boolean, default=False)  # Whether this was sent by us

    # Full raw data for later processing
    raw_data = Column(Text)  # JSON string of complete message data

    # Processing status
    processed = Column(Boolean, default=False)  # Whether message was processed by agent
    processed_date = Column(DateTime, nullable=True)
    snapshot_group = Column(String, nullable=True)  # Group ID for snapshot batch tracking
    agent_chat_id = Column(Integer, nullable=True)  # Link to Chat row if message was added to a chat

    # Timestamps
    created_date = Column(DateTime, default=utc_now_naive)
    modified_date = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

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
    """Enable WAL mode on every connection and set busy timeout."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()
    logger = logging.getLogger(__name__)
    pool = engine.pool
    logger.debug(f"Database connection created (WAL). Pool size: {pool.size()}, checked out: {pool.checkedout()}")

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

# Add missing ticket board columns to existing settings table before create_all
try:
    with engine.connect() as _conn:
        _result = _conn.execute(sa_text("SELECT name FROM sqlite_master WHERE type='table' AND name='settings'"))
        if _result.fetchone():
            _existing = {row[1] for row in _conn.execute(sa_text("PRAGMA table_info(settings)"))}
            for _col, _def in [
                ("kanban_cli_tool", "VARCHAR DEFAULT ''"),
                ("kanban_cli_auth", "VARCHAR DEFAULT ''"),
                # Initiative settings
        ("initiative_level", "VARCHAR DEFAULT 'assist'"),
        ("initiative_allow_telegram", "BOOLEAN DEFAULT 0"),
        ("initiative_allow_routine_tasks", "BOOLEAN DEFAULT 0"),
        ("initiative_scan_boards", "BOOLEAN DEFAULT 1"),
        ("initiative_scan_external_boards", "BOOLEAN DEFAULT 0"),
        ("initiative_scan_email", "BOOLEAN DEFAULT 0"),
        ("initiative_scan_whatsapp", "BOOLEAN DEFAULT 1"),
        ("initiative_scan_telegram", "BOOLEAN DEFAULT 1"),
        ("initiative_suggest_backlog_promotion", "BOOLEAN DEFAULT 1"),
        ("initiative_allow_ticket_lane_moves", "BOOLEAN DEFAULT 0"),
        ("initiative_allow_workflow_start", "BOOLEAN DEFAULT 0"),
        ("initiative_allow_project_cli", "BOOLEAN DEFAULT 0"),
        ("initiative_ask_external_comms", "BOOLEAN DEFAULT 1"),
                ("initiative_ask_file_changes", "BOOLEAN DEFAULT 1"),
                ("initiative_ask_sensitive", "BOOLEAN DEFAULT 1"),
                # Google Gemini provider
                ("gemini_enabled", "BOOLEAN DEFAULT 0"),
                ("gemini_key", "VARCHAR DEFAULT ''"),
                # Cursor project CLI provider
                ("cursor_enabled", "BOOLEAN DEFAULT 0"),
                ("cursor_key", "VARCHAR DEFAULT ''"),
                # Telegram response format settings
                ("telegram_send_online_notice", "BOOLEAN DEFAULT 1"),
                ("telegram_text_only_override", "BOOLEAN DEFAULT 0"),
                ("telegram_auto_match_mode", "BOOLEAN DEFAULT 0"),
                # Load on startup
                ("load_on_startup", "BOOLEAN DEFAULT 1"),
                # Masko (AI skin generation)
                ("masko_enabled", "BOOLEAN DEFAULT 0"),
                ("masko_key", "VARCHAR DEFAULT ''"),
            ]:
                if _col not in _existing:
                    try:
                        _conn.execute(sa_text(f"ALTER TABLE settings ADD COLUMN {_col} {_def}"))
                        _conn.commit()
                    except Exception:
                        pass
except Exception as _mig_err:
    logging.getLogger(__name__).warning(f"Column migration block failed: {_mig_err}")

# Add snippet remote-control metadata columns before create_all. Existing
# installs need ALTER TABLE because SQLAlchemy create_all will not add columns.
try:
    with engine.connect() as _conn:
        _result = _conn.execute(sa_text("SELECT name FROM sqlite_master WHERE type='table' AND name='snippets'"))
        if _result.fetchone():
            _existing = {row[1] for row in _conn.execute(sa_text("PRAGMA table_info(snippets)"))}
            if "remote_hotkey" not in _existing:
                _conn.execute(sa_text("ALTER TABLE snippets ADD COLUMN remote_hotkey VARCHAR DEFAULT ''"))
                _conn.commit()
except Exception as _snippet_mig_err:
    logging.getLogger(__name__).warning(f"Snippet column migration failed: {_snippet_mig_err}")

# Add workflow unification columns to existing auto_workflow* tables before create_all
try:
    with engine.connect() as _conn:
        # auto_workflows: workflow_type, chat_id, context_rules, workflow_input
        _result = _conn.execute(sa_text("SELECT name FROM sqlite_master WHERE type='table' AND name='auto_workflows'"))
        if _result.fetchone():
            _existing = {row[1] for row in _conn.execute(sa_text("PRAGMA table_info(auto_workflows)"))}
            for _col, _def in [
                ("workflow_type", "VARCHAR DEFAULT 'manual'"),
                ("chat_id", "INTEGER"),
                ("context_rules", "TEXT"),
                ("workflow_input", "TEXT"),
            ]:
                if _col not in _existing:
                    try:
                        _conn.execute(sa_text(f"ALTER TABLE auto_workflows ADD COLUMN {_col} {_def}"))
                        _conn.commit()
                    except Exception:
                        pass
        # auto_workflow_steps: verification, step_type, config, tool_used, routing_path
        _result = _conn.execute(sa_text("SELECT name FROM sqlite_master WHERE type='table' AND name='auto_workflow_steps'"))
        if _result.fetchone():
            _existing = {row[1] for row in _conn.execute(sa_text("PRAGMA table_info(auto_workflow_steps)"))}
            for _col, _def in [
                ("verification", "TEXT"),
                ("step_type", "VARCHAR DEFAULT 'agent_instruction'"),
                ("config", "TEXT"),
                ("tool_used", "VARCHAR"),
                ("routing_path", "TEXT"),
            ]:
                if _col not in _existing:
                    try:
                        _conn.execute(sa_text(f"ALTER TABLE auto_workflow_steps ADD COLUMN {_col} {_def}"))
                        _conn.commit()
                    except Exception:
                        pass
except Exception as _wf_mig_err:
    logging.getLogger(__name__).warning(f"Workflow unification column migration failed: {_wf_mig_err}")

# Create the table
Base.metadata.create_all(engine)

# Create a session factory
Session = sessionmaker(bind=engine)

class SessionContext:
    """Context manager for database sessions to ensure proper cleanup.
    Also supports direct usage for backward compatibility."""
    def __init__(self):
        max_retries = 3
        for attempt in range(max_retries):
            try:
                self.session = Session()
                break
            except Exception as e:
                if "database is locked" in str(e).lower() and attempt < max_retries - 1:
                    import time
                    time.sleep(0.1 * (2 ** attempt))
                    continue
                raise
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
                    import time

                    backoff_s = (0.1, 0.2, 0.4)
                    for attempt in range(len(backoff_s) + 1):
                        try:
                            self.session.commit()
                            break
                        except OperationalError as e:
                            msg_l = str(e).lower()
                            if "database is locked" not in msg_l or attempt >= len(backoff_s):
                                raise
                            time.sleep(backoff_s[attempt])
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


def _cleanup_orphaned_test_ide_sessions() -> None:
    """Remove orphaned IDE-bridge rows from the real runtime DB.

    Older builds and tests created ProjectExecutionSession rows against projects
    that no longer exist. Those rows look like real idle Cursor/Codex sessions at
    startup, which makes Initiative send confusing placeholder nudges. Keep the
    cleanup narrow: only IDE bridge rows with a non-null missing project id go.
    """
    if os.environ.get("DECISIONS_TEST_MODE") == "1":
        return
    try:
        with engine.connect() as conn:
            table_exists = conn.execute(sa_text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='project_execution_sessions'"
            )).fetchone()
            if not table_exists:
                return
            event_table_exists = conn.execute(sa_text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='project_execution_events'"
            )).fetchone()
            where_clause = """
                route_type = 'ide_bridge'
                AND project_id IS NOT NULL
                AND project_id NOT IN (SELECT id FROM projects)
            """
            if event_table_exists:
                conn.execute(sa_text(f"""
                    DELETE FROM project_execution_events
                    WHERE session_id IN (
                        SELECT id FROM project_execution_sessions WHERE {where_clause}
                    )
                """))
            conn.execute(sa_text(f"DELETE FROM project_execution_sessions WHERE {where_clause}"))
            conn.commit()
    except Exception as exc:
        logging.getLogger(__name__).debug(
            "Orphaned test IDE session cleanup skipped: %s",
            exc,
            exc_info=True,
        )


def init_db():
    """Initialize the database by creating all tables and adding default settings"""
    # Import project models to ensure they're registered with Base
    from distr.core.db import projects as _projects  # noqa: F401
    from distr.core.db import workflow as _workflow  # noqa: F401
    from distr.core.db import kanban as _kanban  # noqa: F401
    from distr.core.db import proactive as _proactive  # noqa: F401
    from distr.core.db import planner_output as _planner_output  # noqa: F401
    from distr.core.db import schedule_blocks as _schedule_blocks  # noqa: F401
    from distr.core.db import automation as _automation  # noqa: F401

    # Run migrations that need to happen BEFORE create_all (like renaming old tables)
    from distr.core.db.migrations import run_pre_create_migrations
    run_pre_create_migrations()

    Base.metadata.create_all(engine)

    # Run all database migrations
    from distr.core.db.migrations import run_migrations
    run_migrations()

    _cleanup_orphaned_test_ide_sessions()

    try:
        from distr.core.db.proactive import ensure_system_proactive_tasks

        with Session() as session:
            ensure_system_proactive_tasks(session)
    except Exception as _seed_pt:
        logging.getLogger(__name__).warning("ensure_system_proactive_tasks failed: %s", _seed_pt)
    
    with Session() as session:
        if not session.query(Settings).first():
            # Detect system RAM and pick appropriate models directly
            try:
                from distr.core.system_resources import recommend_ollama_defaults, get_total_ram_gb
                _rec = recommend_ollama_defaults(get_total_ram_gb())
            except Exception:
                _rec = {"conversational": "qwen3:0.6b", "coding": "qwen2.5-coder:0.5b", "vision": "qwen3-vl:235b-cloud"}

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
                agent_model=_rec.get('conversational', 'deepseek-v4-pro:cloud'),
                llm_provider='Ollama',
                llm_model=_rec.get('conversational', 'deepseek-v4-pro:cloud'),
                conversational_llm_provider='Ollama',
                conversational_llm_model=_rec.get('conversational', 'deepseek-v4-pro:cloud'),
                coding_llm_provider='Ollama',
                coding_llm_model=_rec.get('coding', 'glm-5.1:cloud'),
                vision_llm_provider='Ollama',
                vision_llm_model=_rec.get('vision', 'qwen3-vl:235b-cloud'),
                image_llm_provider='Ollama',
                image_llm_model='x/flux2-klein:latest',
                code_provider='Ollama',
                code_model=_rec.get('coding', 'glm-5.1:cloud'),
                input_speech='Vosk',
                transcription_model='Whisper.cpp (Local & Offline)',
                indexed_folders='[]',
                connected_accounts='[]',
                last_listening_state=True,
                hands_free_mode=False,  # Default to push-to-talk mode
                global_ptt_hotkey_enabled=True,
                global_ptt_hotkey_combo=HOTKEY_DEFAULTS['global_ptt_hotkey_combo'],
                oracle_size_hotkey_decrease_modifier=HOTKEY_DEFAULTS['oracle_size_hotkey_decrease_modifier'],
                oracle_size_hotkey_decrease_key=HOTKEY_DEFAULTS['oracle_size_hotkey_decrease_key'],
                oracle_size_hotkey_increase_modifier=HOTKEY_DEFAULTS['oracle_size_hotkey_increase_modifier'],
                oracle_size_hotkey_increase_key=HOTKEY_DEFAULTS['oracle_size_hotkey_increase_key'],
                recording_hotkey_enabled=True,
                recording_hotkey_modifier=HOTKEY_DEFAULTS['recording_hotkey_modifier'],
                recording_hotkey_key=HOTKEY_DEFAULTS['recording_hotkey_key'],
                skin_nav_hotkey_previous_modifier=HOTKEY_DEFAULTS['skin_nav_hotkey_previous_modifier'],
                skin_nav_hotkey_previous_key=HOTKEY_DEFAULTS['skin_nav_hotkey_previous_key'],
                skin_nav_hotkey_next_modifier=HOTKEY_DEFAULTS['skin_nav_hotkey_next_modifier'],
                skin_nav_hotkey_next_key=HOTKEY_DEFAULTS['skin_nav_hotkey_next_key'],
                skin_select_hotkey_modifier=HOTKEY_DEFAULTS['skin_select_hotkey_modifier'],
                web_hotkey_chat_modifier=HOTKEY_DEFAULTS['web_hotkey_chat_modifier'],
                web_hotkey_chat_key=HOTKEY_DEFAULTS['web_hotkey_chat_key'],
                web_hotkey_projects_modifier=HOTKEY_DEFAULTS['web_hotkey_projects_modifier'],
                web_hotkey_projects_key=HOTKEY_DEFAULTS['web_hotkey_projects_key'],
                web_hotkey_actions_modifier=HOTKEY_DEFAULTS['web_hotkey_actions_modifier'],
                web_hotkey_actions_key=HOTKEY_DEFAULTS['web_hotkey_actions_key'],
                web_hotkey_snippets_modifier=HOTKEY_DEFAULTS['web_hotkey_snippets_modifier'],
                web_hotkey_snippets_key=HOTKEY_DEFAULTS['web_hotkey_snippets_key'],
                web_hotkey_workflows_modifier=HOTKEY_DEFAULTS['web_hotkey_workflows_modifier'],
                web_hotkey_workflows_key=HOTKEY_DEFAULTS['web_hotkey_workflows_key'],
                web_hotkey_preferences_modifier=HOTKEY_DEFAULTS['web_hotkey_preferences_modifier'],
                web_hotkey_preferences_key=HOTKEY_DEFAULTS['web_hotkey_preferences_key'],
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
