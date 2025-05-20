from sqlalchemy import create_engine, Column, Integer, String, Float, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, backref
from sqlalchemy.dialects.sqlite import CHAR
from uuid import uuid4
import hashlib
import os
from distr.core.constants import DB_DIR
from datetime import datetime

Base = declarative_base()

class Settings(Base):
    __tablename__ = 'settings'

    id = Column(Integer, primary_key=True)

    last_chat_id = Column(Integer, default=1)

    # General Settings
    language = Column(String, default='English')
    theme = Column(String)
    load_splash_sound = Column(Boolean, default=True)
    show_about = Column(Boolean, default=True)
    startup_listening_state = Column(String, default='remember')  # values: 'remember', 'stop', 'start'

    restore_position = Column(Boolean, default=True)
    oracle_position = Column(String, default='Middle Right')

    selected_oracle = Column(String, default='0.gif')

    sphere_size = Column(Integer, default=140)

    consent_given = Column(Boolean, default=False)
    
    # EULA Acceptance
    accepted_eula = Column(Boolean, default=False)

    # Audio Settings
    input_device = Column(String, default='System Default')
    output_device = Column(String, default='System Default')
    play_output = Column(String, default='System Default')
    play_translation = Column(String, default='System Default')
    lock_sound = Column(Boolean, default=False)
    speech_volume = Column(Integer, default=50)
    playback_speed = Column(Float, default=1.0)
    volume = Column(Integer, default=50)

    # AI Settings
    ai_model = Column(String)
    temperature = Column(Float, default=0.7)
    speechmatics_key = Column(String, default='')
    openai_key = Column(String, default='')
    anthropic_key = Column(String, default='')
    aws_polly_key = Column(String, default='')
    ollama_url = Column(String, default='http://localhost:11434/')
    tts_provider = Column(String, default='Coqui-AI')
    tts_voice = Column(String, default='')
    agent_provider = Column(String, default='Ollama')
    agent_model = Column(String, default='')
    code_provider = Column(String, default='Ollama')
    code_model = Column(String, default='')
    input_speech = Column(String, default='Vosk')

    # Advanced Settings
    excluded_files = Column(String, default='')
    indexed_folders = Column(String, default='[]')  # JSON string of folders
    connected_accounts = Column(String, default='[]')  # JSON string of connected accounts

    # Provider States
    assemblyai_enabled = Column(Boolean, default=False)
    speechmatics_enabled = Column(Boolean, default=False)
    openai_enabled = Column(Boolean, default=False)
    anthropic_enabled = Column(Boolean, default=False)
    elevenlabs_enabled = Column(Boolean, default=False)
    ollama_enabled = Column(Boolean, default=True)  # Default to True for Ollama

    # Provider Keys/URLs
    assemblyai_key = Column(String, default='')
    speechmatics_key = Column(String, default='')
    openai_key = Column(String, default='')
    anthropic_key = Column(String, default='')
    ollama_url = Column(String, default='http://localhost:11434/')
    elevenlabs_key = Column(String, default='')

    last_listening_state = Column(Boolean, default=True)

    voice_provider = Column(String, default='kokoro')
    kokoro_voice = Column(String, default='af_heart')
    elevenlabs_voice = Column(String, default='Hayley Williams')


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

    play_sticky = Column(Boolean, default=False) #if true, does not consider timing nor mouse movement
    action = Column(Text)  # Store as JSON string

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


# Create the database file if it doesn't exist
if not os.path.exists(DB_DIR):
    os.makedirs(DB_DIR)

db_path = os.path.join(DB_DIR, 'settings.db')
engine = create_engine(f'sqlite:///{db_path}')

# Create the table
Base.metadata.create_all(engine)

# Create a session factory
Session = sessionmaker(bind=engine)

def get_session():
    return Session()

def init_db():
    """Initialize the database by creating all tables and adding default settings"""
    Base.metadata.create_all(engine)
    
    with Session() as session:
        if not session.query(Settings).first():
            default_settings = Settings(
                language='English',
                oracle_position='Middle Right',
                selected_oracle='0.gif',
                startup_listening_state='remember',
                sphere_size=180,
                input_device='System Default',
                output_device='System Default',
                play_output='System Default',
                play_translation='System Default',
                speech_volume=50,
                playback_speed=1.0,
                volume=50,
                temperature=0.7,
                ollama_url='http://localhost:11434/',
                tts_provider='Coqui-AI',
                agent_provider='Ollama',
                code_provider='Ollama',
                input_speech='Vosk',
                indexed_folders='[]',
                connected_accounts='[]',
                last_listening_state=True,
                accepted_eula=False,  # Default to EULA not accepted
                elevenlabs_enabled=False,
                elevenlabs_key=''
            )
            session.add(default_settings)
            try:
                session.commit()
            except Exception as e:
                session.rollback()
                print(f"Error creating default settings: {str(e)}")
                raise

init_db()
