"""
Database Migration Utilities

This module handles database schema migrations for adding new columns
and updating existing tables.
"""
import logging
import json
from sqlalchemy import text, inspect
from . import engine, Session, Base, Settings
from datetime import datetime
from distr.core.hotkeys import DEFAULTS as HOTKEY_DEFAULTS

logger = logging.getLogger(__name__)

def run_pre_create_migrations():
    """Run migrations that must happen BEFORE Base.metadata.create_all()"""
    logger.info("Running pre-create migrations...")
    
    # Handle migration for workflow_projects table (renamed from old 'projects' table)
    try:
        with engine.connect() as conn:
            # Check if old 'projects' table exists with 'name' column but no 'folder_location' (old workflow Project model)
            try:
                result = conn.execute(text("PRAGMA table_info(projects)"))
                columns = [row[1] for row in result]
                
                if 'name' in columns and 'folder_location' not in columns:
                    logger.info("Found old 'projects' table (workflow) - migrating to 'workflow_projects'")
                    # Rename old table to workflow_projects
                    conn.execute(text("ALTER TABLE projects RENAME TO workflow_projects"))
                    conn.commit()
                    logger.info("Renamed old projects table to workflow_projects")
            except Exception as e:
                # Table doesn't exist, that's fine
                logger.debug(f"Projects table doesn't exist or already migrated: {e}")
    except Exception as e:
        logger.warning(f"Could not run pre-create migrations: {e}")
    
    logger.info("Pre-create migrations completed")

def run_migrations():
    """Run all database migrations."""
    logger.info("Running database migrations...")
    
    # Handle database migration for hands_free_mode column
    try:
        with Session() as session:
            session.execute(text("SELECT hands_free_mode FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN hands_free_mode BOOLEAN DEFAULT 0"))
                conn.commit()
                logger.info("Added hands_free_mode column to settings table")
            except Exception as e:
                logger.warning(f"Could not add hands_free_mode column: {e}")

    # Handle database migration for global PTT hotkey columns
    try:
        with Session() as session:
            session.execute(text("SELECT global_ptt_hotkey_enabled FROM settings LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN global_ptt_hotkey_enabled BOOLEAN DEFAULT 1"))
                conn.commit()
                logger.info("Added global_ptt_hotkey_enabled column to settings table")
            except Exception as e:
                logger.warning(f"Could not add global_ptt_hotkey_enabled column: {e}")

    try:
        with Session() as session:
            session.execute(text("SELECT global_ptt_hotkey_combo FROM settings LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text(f"ALTER TABLE settings ADD COLUMN global_ptt_hotkey_combo VARCHAR DEFAULT '{HOTKEY_DEFAULTS['global_ptt_hotkey_combo']}'"))
                conn.commit()
                logger.info("Added global_ptt_hotkey_combo column to settings table")
            except Exception as e:
                logger.warning(f"Could not add global_ptt_hotkey_combo column: {e}")

    try:
        with Session() as session:
            session.execute(text("SELECT oracle_size_hotkey_decrease_modifier FROM settings LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text(f"ALTER TABLE settings ADD COLUMN oracle_size_hotkey_decrease_modifier VARCHAR DEFAULT '{HOTKEY_DEFAULTS['oracle_size_hotkey_decrease_modifier']}'"))
                conn.commit()
                logger.info("Added oracle_size_hotkey_decrease_modifier column to settings table")
            except Exception as e:
                logger.warning(f"Could not add oracle_size_hotkey_decrease_modifier column: {e}")

    try:
        with Session() as session:
            session.execute(text("SELECT oracle_size_hotkey_decrease_key FROM settings LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text(f"ALTER TABLE settings ADD COLUMN oracle_size_hotkey_decrease_key VARCHAR DEFAULT '{HOTKEY_DEFAULTS['oracle_size_hotkey_decrease_key']}'"))
                conn.commit()
                logger.info("Added oracle_size_hotkey_decrease_key column to settings table")
            except Exception as e:
                logger.warning(f"Could not add oracle_size_hotkey_decrease_key column: {e}")

    try:
        with Session() as session:
            session.execute(text("SELECT oracle_size_hotkey_increase_modifier FROM settings LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text(f"ALTER TABLE settings ADD COLUMN oracle_size_hotkey_increase_modifier VARCHAR DEFAULT '{HOTKEY_DEFAULTS['oracle_size_hotkey_increase_modifier']}'"))
                conn.commit()
                logger.info("Added oracle_size_hotkey_increase_modifier column to settings table")
            except Exception as e:
                logger.warning(f"Could not add oracle_size_hotkey_increase_modifier column: {e}")

    try:
        with Session() as session:
            session.execute(text("SELECT oracle_size_hotkey_increase_key FROM settings LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text(f"ALTER TABLE settings ADD COLUMN oracle_size_hotkey_increase_key VARCHAR DEFAULT '{HOTKEY_DEFAULTS['oracle_size_hotkey_increase_key']}'"))
                conn.commit()
                logger.info("Added oracle_size_hotkey_increase_key column to settings table")
            except Exception as e:
                logger.warning(f"Could not add oracle_size_hotkey_increase_key column: {e}")
    
    # Handle database migration for accepted_eula column
    try:
        with Session() as session:
            session.execute(text("SELECT accepted_eula FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN accepted_eula BOOLEAN DEFAULT 0"))
                conn.commit()
                logger.info("Added accepted_eula column to settings table")
            except Exception as e:
                logger.warning(f"Could not add accepted_eula column: {e}")
    
    # Handle database migration for recording_filename column
    try:
        with Session() as session:
            session.execute(text("SELECT recording_filename FROM actions LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE actions ADD COLUMN recording_filename VARCHAR"))
                conn.commit()
                logger.info("Added recording_filename column to actions table")
            except Exception as e:
                logger.warning(f"Could not add recording_filename column: {e}")
    
    # Handle database migration for last_run_date column on actions
    try:
        with Session() as session:
            session.execute(text("SELECT last_run_date FROM actions LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE actions ADD COLUMN last_run_date DATETIME"))
                conn.commit()
                logger.info("Added last_run_date column to actions table")
            except Exception as e:
                logger.warning(f"Could not add last_run_date column: {e}")
    
    # Handle database migration for chat_voice_enabled column
    try:
        with Session() as session:
            session.execute(text("SELECT chat_voice_enabled FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN chat_voice_enabled BOOLEAN DEFAULT 1"))
                conn.commit()
                logger.info("Added chat_voice_enabled column to settings table")
            except Exception as e:
                logger.warning(f"Could not add chat_voice_enabled column: {e}")
    
    # Handle database migration for welcome_greet_me column
    try:
        with Session() as session:
            session.execute(text("SELECT welcome_greet_me FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN welcome_greet_me BOOLEAN DEFAULT 1"))
                conn.commit()
                logger.info("Added welcome_greet_me column to settings table")
            except Exception as e:
                logger.warning(f"Could not add welcome_greet_me column: {e}")

    # Handle database migration for telegram_send_online_notice column
    try:
        with Session() as session:
            session.execute(text("SELECT telegram_send_online_notice FROM settings LIMIT 1"))
    except Exception:
        # Lifecycle notices are opt-in; frequent restarts must not spam Telegram.
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN telegram_send_online_notice BOOLEAN DEFAULT 0"))
                conn.commit()
                logger.info("Added telegram_send_online_notice column to settings table")
            except Exception as e:
                logger.warning(f"Could not add telegram_send_online_notice column: {e}")
    
    # Handle database migration for always_confirm_file_operations column
    try:
        with Session() as session:
            session.execute(text("SELECT always_confirm_file_operations FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN always_confirm_file_operations BOOLEAN DEFAULT 1"))
                conn.commit()
                logger.info("Added always_confirm_file_operations column to settings table")
            except Exception as e:
                logger.warning(f"Could not add always_confirm_file_operations column: {e}")
    
    # Handle database migration for translation_device column
    try:
        with Session() as session:
            session.execute(text("SELECT translation_device FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN translation_device VARCHAR DEFAULT 'System Default'"))
                conn.commit()
                logger.info("Added translation_device column to settings table")
            except Exception as e:
                logger.warning(f"Could not add translation_device column: {e}")
    
    # Handle database migration for transcription_model column
    try:
        with Session() as session:
            session.execute(text("SELECT transcription_model FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN transcription_model VARCHAR DEFAULT 'Whisper.cpp (Local & Offline)'"))
                conn.commit()
                logger.info("Added transcription_model column to settings table")
            except Exception as e:
                logger.warning(f"Could not add transcription_model column: {e}")
    
    # Handle database migration for locked_input and locked_output columns
    try:
        with Session() as session:
            session.execute(text("SELECT locked_input FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN locked_input VARCHAR DEFAULT 'System Default'"))
                conn.commit()
                logger.info("Added locked_input column to settings table")
            except Exception as e:
                logger.warning(f"Could not add locked_input column: {e}")
    
    try:
        with Session() as session:
            session.execute(text("SELECT locked_output FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN locked_output VARCHAR DEFAULT 'System Default'"))
                conn.commit()
                logger.info("Added locked_output column to settings table")
            except Exception as e:
                logger.warning(f"Could not add locked_output column: {e}")
    
    # Handle database migration for locked_input_list and locked_output_list columns
    try:
        with Session() as session:
            session.execute(text("SELECT locked_input_list FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN locked_input_list TEXT"))
                conn.commit()
                logger.info("Added locked_input_list column to settings table")
            except Exception as e:
                logger.warning(f"Could not add locked_input_list column: {e}")
    
    try:
        with Session() as session:
            session.execute(text("SELECT locked_output_list FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN locked_output_list TEXT"))
                conn.commit()
                logger.info("Added locked_output_list column to settings table")
            except Exception as e:
                logger.warning(f"Could not add locked_output_list column: {e}")
    
    # Handle database migration for vad_threshold column
    try:
        with Session() as session:
            session.execute(text("SELECT vad_threshold FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN vad_threshold INTEGER DEFAULT 50"))
                conn.commit()
                logger.info("Added vad_threshold column to settings table")
            except Exception as e:
                logger.warning(f"Could not add vad_threshold column: {e}")
    
    # Handle database migration for rube_enabled column
    try:
        with Session() as session:
            session.execute(text("SELECT rube_enabled FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN rube_enabled BOOLEAN DEFAULT 0"))
                conn.commit()
                logger.info("Added rube_enabled column to settings table")
            except Exception as e:
                logger.warning(f"Could not add rube_enabled column: {e}")
    
    # Handle database migration for rube_token column
    try:
        with Session() as session:
            session.execute(text("SELECT rube_token FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN rube_token VARCHAR DEFAULT ''"))
                conn.commit()
                logger.info("Added rube_token column to settings table")
            except Exception as e:
                logger.warning(f"Could not add rube_token column: {e}")
    
    # Handle database migration for openrouter_enabled column
    try:
        with Session() as session:
            session.execute(text("SELECT openrouter_enabled FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN openrouter_enabled BOOLEAN DEFAULT 0"))
                conn.commit()
                logger.info("Added openrouter_enabled column to settings table")
            except Exception as e:
                logger.warning(f"Could not add openrouter_enabled column: {e}")
    
    # Handle database migration for openrouter_key column
    try:
        with Session() as session:
            session.execute(text("SELECT openrouter_key FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN openrouter_key VARCHAR DEFAULT ''"))
                conn.commit()
                logger.info("Added openrouter_key column to settings table")
            except Exception as e:
                logger.warning(f"Could not add openrouter_key column: {e}")
    
    # Handle database migration for groq_enabled column
    try:
        with Session() as session:
            session.execute(text("SELECT groq_enabled FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN groq_enabled BOOLEAN DEFAULT 0"))
                conn.commit()
                logger.info("Added groq_enabled column to settings table")
            except Exception as e:
                logger.warning(f"Could not add groq_enabled column: {e}")
    
    # Handle database migration for groq_key column
    try:
        with Session() as session:
            session.execute(text("SELECT groq_key FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN groq_key VARCHAR DEFAULT ''"))
                conn.commit()
                logger.info("Added groq_key column to settings table")
            except Exception as e:
                logger.warning(f"Could not add groq_key column: {e}")
    
    # Handle database migration for kilo_enabled column
    try:
        with Session() as session:
            session.execute(text("SELECT kilo_enabled FROM settings LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN kilo_enabled BOOLEAN DEFAULT 0"))
                conn.commit()
                logger.info("Added kilo_enabled column to settings table")
            except Exception as e:
                logger.warning(f"Could not add kilo_enabled column: {e}")
    
    # Handle database migration for kilo_key column
    try:
        with Session() as session:
            session.execute(text("SELECT kilo_key FROM settings LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN kilo_key VARCHAR DEFAULT ''"))
                conn.commit()
                logger.info("Added kilo_key column to settings table")
            except Exception as e:
                logger.warning(f"Could not add kilo_key column: {e}")
    
    # Handle database migration for gemini_enabled column
    try:
        with Session() as session:
            session.execute(text("SELECT gemini_enabled FROM settings LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN gemini_enabled BOOLEAN DEFAULT 0"))
                conn.commit()
                logger.info("Added gemini_enabled column to settings table")
            except Exception as e:
                logger.warning(f"Could not add gemini_enabled column: {e}")
    
    # Handle database migration for gemini_key column
    try:
        with Session() as session:
            session.execute(text("SELECT gemini_key FROM settings LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN gemini_key VARCHAR DEFAULT ''"))
                conn.commit()
                logger.info("Added gemini_key column to settings table")
            except Exception as e:
                logger.warning(f"Could not add gemini_key column: {e}")

    # Handle database migration for nvidia_enabled column
    try:
        with Session() as session:
            session.execute(text("SELECT nvidia_enabled FROM settings LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN nvidia_enabled BOOLEAN DEFAULT 0"))
                conn.commit()
                logger.info("Added nvidia_enabled column to settings table")
            except Exception as e:
                logger.warning(f"Could not add nvidia_enabled column: {e}")

    # Handle database migration for nvidia_key column
    try:
        with Session() as session:
            session.execute(text("SELECT nvidia_key FROM settings LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN nvidia_key VARCHAR DEFAULT ''"))
                conn.commit()
                logger.info("Added nvidia_key column to settings table")
            except Exception as e:
                logger.warning(f"Could not add nvidia_key column: {e}")

    # Handle database migration for pixazo_enabled column
    try:
        with Session() as session:
            session.execute(text("SELECT pixazo_enabled FROM settings LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN pixazo_enabled BOOLEAN DEFAULT 0"))
                conn.commit()
                logger.info("Added pixazo_enabled column to settings table")
            except Exception as e:
                logger.warning(f"Could not add pixazo_enabled column: {e}")

    # Handle database migration for pixazo_key column
    try:
        with Session() as session:
            session.execute(text("SELECT pixazo_key FROM settings LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN pixazo_key VARCHAR DEFAULT ''"))
                conn.commit()
                logger.info("Added pixazo_key column to settings table")
            except Exception as e:
                logger.warning(f"Could not add pixazo_key column: {e}")

    # Handle database migration for pixazo_voice column
    try:
        with Session() as session:
            session.execute(text("SELECT pixazo_voice FROM settings LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN pixazo_voice VARCHAR DEFAULT 'voxcpm'"))
                conn.commit()
                logger.info("Added pixazo_voice column to settings table")
            except Exception as e:
                logger.warning(f"Could not add pixazo_voice column: {e}")

    # Handle database migration for pixazo_dit_steps column
    try:
        with Session() as session:
            session.execute(text("SELECT pixazo_dit_steps FROM settings LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN pixazo_dit_steps INTEGER DEFAULT 6"))
                conn.commit()
                logger.info("Added pixazo_dit_steps column to settings table")
            except Exception as e:
                logger.warning(f"Could not add pixazo_dit_steps column: {e}")

    # Handle database migration for video_llm_provider column
    try:
        with Session() as session:
            session.execute(text("SELECT video_llm_provider FROM settings LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN video_llm_provider VARCHAR DEFAULT ''"))
                conn.commit()
                logger.info("Added video_llm_provider column to settings table")
            except Exception as e:
                logger.warning(f"Could not add video_llm_provider column: {e}")

    # Handle database migration for video_llm_model column
    try:
        with Session() as session:
            session.execute(text("SELECT video_llm_model FROM settings LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN video_llm_model VARCHAR DEFAULT ''"))
                conn.commit()
                logger.info("Added video_llm_model column to settings table")
            except Exception as e:
                logger.warning(f"Could not add video_llm_model column: {e}")

    # Handle database migration for cursor_enabled column
    try:
        with Session() as session:
            session.execute(text("SELECT cursor_enabled FROM settings LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN cursor_enabled BOOLEAN DEFAULT 0"))
                conn.commit()
                logger.info("Added cursor_enabled column to settings table")
            except Exception as e:
                logger.warning(f"Could not add cursor_enabled column: {e}")

    # Handle database migration for cursor_key column
    try:
        with Session() as session:
            session.execute(text("SELECT cursor_key FROM settings LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN cursor_key VARCHAR DEFAULT ''"))
                conn.commit()
                logger.info("Added cursor_key column to settings table")
            except Exception as e:
                logger.warning(f"Could not add cursor_key column: {e}")
    
    # Handle database migration for model_name column in chats table
    try:
        with Session() as session:
            session.execute(text("SELECT model_name FROM chats LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE chats ADD COLUMN model_name VARCHAR DEFAULT NULL"))
                conn.commit()
                logger.info("Added model_name column to chats table")
            except Exception as e:
                logger.warning(f"Could not add model_name column: {e}")
    
    # Handle database migration for provider column in chats table
    try:
        with Session() as session:
            session.execute(text("SELECT provider FROM chats LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE chats ADD COLUMN provider VARCHAR DEFAULT NULL"))
                conn.commit()
                logger.info("Added provider column to chats table")
            except Exception as e:
                logger.warning(f"Could not add provider column: {e}")
    
    # Handle database migration for openai_voice column
    try:
        with Session() as session:
            session.execute(text("SELECT openai_voice FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN openai_voice VARCHAR DEFAULT 'alloy'"))
                conn.commit()
                logger.info("Added openai_voice column to settings table")
            except Exception as e:
                logger.warning(f"Could not add openai_voice column: {e}")

    # Handle database migration for f5tts_voice column
    try:
        with Session() as session:
            session.execute(text("SELECT f5tts_voice FROM settings LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN f5tts_voice VARCHAR DEFAULT 'default'"))
                conn.commit()
                logger.info("Added f5tts_voice column to settings table")
            except Exception as e:
                logger.warning(f"Could not add f5tts_voice column: {e}")

    # Handle database migration for voxcpm_voice column
    try:
        with Session() as session:
            session.execute(text("SELECT voxcpm_voice FROM settings LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN voxcpm_voice VARCHAR DEFAULT 'default'"))
                conn.commit()
                logger.info("Added voxcpm_voice column to settings table")
            except Exception as e:
                logger.warning(f"Could not add voxcpm_voice column: {e}")

    # Handle database migration for supertonic_voice column
    try:
        with Session() as session:
            session.execute(text("SELECT supertonic_voice FROM settings LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN supertonic_voice VARCHAR DEFAULT 'M1'"))
                conn.commit()
                logger.info("Added supertonic_voice column to settings table")
            except Exception as e:
                logger.warning(f"Could not add supertonic_voice column: {e}")

    # Handle database migration for chatterbox_voice column
    try:
        with Session() as session:
            session.execute(text("SELECT chatterbox_voice FROM settings LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN chatterbox_voice VARCHAR DEFAULT 'default'"))
                conn.commit()
                logger.info("Added chatterbox_voice column to settings table")
            except Exception as e:
                logger.warning(f"Could not add chatterbox_voice column: {e}")

    # Handle database migration for coqui_voice column (Coqui TTS offline voices)
    try:
        with Session() as session:
            session.execute(text("SELECT coqui_voice FROM settings LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN coqui_voice VARCHAR DEFAULT 'p225'"))
                conn.commit()
                logger.info("Added coqui_voice column to settings table")
            except Exception as e:
                logger.warning(f"Could not add coqui_voice column: {e}")

    # Handle database migration for qwen3_voice/replicate settings columns (Qwen3-TTS)
    for col, dtype, default in [
        ("qwen3_voice", "VARCHAR", "'aiden'"),
        ("replicate_api_token", "VARCHAR", "''"),
        ("replicate_enabled", "BOOLEAN", "0"),
    ]:
        try:
            with Session() as session:
                session.execute(text(f"SELECT {col} FROM settings LIMIT 1"))
        except Exception:
            with engine.connect() as conn:
                try:
                    conn.execute(text(f"ALTER TABLE settings ADD COLUMN {col} {dtype} DEFAULT {default}"))
                    conn.commit()
                    logger.info("Added %s column to settings table", col)
                except Exception as e:
                    logger.warning("Could not add %s column: %s", col, e)

    # ElevenLabs voice_settings (only used when voice_provider is ElevenLabs)
    for col, default in [
        ("elevenlabs_stability", "0.5"),
        ("elevenlabs_similarity_boost", "0.6"),
        ("elevenlabs_style", "0.25"),
        ("elevenlabs_use_speaker_boost", "1"),
    ]:
        try:
            with Session() as session:
                session.execute(text(f"SELECT {col} FROM settings LIMIT 1"))
        except Exception:
            with engine.connect() as conn:
                try:
                    dtype = "BOOLEAN" if "speaker_boost" in col else "REAL"
                    conn.execute(text(f"ALTER TABLE settings ADD COLUMN {col} {dtype} DEFAULT {default}"))
                    conn.commit()
                    logger.info(f"Added {col} column to settings table")
                except Exception as e:
                    logger.warning(f"Could not add {col} column: {e}")

    # Handle database migration for conversational_llm_provider column
    try:
        with Session() as session:
            session.execute(text("SELECT conversational_llm_provider FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN conversational_llm_provider VARCHAR DEFAULT 'Ollama'"))
                conn.commit()
                logger.info("Added conversational_llm_provider column to settings table")
            except Exception as e:
                logger.warning(f"Could not add conversational_llm_provider column: {e}")
    
    # Handle database migration for conversational_llm_model column
    try:
        with Session() as session:
            session.execute(text("SELECT conversational_llm_model FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN conversational_llm_model VARCHAR DEFAULT ''"))
                conn.commit()
                logger.info("Added conversational_llm_model column to settings table")
            except Exception as e:
                logger.warning(f"Could not add conversational_llm_model column: {e}")
    
    # Handle database migration for coding_llm_provider column
    try:
        with Session() as session:
            session.execute(text("SELECT coding_llm_provider FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN coding_llm_provider VARCHAR DEFAULT 'Ollama'"))
                conn.commit()
                logger.info("Added coding_llm_provider column to settings table")
            except Exception as e:
                logger.warning(f"Could not add coding_llm_provider column: {e}")
    
    # Handle database migration for coding_llm_model column
    try:
        with Session() as session:
            session.execute(text("SELECT coding_llm_model FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN coding_llm_model VARCHAR DEFAULT ''"))
                conn.commit()
                logger.info("Added coding_llm_model column to settings table")
            except Exception as e:
                logger.warning(f"Could not add coding_llm_model column: {e}")

    # Handle database migration for workflow/legacy workflow LLM columns
    for _col, _default in [
        ("workflow_llm_provider", "''"),
        ("workflow_llm_model", "''"),
        ("step_runner_llm_provider", "''"),
        ("step_runner_llm_model", "''"),
    ]:
        try:
            with Session() as session:
                session.execute(text(f"SELECT {_col} FROM settings LIMIT 1"))
        except Exception:
            with engine.connect() as conn:
                try:
                    conn.execute(text(f"ALTER TABLE settings ADD COLUMN {_col} VARCHAR DEFAULT {_default}"))
                    conn.commit()
                    logger.info("Added %s column to settings table", _col)
                except Exception as e:
                    logger.warning("Could not add %s column: %s", _col, e)

    # Handle database migration for vision_llm_provider column
    try:
        with Session() as session:
            session.execute(text("SELECT vision_llm_provider FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN vision_llm_provider VARCHAR DEFAULT 'Ollama'"))
                conn.commit()
                logger.info("Added vision_llm_provider column to settings table")
            except Exception as e:
                logger.warning(f"Could not add vision_llm_provider column: {e}")
    
    # Handle database migration for vision_llm_model column
    try:
        with Session() as session:
            session.execute(text("SELECT vision_llm_model FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN vision_llm_model VARCHAR DEFAULT ''"))
                conn.commit()
                logger.info("Added vision_llm_model column to settings table")
            except Exception as e:
                logger.warning(f"Could not add vision_llm_model column: {e}")
    
    # Handle database migration for image_llm_provider column
    try:
        with Session() as session:
            session.execute(text("SELECT image_llm_provider FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN image_llm_provider VARCHAR DEFAULT 'Ollama'"))
                conn.commit()
                logger.info("Added image_llm_provider column to settings table")
            except Exception as e:
                logger.warning(f"Could not add image_llm_provider column: {e}")
    
    # Handle database migration for image_llm_model column
    try:
        with Session() as session:
            session.execute(text("SELECT image_llm_model FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN image_llm_model VARCHAR DEFAULT ''"))
                conn.commit()
                logger.info("Added image_llm_model column to settings table")
            except Exception as e:
                logger.warning(f"Could not add image_llm_model column: {e}")

    # Handle database migration for computer_use LLM columns
    for _col, _default in [("computer_use_provider", "''"), ("computer_use_model", "''")]:
        try:
            with Session() as session:
                session.execute(text(f"SELECT {_col} FROM settings LIMIT 1"))
        except Exception:
            with engine.connect() as conn:
                try:
                    conn.execute(text(f"ALTER TABLE settings ADD COLUMN {_col} VARCHAR DEFAULT {_default}"))
                    conn.commit()
                    logger.info("Added %s column to settings table", _col)
                except Exception as e:
                    logger.warning("Could not add %s column: %s", _col, e)

    # Hermes orchestration setup columns. These control the workflow driving
    # brain separately from generic chat and coding defaults.
    for _col, _ddl in [
        ("orchestrator_enabled", "BOOLEAN DEFAULT 1"),
        ("orchestrator_provider", "VARCHAR DEFAULT ''"),
        ("orchestrator_model", "VARCHAR DEFAULT ''"),
        ("orchestrator_validator_provider", "VARCHAR DEFAULT ''"),
        ("orchestrator_validator_model", "VARCHAR DEFAULT ''"),
        ("orchestrator_correction_provider", "VARCHAR DEFAULT ''"),
        ("orchestrator_correction_model", "VARCHAR DEFAULT ''"),
        ("orchestrator_memory_export_enabled", "BOOLEAN DEFAULT 0"),
    ]:
        try:
            with Session() as session:
                session.execute(text(f"SELECT {_col} FROM settings LIMIT 1"))
        except Exception:
            with engine.connect() as conn:
                try:
                    conn.execute(text(f"ALTER TABLE settings ADD COLUMN {_col} {_ddl}"))
                    conn.commit()
                    logger.info("Added %s column to settings table", _col)
                except Exception as e:
                    logger.warning("Could not add %s column: %s", _col, e)

    # Handle database migration for agent_current_chat_id column in settings (web chat "In agent" state)
    try:
        with Session() as session:
            session.execute(text("SELECT agent_current_chat_id FROM settings LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN agent_current_chat_id INTEGER DEFAULT NULL"))
                conn.commit()
                logger.info("Added agent_current_chat_id column to settings table")
            except Exception as e:
                logger.warning(f"Could not add agent_current_chat_id column: {e}")

    # Handle database migration for is_hidden column in chats table
    try:
        with Session() as session:
            session.execute(text("SELECT is_hidden FROM chats LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE chats ADD COLUMN is_hidden BOOLEAN DEFAULT 0"))
                conn.commit()
                logger.info("Added is_hidden column to chats table")
            except Exception as e:
                logger.warning(f"Could not add is_hidden column: {e}")

    # Handle database migration for is_instruction column in actions table
    try:
        with Session() as session:
            session.execute(text("SELECT is_instruction FROM actions LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE actions ADD COLUMN is_instruction BOOLEAN DEFAULT 0"))
                conn.commit()
                logger.info("Added is_instruction column to actions table")
            except Exception as e:
                logger.warning(f"Could not add is_instruction column: {e}")
    
    # Handle database migration for instruction_text column in actions table
    try:
        with Session() as session:
            session.execute(text("SELECT instruction_text FROM actions LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE actions ADD COLUMN instruction_text TEXT"))
                conn.commit()
                logger.info("Added instruction_text column to actions table")
            except Exception as e:
                logger.warning(f"Could not add instruction_text column: {e}")
    
    # Migrate Jira and Trello accounts from individual columns to connected_accounts JSON
    # This migration moves existing single account data to the new multi-account format
    try:
        inspector = inspect(engine)
        columns = {col["name"] for col in inspector.get_columns("settings")}
        if "id" not in columns:
            logger.debug("Skipping Jira/Trello migration: settings.id column missing")
        else:
            # Build a safe projection with only columns that actually exist.
            wanted_cols = [
                "id",
                "connected_accounts",
                "jira_server_url",
                "jira_email",
                "jira_api_token",
                "is_jira_valid",
                "trello_api_key",
                "trello_api_token",
                "is_trello_valid",
            ]
            select_cols = [c for c in wanted_cols if c in columns]
            if select_cols:
                with engine.connect() as conn:
                    row = conn.execute(
                        text(f"SELECT {', '.join(select_cols)} FROM settings LIMIT 1")
                    ).mappings().first()
                    if row:
                        connected_accounts = []
                        raw_connected = row.get("connected_accounts")
                        if raw_connected:
                            try:
                                if isinstance(raw_connected, str):
                                    connected_accounts = json.loads(raw_connected)
                                else:
                                    connected_accounts = raw_connected
                                if not isinstance(connected_accounts, list):
                                    connected_accounts = [connected_accounts] if isinstance(connected_accounts, dict) else []
                            except Exception as e:
                                logger.warning(f"Failed to parse connected_accounts: {e}")
                                connected_accounts = []

                        jira_url = (row.get("jira_server_url") or "").strip()
                        jira_email = (row.get("jira_email") or "").strip()
                        jira_token = (row.get("jira_api_token") or "").strip()
                        has_jira_data = bool(jira_url and jira_email and jira_token)

                        trello_key = (row.get("trello_api_key") or "").strip()
                        trello_token = (row.get("trello_api_token") or "").strip()
                        has_trello_data = bool(trello_key and trello_token)

                        migrated_any = False
                        if has_jira_data:
                            jira_exists = any(
                                isinstance(acc, dict) and acc.get("provider") == "jira"
                                for acc in connected_accounts
                            )
                            if not jira_exists:
                                connected_accounts.append({
                                    "provider": "jira",
                                    "name": "Default Jira Account",
                                    "server_url": jira_url,
                                    "email": jira_email,
                                    "api_token": jira_token,
                                    "is_valid": bool(row.get("is_jira_valid", False)),
                                    "created_at": datetime.utcnow().isoformat(),
                                })
                                migrated_any = True
                                logger.info("Migrated Jira account to connected_accounts")

                        if has_trello_data:
                            trello_exists = any(
                                isinstance(acc, dict) and acc.get("provider") == "trello"
                                for acc in connected_accounts
                            )
                            if not trello_exists:
                                connected_accounts.append({
                                    "provider": "trello",
                                    "name": "Default Trello Account",
                                    "api_key": trello_key,
                                    "api_token": trello_token,
                                    "is_valid": bool(row.get("is_trello_valid", False)),
                                    "created_at": datetime.utcnow().isoformat(),
                                })
                                migrated_any = True
                                logger.info("Migrated Trello account to connected_accounts")

                        if migrated_any and "connected_accounts" in columns:
                            conn.execute(
                                text("UPDATE settings SET connected_accounts = :connected_accounts WHERE id = :id"),
                                {
                                    "connected_accounts": json.dumps(connected_accounts),
                                    "id": int(row["id"]),
                                },
                            )
                            conn.commit()
                            logger.info("Migration completed: Jira/Trello accounts moved to connected_accounts")
    except Exception as e:
        logger.warning(f"Could not migrate Jira/Trello accounts: {e}")
    
    # Handle database migration for llm_provider column
    try:
        with Session() as session:
            session.execute(text("SELECT llm_provider FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN llm_provider VARCHAR DEFAULT 'Ollama'"))
                conn.commit()
                logger.info("Added llm_provider column to settings table")
            except Exception as e:
                logger.warning(f"Could not add llm_provider column: {e}")
    
    # Handle database migration for llm_model column
    try:
        with Session() as session:
            session.execute(text("SELECT llm_model FROM settings LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN llm_model VARCHAR DEFAULT ''"))
                conn.commit()
                logger.info("Added llm_model column to settings table")
            except Exception as e:
                logger.warning(f"Could not add llm_model column: {e}")
    
    logger.info("Database migrations completed")


    # Handle database migration for in_use column in projects table
    try:
        with Session() as session:
            session.execute(text("SELECT in_use FROM projects LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE projects ADD COLUMN in_use BOOLEAN DEFAULT 0"))
                conn.commit()
                logger.info("Added in_use column to projects table")
            except Exception as e:
                logger.warning(f"Could not add in_use column: {e}")

    # Handle database migration for startup_instructions column in projects table
    try:
        with Session() as session:
            session.execute(text("SELECT startup_instructions FROM projects LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE projects ADD COLUMN startup_instructions TEXT"))
                conn.commit()
                logger.info("Added startup_instructions column to projects table")
            except Exception as e:
                logger.warning(f"Could not add startup_instructions column: {e}")
    
    # Handle database migration for provider column in projects table
    try:
        with Session() as session:
            session.execute(text("SELECT provider FROM projects LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE projects ADD COLUMN provider VARCHAR DEFAULT NULL"))
                conn.commit()
                logger.info("Added provider column to projects table")
            except Exception as e:
                logger.warning(f"Could not add provider column: {e}")
    
    # Handle database migration for board_id column in projects table
    try:
        with Session() as session:
            session.execute(text("SELECT board_id FROM projects LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE projects ADD COLUMN board_id VARCHAR DEFAULT NULL"))
                conn.commit()
                logger.info("Added board_id column to projects table")
            except Exception as e:
                logger.warning(f"Could not add board_id column: {e}")
    
    # Handle database migration for board_name column in projects table
    try:
        with Session() as session:
            session.execute(text("SELECT board_name FROM projects LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE projects ADD COLUMN board_name VARCHAR DEFAULT NULL"))
                conn.commit()
                logger.info("Added board_name column to projects table")
            except Exception as e:
                logger.warning(f"Could not add board_name column: {e}")

    # Handle database migration for kanban_board_id column in projects table
    try:
        with Session() as session:
            session.execute(text("SELECT kanban_board_id FROM projects LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE projects ADD COLUMN kanban_board_id INTEGER DEFAULT NULL"))
                conn.commit()
                logger.info("Added kanban_board_id column to projects table")
            except Exception as e:
                logger.warning(f"Could not add kanban_board_id column: {e}")

    # Handle database migration for coding_backend column in projects table
    try:
        with Session() as session:
            session.execute(text("SELECT coding_backend FROM projects LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE projects ADD COLUMN coding_backend VARCHAR DEFAULT 'pi'"))
                conn.execute(text("UPDATE projects SET coding_backend = 'pi' WHERE coding_backend IS NULL OR coding_backend = ''"))
                conn.commit()
                logger.info("Added coding_backend column to projects table")
            except Exception as e:
                logger.warning(f"Could not add coding_backend column: {e}")

    # Handle database migration for per-project CLI model selection
    try:
        with Session() as session:
            session.execute(text("SELECT coding_backend_model FROM projects LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE projects ADD COLUMN coding_backend_model VARCHAR DEFAULT ''"))
                conn.commit()
                logger.info("Added coding_backend_model column to projects table")
            except Exception as e:
                logger.warning(f"Could not add coding_backend_model column: {e}")

    # Handle database migration for manual Projects sidebar ordering
    try:
        with Session() as session:
            session.execute(text("SELECT position FROM projects LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE projects ADD COLUMN position INTEGER DEFAULT 0"))
                conn.commit()
                logger.info("Added position column to projects table")
            except Exception as e:
                logger.warning(f"Could not add position column: {e}")

    # Project columns required before any Project ORM queries below (board column seeding).
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='projects'"))
            if result.fetchone():
                existing = {row[1] for row in conn.execute(text("PRAGMA table_info(projects)"))}
                if "start_time_tracker" not in existing:
                    conn.execute(text("ALTER TABLE projects ADD COLUMN start_time_tracker BOOLEAN DEFAULT 1"))
                    conn.commit()
                    logger.info("Added start_time_tracker column to projects table")
                if "notes" not in existing:
                    conn.execute(text("ALTER TABLE projects ADD COLUMN notes TEXT"))
                    conn.commit()
                    logger.info("Added notes column to projects table")
    except Exception as e:
        logger.warning(f"Could not add project notes/start_time_tracker columns: {e}")

    # Ensure board tables exist
    try:
        from .projects import BoardColumn, BoardTicket  # Imported here to avoid circular imports
        inspector = inspect(engine)
        existing_tables = inspector.get_table_names()
        if 'board_columns' not in existing_tables:
            BoardColumn.__table__.create(bind=engine)
            logger.info("Created board_columns table")
        if 'board_tickets' not in existing_tables:
            BoardTicket.__table__.create(bind=engine)
            logger.info("Created board_tickets table")
        else:
            # Add time_estimate column if it doesn't exist
            try:
                with engine.connect() as conn:
                    result = conn.execute(text("PRAGMA table_info(board_tickets)"))
                    columns = [row[1] for row in result]
                    if 'time_estimate' not in columns:
                        conn.execute(text("ALTER TABLE board_tickets ADD COLUMN time_estimate VARCHAR"))
                        conn.commit()
                        logger.info("Added time_estimate column to board_tickets table")
            except Exception as e:
                logger.warning(f"Could not add time_estimate column: {e}")
    except Exception as e:
        logger.warning(f"Could not ensure board tables exist: {e}")

    # Initialize default columns for existing projects (Backlog, In Progress, QA/Assess, Done)
    try:
        from .projects import Project, BoardColumn
        with Session() as session:
            projects = session.query(Project).all()
            default_columns = ["Backlog", "In Progress", "QA/Assess", "Done"]

            for project in projects:
                existing_count = session.query(BoardColumn).filter_by(project_id=project.id).count()
                if existing_count == 0:
                    for idx, col_name in enumerate(default_columns):
                        column = BoardColumn(
                            project_id=project.id,
                            name=col_name,
                            position=idx
                        )
                        session.add(column)
            session.commit()
            logger.info("Initialized default board columns for existing projects")
    except Exception as e:
        logger.warning(f"Could not initialize default board columns: {e}")

    # Note: Projects table migrations are handled in run_pre_create_migrations()
    # which runs before Base.metadata.create_all()

    # Add voice_provider and voice_model columns to chats table
    try:
        with Session() as session:
            session.execute(text("SELECT voice_provider FROM chats LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        logger.info("Adding voice_provider column to chats table...")
        try:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE chats ADD COLUMN voice_provider VARCHAR"))
                conn.commit()
                logger.info("Added voice_provider column to chats table")
        except Exception as e:
            logger.warning(f"Could not add voice_provider column: {e}")
    try:
        with Session() as session:
            session.execute(text("SELECT voice_model FROM chats LIMIT 1"))
    except Exception:
        # Column doesn't exist, add it
        logger.info("Adding voice_model column to chats table...")
        try:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE chats ADD COLUMN voice_model VARCHAR"))
                conn.commit()
                logger.info("Added voice_model column to chats table")
        except Exception as e:
            logger.warning(f"Could not add voice_model column: {e}")

    # Legacy workflow scheduled sessions (add columns if table exists and columns missing)
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='step_runner_sessions'"))
            if result.fetchone():
                for col, col_def in [
                    ("session_type", "VARCHAR DEFAULT 'instruction'"),
                    ("schedule", "VARCHAR"),
                    ("next_run_at", "DATETIME"),
                    ("last_run_at", "DATETIME"),
                    ("enabled", "BOOLEAN DEFAULT 1"),
                ]:
                    try:
                        conn.execute(text(f"ALTER TABLE step_runner_sessions ADD COLUMN {col} {col_def}"))
                        conn.commit()
                        logger.info(f"Added {col} column to step_runner_sessions table")
                    except Exception as e:
                        if "duplicate column" not in str(e).lower():
                            logger.warning(f"Could not add {col} column: {e}")
    except Exception as e:
        logger.debug(f"Legacy workflow migration: {e}")

    # Legacy workflow schema: timezone, schedule_time, step_runner_runs table
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='step_runner_sessions'"))
            if result.fetchone():
                for col, col_def in [
                    ("timezone", "VARCHAR"),
                    ("schedule_time", "VARCHAR"),
                    ("schedule_days", "VARCHAR"),
                ]:
                    try:
                        conn.execute(text(f"ALTER TABLE step_runner_sessions ADD COLUMN {col} {col_def}"))
                        conn.commit()
                        logger.info(f"Added {col} column to step_runner_sessions table")
                    except Exception as e:
                        if "duplicate column" not in str(e).lower():
                            logger.warning(f"Could not add {col} column: {e}")
            # step_runner_runs table (create_all will create if model exists)
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='step_runner_runs'"))
            if not result.fetchone():
                conn.execute(text("""
                    CREATE TABLE step_runner_runs (
                        id INTEGER PRIMARY KEY,
                        session_id INTEGER NOT NULL REFERENCES step_runner_sessions(id),
                        started_at DATETIME,
                        completed_at DATETIME,
                        status VARCHAR,
                        step_results TEXT
                    )
                """))
                conn.commit()
                logger.info("Created step_runner_runs table")
    except Exception as e:
        logger.debug(f"Legacy workflow migration 2: {e}")

    # Legacy workflow schema: add verification column to steps
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='step_runner_steps'"))
            if result.fetchone():
                try:
                    conn.execute(text("ALTER TABLE step_runner_steps ADD COLUMN verification TEXT"))
                    conn.commit()
                    logger.info("Added verification column to step_runner_steps table")
                except Exception as e:
                    if "duplicate column" not in str(e).lower():
                        logger.warning(f"Could not add verification column: {e}")
    except Exception as e:
        logger.debug(f"Legacy workflow migration 3: {e}")

    # Legacy workflow schema: add step_type, config, code columns to step_runner_steps
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='step_runner_steps'"))
            if result.fetchone():
                for col, col_def in [
                    ("step_type", "VARCHAR DEFAULT 'run_command'"),
                    ("config", "TEXT"),
                    ("code", "TEXT"),
                ]:
                    try:
                        conn.execute(text(f"ALTER TABLE step_runner_steps ADD COLUMN {col} {col_def}"))
                        conn.commit()
                        logger.info(f"Added {col} column to step_runner_steps table")
                    except Exception as e:
                        if "duplicate column" not in str(e).lower():
                            logger.warning(f"Could not add {col} column to step_runner_steps: {e}")
    except Exception as e:
        logger.debug(f"Legacy workflow migration 4 (step_type/config/code): {e}")

    # Legacy workflow schema: add context_rules and workflow_input columns to step_runner_sessions
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='step_runner_sessions'"))
            if result.fetchone():
                for col, col_def in [
                    ("context_rules", "TEXT"),
                    ("workflow_input", "TEXT"),
                ]:
                    try:
                        conn.execute(text(f"ALTER TABLE step_runner_sessions ADD COLUMN {col} {col_def}"))
                        conn.commit()
                        logger.info(f"Added {col} column to step_runner_sessions table")
                    except Exception as e:
                        if "duplicate column" not in str(e).lower():
                            logger.warning(f"Could not add {col} column to step_runner_sessions: {e}")
    except Exception as e:
        logger.debug(f"Legacy workflow migration 5 (context_rules/workflow_input): {e}")

    # Custom Voices: add personality column
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='custom_voices'"))
            if result.fetchone():
                try:
                    conn.execute(text("ALTER TABLE custom_voices ADD COLUMN personality TEXT DEFAULT ''"))
                    conn.commit()
                    logger.info("Added personality column to custom_voices table")
                except Exception as e:
                    if "duplicate column" not in str(e).lower():
                        logger.warning(f"Could not add personality column: {e}")
    except Exception as e:
        logger.debug(f"Custom voices personality migration: {e}")

    # Custom Voices: add gender column
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='custom_voices'"))
            if result.fetchone():
                try:
                    conn.execute(text("ALTER TABLE custom_voices ADD COLUMN gender VARCHAR DEFAULT 'female'"))
                    conn.commit()
                    logger.info("Added gender column to custom_voices table")
                except Exception as e:
                    if "duplicate column" not in str(e).lower():
                        logger.warning(f"Could not add gender column: {e}")
    except Exception as e:
        logger.debug(f"Custom voices gender migration: {e}")

    # AutoWorkflow steps: migrate from multi-action model to single-action-per-step
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='auto_workflow_steps'"))
            if result.fetchone():
                for col, col_def in [
                    ("action_type", "VARCHAR DEFAULT 'agent_instruction'"),
                    ("instruction", "TEXT"),
                    ("validation_prompt", "TEXT"),
                    ("screenshot_path", "VARCHAR"),
                    ("wait_before_next", "INTEGER DEFAULT 0"),
                ]:
                    try:
                        conn.execute(text(f"ALTER TABLE auto_workflow_steps ADD COLUMN {col} {col_def}"))
                        conn.commit()
                        logger.info(f"Added {col} column to auto_workflow_steps table")
                    except Exception as e:
                        if "duplicate column" not in str(e).lower():
                            logger.warning(f"Could not add {col} column: {e}")
            # Add current_step_id to auto_workflow_runs
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='auto_workflow_runs'"))
            if result.fetchone():
                try:
                    conn.execute(text("ALTER TABLE auto_workflow_runs ADD COLUMN current_step_id INTEGER"))
                    conn.commit()
                    logger.info("Added current_step_id column to auto_workflow_runs table")
                except Exception as e:
                    if "duplicate column" not in str(e).lower():
                        logger.warning(f"Could not add current_step_id column: {e}")
                try:
                    conn.execute(text("ALTER TABLE auto_workflow_runs ADD COLUMN step_results TEXT"))
                    conn.commit()
                    logger.info("Added step_results column to auto_workflow_runs table")
                except Exception as e:
                    if "duplicate column" not in str(e).lower():
                        logger.warning(f"Could not add step_results column: {e}")
    except Exception as e:
        logger.debug(f"AutoWorkflow migration: {e}")

    # AutoWorkflowStepResult table + recording_filename column on steps
    try:
        with engine.connect() as conn:
            # Create step_results table if not exists
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS auto_workflow_step_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    step_id INTEGER NOT NULL REFERENCES auto_workflow_steps(id),
                    run_id INTEGER REFERENCES auto_workflow_runs(id),
                    agent_response TEXT,
                    status VARCHAR DEFAULT 'pending',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.commit()
            # Add recording_filename to steps
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='auto_workflow_steps'"))
            if result.fetchone():
                try:
                    conn.execute(text("ALTER TABLE auto_workflow_steps ADD COLUMN recording_filename VARCHAR"))
                    conn.commit()
                    logger.info("Added recording_filename column to auto_workflow_steps table")
                except Exception as e:
                    if "duplicate column" not in str(e).lower():
                        logger.warning(f"Could not add recording_filename column: {e}")
    except Exception as e:
        logger.debug(f"AutoWorkflowStepResult migration: {e}")

    # AutoWorkflow steps: add routing_mode and routing_prompt columns
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='auto_workflow_steps'"))
            if result.fetchone():
                for col, col_def in [
                    ("routing_mode", "VARCHAR DEFAULT 'static'"),
                    ("routing_prompt", "TEXT"),
                ]:
                    try:
                        conn.execute(text(f"ALTER TABLE auto_workflow_steps ADD COLUMN {col} {col_def}"))
                        conn.commit()
                        logger.info(f"Added {col} column to auto_workflow_steps table")
                    except Exception as e:
                        if "duplicate column" not in str(e).lower():
                            logger.warning(f"Could not add {col} column: {e}")
    except Exception as e:
        logger.debug(f"AutoWorkflow routing_mode migration: {e}")

    # Ticket boards: add default routing/link columns.
    _kanban_board_columns = [
        ("default_workflow_id", "INTEGER DEFAULT NULL"),
        ("default_project_id", "INTEGER DEFAULT NULL"),
        ("default_snippet_id", "INTEGER DEFAULT NULL"),
        ("default_action_id", "INTEGER DEFAULT NULL"),
        ("in_use", "BOOLEAN DEFAULT 0"),
    ]
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='kanban_boards'"))
            if result.fetchone():
                for col, col_def in _kanban_board_columns:
                    try:
                        conn.execute(text(f"ALTER TABLE kanban_boards ADD COLUMN {col} {col_def}"))
                        conn.commit()
                        logger.info(f"Added {col} column to kanban_boards table")
                    except Exception as e:
                        if "duplicate column" not in str(e).lower():
                            logger.warning(f"Could not add {col} column to kanban_boards: {e}")
    except Exception as e:
        logger.debug(f"Ticket Board columns migration: {e}")

    # AutoWorkflow steps: add action_id column (link step to Action entity)
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='auto_workflow_steps'"))
            if result.fetchone():
                try:
                    conn.execute(text("ALTER TABLE auto_workflow_steps ADD COLUMN action_id INTEGER REFERENCES actions(id)"))
                    conn.commit()
                    logger.info("Added action_id column to auto_workflow_steps table")
                except Exception as e:
                    if "duplicate column" not in str(e).lower():
                        logger.warning(f"Could not add action_id column: {e}")
    except Exception as e:
        logger.debug(f"AutoWorkflow action_id migration: {e}")

    # AutoWorkflow steps: add code, validation_code, linked_project_id, wait_for_continue columns
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='auto_workflow_steps'"))
            if result.fetchone():
                for col, col_def in [
                    ("code", "TEXT"),
                    ("validation_code", "TEXT"),
                    ("linked_project_id", "INTEGER"),
                    ("wait_for_continue", "BOOLEAN DEFAULT 0"),
                ]:
                    try:
                        conn.execute(text(f"ALTER TABLE auto_workflow_steps ADD COLUMN {col} {col_def}"))
                        conn.commit()
                        logger.info(f"Added {col} column to auto_workflow_steps table")
                    except Exception as e:
                        if "duplicate column" not in str(e).lower():
                            logger.warning(f"Could not add {col} column to auto_workflow_steps: {e}")
    except Exception as e:
        logger.debug(f"AutoWorkflow code/validation/wait migration: {e}")

    # Migrate retired or non-chat Ollama defaults to the current local default.
    try:
        with Session() as session:
            settings = session.query(Settings).first()
            if settings:
                changed = False
                old_conv = (
                    'qwen3:8b', 'qwen3:4b', 'qwen3:1.7b', 'qwen3:0.6b',
                    'gemma4:e2b', 'deepseek-v4-pro:cloud', 'minimax-m2.5:cloud',
                )
                if settings.conversational_llm_model in old_conv or settings.llm_model in old_conv or settings.agent_model in old_conv:
                    settings.conversational_llm_model = 'ornith:9b'
                    settings.llm_model = 'ornith:9b'
                    settings.agent_model = 'ornith:9b'
                    changed = True
                old_code_models = ('glm-5.1:cloud',)
                old_code_prefixes = ('qwen2.5-coder:', 'codegemma')
                coding_model = settings.coding_llm_model or ''
                code_model = settings.code_model or ''
                if (
                    coding_model in old_code_models
                    or code_model in old_code_models
                    or any(coding_model.startswith(p) for p in old_code_prefixes)
                    or any(code_model.startswith(p) for p in old_code_prefixes)
                ):
                    settings.coding_llm_model = 'ornith:9b'
                    settings.code_model = 'ornith:9b'
                    changed = True
                if changed:
                    session.commit()
                    logger.info("Migrated default Ollama models to ornith:9b")
    except Exception as e:
        logger.debug(f"Cloud model migration: {e}")

    # Seed empty workflows with default steps
    try:
        from distr.core.db.seed_workflows import seed_workflows
        seed_workflows()
    except Exception as e:
        logger.warning(f"Could not seed workflows: {e}")

    # Ticket Board/project execution settings columns on settings table
    _kanban_settings_columns = [
        ("kanban_cli_tool", "VARCHAR DEFAULT ''"),
        ("kanban_cli_auth", "VARCHAR DEFAULT ''"),
        ("project_cli_low_backend", "VARCHAR DEFAULT 'cursor'"),
        ("project_cli_low_model", "VARCHAR DEFAULT 'auto'"),
        ("project_cli_low_model_provider", "VARCHAR DEFAULT ''"),
        ("project_cli_medium_backend", "VARCHAR DEFAULT 'codex'"),
        ("project_cli_medium_model", "VARCHAR DEFAULT 'auto'"),
        ("project_cli_medium_model_provider", "VARCHAR DEFAULT ''"),
        ("project_cli_high_backend", "VARCHAR DEFAULT 'codex'"),
        ("project_cli_high_model", "VARCHAR DEFAULT 'gpt-5.3-codex'"),
        ("project_cli_high_model_provider", "VARCHAR DEFAULT ''"),
        ("project_cli_low_codex_intelligence", "VARCHAR DEFAULT ''"),
        ("project_cli_low_codex_speed", "VARCHAR DEFAULT ''"),
        ("project_cli_medium_codex_intelligence", "VARCHAR DEFAULT ''"),
        ("project_cli_medium_codex_speed", "VARCHAR DEFAULT ''"),
        ("project_cli_high_codex_intelligence", "VARCHAR DEFAULT ''"),
        ("project_cli_high_codex_speed", "VARCHAR DEFAULT ''"),
        ("_kanban_migration_done", "BOOLEAN DEFAULT 0"),
    ]
    try:
        with engine.connect() as conn:
            for col, col_def in _kanban_settings_columns:
                try:
                    conn.execute(text(f"ALTER TABLE settings ADD COLUMN {col} {col_def}"))
                    conn.commit()
                    logger.info(f"Added {col} column to settings table")
                except Exception as e:
                    if "duplicate column" not in str(e).lower():
                        logger.debug(f"Could not add {col} to settings: {e}")
    except Exception as e:
        logger.debug(f"Ticket Board settings migration: {e}")

    _cli_fallback_columns = [
        ("project_cli_low_fallback_backend", "VARCHAR DEFAULT ''"),
        ("project_cli_low_fallback_model", "VARCHAR DEFAULT ''"),
        ("project_cli_medium_fallback_backend", "VARCHAR DEFAULT ''"),
        ("project_cli_medium_fallback_model", "VARCHAR DEFAULT ''"),
        ("project_cli_high_fallback_backend", "VARCHAR DEFAULT ''"),
        ("project_cli_high_fallback_model", "VARCHAR DEFAULT ''"),
    ]
    try:
        with engine.connect() as conn:
            for col, col_def in _cli_fallback_columns:
                try:
                    conn.execute(text(f"ALTER TABLE settings ADD COLUMN {col} {col_def}"))
                    conn.commit()
                    logger.info(f"Added {col} column to settings table")
                except Exception as e:
                    if "duplicate column" not in str(e).lower():
                        logger.debug(f"Could not add {col} to settings: {e}")
    except Exception as e:
        logger.debug(f"CLI fallback settings migration: {e}")

    # Add color column to kanban_boards table
    try:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE kanban_boards ADD COLUMN color VARCHAR"))
                conn.commit()
                logger.info("Added color column to kanban_boards table")
            except Exception as e:
                if "duplicate column" not in str(e).lower():
                    logger.debug(f"Could not add color to kanban_boards: {e}")
    except Exception as e:
        logger.debug(f"Ticket Board color migration: {e}")

    # Add position column to kanban_boards table
    try:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE kanban_boards ADD COLUMN position INTEGER DEFAULT 0"))
                conn.commit()
                logger.info("Added position column to kanban_boards table")
            except Exception as e:
                if "duplicate column" not in str(e).lower():
                    logger.debug(f"Could not add position to kanban_boards: {e}")
    except Exception as e:
        logger.debug(f"Ticket Board position migration: {e}")

    # Initiative settings columns
    initiative_columns = [
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
    ]
    for col_name, col_type in initiative_columns:
        try:
            with Session() as session:
                session.execute(text(f"SELECT {col_name} FROM settings LIMIT 1"))
        except Exception:
            with engine.connect() as conn:
                try:
                    conn.execute(text(f"ALTER TABLE settings ADD COLUMN {col_name} {col_type}"))
                    conn.commit()
                    logger.info(f"Added {col_name} column to settings table")
                except Exception as e:
                    if "duplicate column" not in str(e).lower():
                        logger.warning(f"Could not add {col_name} column: {e}")

    # Migrate always_confirm_file_operations value to initiative_ask_file_changes
    try:
        with Session() as session:
            settings = session.query(Settings).first()
            if settings and hasattr(settings, 'always_confirm_file_operations') and hasattr(settings, 'initiative_ask_file_changes'):
                if settings.initiative_ask_file_changes is None:
                    settings.initiative_ask_file_changes = settings.always_confirm_file_operations
                    session.commit()
                    logger.info("Migrated always_confirm_file_operations -> initiative_ask_file_changes")
    except Exception as e:
        logger.debug(f"Initiative migration: {e}")

    # Add send_to_cli column to kanban_boards
    try:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE kanban_boards ADD COLUMN send_to_cli BOOLEAN DEFAULT 0"))
                conn.commit()
                logger.info("Added send_to_cli column to kanban_boards table")
            except Exception as e:
                if "duplicate column" not in str(e).lower():
                    logger.debug(f"Could not add send_to_cli to kanban_boards: {e}")
    except Exception as e:
        logger.debug(f"Ticket Board send_to_cli migration: {e}")

    # Add archived column to kanban_boards
    try:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE kanban_boards ADD COLUMN archived BOOLEAN DEFAULT 0"))
                conn.commit()
                logger.info("Added archived column to kanban_boards table")
            except Exception as e:
                if "duplicate column" not in str(e).lower():
                    logger.debug(f"Could not add archived to kanban_boards: {e}")
    except Exception as e:
        logger.debug(f"Ticket Board archived migration: {e}")

    # ── Ticket send_to_cli ──
    try:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE kanban_tickets ADD COLUMN send_to_cli BOOLEAN DEFAULT 0"))
                conn.commit()
                logger.info("Added send_to_cli column to kanban_tickets table")
            except Exception as e:
                if "duplicate column" not in str(e).lower():
                    logger.debug(f"Could not add send_to_cli to kanban_tickets: {e}")
    except Exception as e:
        logger.debug(f"Ticket Board send_to_cli migration: {e}")

    # ── Ticket Board time tracking fields on local tickets ──
    for _tcol, _ttype, _tdef in [
        ("time_estimate", "VARCHAR", "NULL"),
        ("time_spent", "VARCHAR", "NULL"),
        ("workflow_queue_position", "INTEGER", "0"),
        ("context_notes", "TEXT", "NULL"),
    ]:
        try:
            with Session() as s:
                s.execute(text(f"SELECT {_tcol} FROM kanban_tickets LIMIT 1"))
        except Exception:
            try:
                with engine.connect() as conn:
                    conn.execute(text(f"ALTER TABLE kanban_tickets ADD COLUMN {_tcol} {_ttype} DEFAULT {_tdef}"))
                    conn.commit()
                    logger.info(f"Added {_tcol} column to kanban_tickets table")
            except Exception as e:
                if "duplicate column" not in str(e).lower():
                    logger.warning(f"Could not add {_tcol} to kanban_tickets: {e}")

    # ── skin_sizes table ──
    try:
        with engine.connect() as conn:
            conn.execute(text(
                "CREATE TABLE IF NOT EXISTS skin_sizes ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "skin_slug VARCHAR NOT NULL UNIQUE, "
                "size_px INTEGER NOT NULL DEFAULT 180"
                ")"
            ))
            conn.commit()
            logger.info("Ensured skin_sizes table exists")
    except Exception as e:
        logger.debug(f"skin_sizes table migration: {e}")

    # ── Workflow–StepRunner Unification: add new columns to auto_workflow* tables ──
    # These columns were added to the SQLAlchemy models but existing databases
    # need ALTER TABLE to pick them up.

    # auto_workflows: workflow_type, chat_id, context_rules, workflow_input
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='auto_workflows'"))
            if result.fetchone():
                for col, col_def in [
                    ("workflow_type", "VARCHAR DEFAULT 'manual'"),
                    ("chat_id", "INTEGER"),
                    ("context_rules", "TEXT"),
                    ("workflow_input", "TEXT"),
                    ("run_settings", "TEXT"),
                ]:
                    try:
                        conn.execute(text(f"ALTER TABLE auto_workflows ADD COLUMN {col} {col_def}"))
                        conn.commit()
                        logger.info(f"Added {col} column to auto_workflows table")
                    except Exception as e:
                        if "duplicate column" not in str(e).lower():
                            logger.warning(f"Could not add {col} column to auto_workflows: {e}")
    except Exception as e:
        logger.debug(f"Workflow unification migration (auto_workflows): {e}")

    # auto_workflows: safety_mode, safety_frozen_scope, pre_chain, post_chain, verification_template
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='auto_workflows'"))
            if result.fetchone():
                for col, col_def in [
                    ("safety_mode", "VARCHAR"),
                    ("safety_frozen_scope", "VARCHAR"),
                    ("pre_chain", "TEXT"),
                    ("post_chain", "TEXT"),
                    ("verification_template", "VARCHAR"),
                ]:
                    try:
                        conn.execute(text(f"ALTER TABLE auto_workflows ADD COLUMN {col} {col_def}"))
                        conn.commit()
                        logger.info(f"Added {col} column to auto_workflows table")
                    except Exception as e:
                        if "duplicate column" not in str(e).lower():
                            logger.warning(f"Could not add {col} column to auto_workflows: {e}")
    except Exception as e:
        logger.debug(f"Safety and skill-chaining migration (auto_workflows): {e}")

    # auto_workflow_steps: verification, step_type, config, tool_used, routing_path
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='auto_workflow_steps'"))
            if result.fetchone():
                for col, col_def in [
                    ("verification", "TEXT"),
                    ("step_type", "VARCHAR DEFAULT 'agent_instruction'"),
                    ("config", "TEXT"),
                    ("tool_used", "VARCHAR"),
                    ("routing_path", "TEXT"),
                ]:
                    try:
                        conn.execute(text(f"ALTER TABLE auto_workflow_steps ADD COLUMN {col} {col_def}"))
                        conn.commit()
                        logger.info(f"Added {col} column to auto_workflow_steps table")
                    except Exception as e:
                        if "duplicate column" not in str(e).lower():
                            logger.warning(f"Could not add {col} column to auto_workflow_steps: {e}")
    except Exception as e:
        logger.debug(f"Workflow unification migration (auto_workflow_steps): {e}")

    # Handle database migration for load_on_startup column in settings table
    try:
        with Session() as session:
            session.execute(text("SELECT load_on_startup FROM settings LIMIT 1"))
    except Exception:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE settings ADD COLUMN load_on_startup BOOLEAN DEFAULT 1"))
                conn.commit()
                logger.info("Added load_on_startup column to settings table")
            except Exception as e:
                logger.warning(f"Could not add load_on_startup column: {e}")

    # Dictation hotkey settings
    for _col_name, _col_type in [
        ("dictation_hotkey_enabled", f"BOOLEAN DEFAULT {int(HOTKEY_DEFAULTS['dictation_hotkey_enabled'])}"),
        ("dictation_hotkey_modifier", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['dictation_hotkey_modifier']}'"),
        ("dictation_hotkey_key", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['dictation_hotkey_key']}'"),
        ("ticket_dictation_hotkey_enabled", f"BOOLEAN DEFAULT {int(HOTKEY_DEFAULTS['ticket_dictation_hotkey_enabled'])}"),
        ("ticket_dictation_hotkey_modifier", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['ticket_dictation_hotkey_modifier']}'"),
        ("ticket_dictation_hotkey_key", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['ticket_dictation_hotkey_key']}'"),
        ("instant_dictation", "BOOLEAN DEFAULT 1"),
        ("dictation_ticket_use_llm", "BOOLEAN DEFAULT 1"),
        ("dictation_ticket_model", "VARCHAR DEFAULT 'qwen2.5:0.5b'"),
        ("dictation_ticket_timeout", "VARCHAR DEFAULT '1.2'"),
        ("dictation_ticket_prompt", "TEXT DEFAULT ''"),
    ]:
        try:
            with Session() as session:
                session.execute(text(f"SELECT {_col_name} FROM settings LIMIT 1"))
        except Exception:
            with engine.connect() as conn:
                try:
                    conn.execute(text(f"ALTER TABLE settings ADD COLUMN {_col_name} {_col_type}"))
                    conn.commit()
                    logger.info("Added %s column to settings table", _col_name)
                except Exception as e:
                    logger.warning("Could not add %s column: %s", _col_name, e)

    try:
        with engine.connect() as conn:
            conn.execute(text(
                "UPDATE settings "
                "SET dictation_hotkey_modifier = :plain_mod "
                "WHERE dictation_hotkey_modifier = :ticket_mod "
                "AND COALESCE(dictation_hotkey_key, '') = '' "
                "AND ticket_dictation_hotkey_modifier = :ticket_mod "
                "AND COALESCE(ticket_dictation_hotkey_key, '') = ''"
            ), {
                "plain_mod": HOTKEY_DEFAULTS["dictation_hotkey_modifier"],
                "ticket_mod": HOTKEY_DEFAULTS["ticket_dictation_hotkey_modifier"],
            })
            conn.commit()
    except Exception as e:
        logger.debug("Could not separate dictation and ticket dictation defaults: %s", e)

    # Recording shortcut settings
    for _col_name, _col_type in [
        ("recording_hotkey_enabled", "BOOLEAN DEFAULT 1"),
        ("recording_hotkey_modifier", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['recording_hotkey_modifier']}'"),
        ("recording_hotkey_key", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['recording_hotkey_key']}'"),
        ("skin_nav_hotkey_previous_modifier", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['skin_nav_hotkey_previous_modifier']}'"),
        ("skin_nav_hotkey_previous_key", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['skin_nav_hotkey_previous_key']}'"),
        ("skin_nav_hotkey_next_modifier", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['skin_nav_hotkey_next_modifier']}'"),
        ("skin_nav_hotkey_next_key", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['skin_nav_hotkey_next_key']}'"),
        ("skin_select_hotkey_modifier", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['skin_select_hotkey_modifier']}'"),
        ("web_hotkey_chat_modifier", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['web_hotkey_chat_modifier']}'"),
        ("web_hotkey_chat_key", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['web_hotkey_chat_key']}'"),
        ("web_hotkey_projects_modifier", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['web_hotkey_projects_modifier']}'"),
        ("web_hotkey_projects_key", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['web_hotkey_projects_key']}'"),
        ("web_hotkey_actions_modifier", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['web_hotkey_actions_modifier']}'"),
        ("web_hotkey_actions_key", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['web_hotkey_actions_key']}'"),
        ("web_hotkey_snippets_modifier", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['web_hotkey_snippets_modifier']}'"),
        ("web_hotkey_snippets_key", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['web_hotkey_snippets_key']}'"),
        ("web_hotkey_workflows_modifier", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['web_hotkey_workflows_modifier']}'"),
        ("web_hotkey_workflows_key", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['web_hotkey_workflows_key']}'"),
        ("web_hotkey_automations_modifier", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['web_hotkey_automations_modifier']}'"),
        ("web_hotkey_automations_key", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['web_hotkey_automations_key']}'"),
        ("web_hotkey_ticket_board_modifier", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['web_hotkey_ticket_board_modifier']}'"),
        ("web_hotkey_ticket_board_key", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['web_hotkey_ticket_board_key']}'"),
        ("web_hotkey_irc_modifier", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['web_hotkey_irc_modifier']}'"),
        ("web_hotkey_irc_key", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['web_hotkey_irc_key']}'"),
        ("web_hotkey_preferences_modifier", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['web_hotkey_preferences_modifier']}'"),
        ("web_hotkey_preferences_key", f"VARCHAR DEFAULT '{HOTKEY_DEFAULTS['web_hotkey_preferences_key']}'"),
    ]:
        try:
            with Session() as session:
                session.execute(text(f"SELECT {_col_name} FROM settings LIMIT 1"))
        except Exception:
            with engine.connect() as conn:
                try:
                    conn.execute(text(f"ALTER TABLE settings ADD COLUMN {_col_name} {_col_type}"))
                    conn.commit()
                    logger.info("Added %s column to settings table", _col_name)
                except Exception as e:
                    logger.warning("Could not add %s column: %s", _col_name, e)

    # Update existing settings rows to new default skin/size hotkeys:
    # - skin navigation: Control+Command + Left/Right
    # - size controls: Control+Command + Down/Up
    try:
        with engine.connect() as conn:
            conn.execute(text(
                "UPDATE settings "
                "SET skin_nav_hotkey_previous_modifier = :mod, skin_nav_hotkey_previous_key = :prev_key "
                "WHERE skin_nav_hotkey_previous_modifier = 'option_command' AND skin_nav_hotkey_previous_key = 'left_arrow'"
            ), {"mod": HOTKEY_DEFAULTS["skin_nav_hotkey_previous_modifier"], "prev_key": HOTKEY_DEFAULTS["skin_nav_hotkey_previous_key"]})
            conn.execute(text(
                "UPDATE settings "
                "SET skin_nav_hotkey_next_modifier = :mod, skin_nav_hotkey_next_key = :next_key "
                "WHERE skin_nav_hotkey_next_modifier = 'option_command' AND skin_nav_hotkey_next_key = 'right_arrow'"
            ), {"mod": HOTKEY_DEFAULTS["skin_nav_hotkey_next_modifier"], "next_key": HOTKEY_DEFAULTS["skin_nav_hotkey_next_key"]})
            conn.execute(text(
                "UPDATE settings "
                "SET oracle_size_hotkey_decrease_modifier = :mod, oracle_size_hotkey_decrease_key = :down_key "
                "WHERE oracle_size_hotkey_decrease_modifier = 'option_command' AND oracle_size_hotkey_decrease_key = 'left_bracket'"
            ), {"mod": HOTKEY_DEFAULTS["oracle_size_hotkey_decrease_modifier"], "down_key": HOTKEY_DEFAULTS["oracle_size_hotkey_decrease_key"]})
            conn.execute(text(
                "UPDATE settings "
                "SET oracle_size_hotkey_increase_modifier = :mod, oracle_size_hotkey_increase_key = :up_key "
                "WHERE oracle_size_hotkey_increase_modifier = 'option_command' AND oracle_size_hotkey_increase_key = 'right_bracket'"
            ), {"mod": HOTKEY_DEFAULTS["oracle_size_hotkey_increase_modifier"], "up_key": HOTKEY_DEFAULTS["oracle_size_hotkey_increase_key"]})
            conn.commit()
            logger.info("Updated existing shortcut rows to new Control+Command arrow defaults")
    except Exception as e:
        logger.warning("Could not update existing shortcut rows to new defaults: %s", e)

    # Handle database migration for whatsapp_messages table
    try:
        with Session() as session:
            session.execute(text("SELECT id FROM whatsapp_messages LIMIT 1"))
    except Exception:
        # Table doesn't exist, create it
        try:
            with engine.connect() as conn:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS whatsapp_messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        message_id VARCHAR NOT NULL UNIQUE,
                        jid VARCHAR NOT NULL,
                        jid_phone VARCHAR,
                        chat_type VARCHAR,
                        sender_jid VARCHAR,
                        sender_phone VARCHAR,
                        sender_push_name VARCHAR,
                        text TEXT,
                        caption TEXT,
                        media_type VARCHAR,
                        media_mime_type VARCHAR,
                        media_filename VARCHAR,
                        media_local_path VARCHAR,
                        media_file_length INTEGER,
                        media_duration INTEGER,
                        whatsapp_timestamp INTEGER,
                        from_me BOOLEAN DEFAULT 0,
                        raw_data TEXT,
                        processed BOOLEAN DEFAULT 0,
                        processed_date DATETIME,
                        snapshot_group VARCHAR,
                        agent_chat_id INTEGER,
                        created_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                        modified_date DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                conn.commit()
                logger.info("Created whatsapp_messages table")
        except Exception as e:
            logger.warning(f"Could not create whatsapp_messages table: {e}")

    # ── WhatsApp phone links table ──────────────────────────────────────────
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='whatsapp_phone_links'"))
            if not result.fetchone():
                conn.execute(text("""
                    CREATE TABLE whatsapp_phone_links (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        board_id INTEGER NOT NULL REFERENCES kanban_boards(id),
                        phone_jid VARCHAR NOT NULL,
                        phone_number VARCHAR,
                        contact_name VARCHAR,
                        auto_snapshot BOOLEAN DEFAULT 0,
                        created_date DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                conn.commit()
                logger.info("Created whatsapp_phone_links table")
    except Exception as e:
        logger.warning(f"Could not create whatsapp_phone_links table: {e}")

    # ── WhatsApp compose drafts (unsent reply text per chat) ─────────────────
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='whatsapp_compose_drafts'"))
            if not result.fetchone():
                conn.execute(text("""
                    CREATE TABLE whatsapp_compose_drafts (
                        jid_phone VARCHAR PRIMARY KEY,
                        jid VARCHAR,
                        chat_type VARCHAR DEFAULT 'private',
                        contact_name VARCHAR,
                        draft_text TEXT NOT NULL DEFAULT '',
                        source VARCHAR DEFAULT 'user',
                        board_id INTEGER,
                        updated_date DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                conn.commit()
                logger.info("Created whatsapp_compose_drafts table")
    except Exception as e:
        logger.warning(f"Could not create whatsapp_compose_drafts table: {e}")

    # ── Add whatsapp_message_id / whatsapp_message_wa_id to kanban_tickets ──
    for _wcol, _wtype, _wdef in [
        ("whatsapp_message_id", "INTEGER", "NULL"),
        ("whatsapp_message_wa_id", "VARCHAR", "NULL"),
    ]:
        try:
            with Session() as s:
                s.execute(text(f"SELECT {_wcol} FROM kanban_tickets LIMIT 1"))
        except Exception:
            try:
                with engine.connect() as conn:
                    conn.execute(text(f"ALTER TABLE kanban_tickets ADD COLUMN {_wcol} {_wtype} DEFAULT {_wdef}"))
                    conn.commit()
                    logger.info(f"Added {_wcol} column to kanban_tickets table")
            except Exception as e:
                if "duplicate column" not in str(e).lower():
                    logger.warning(f"Could not add {_wcol} to kanban_tickets: {e}")

    # ── WhatsApp messages: add missing columns to existing tables ──
    for _wcol, _wtype, _wdef in [
        ("snapshot_group", "VARCHAR", "NULL"),
        ("agent_chat_id", "INTEGER", "NULL"),
        ("media_duration", "INTEGER", "NULL"),
    ]:
        try:
            with Session() as s:
                s.execute(text(f"SELECT {_wcol} FROM whatsapp_messages LIMIT 1"))
        except Exception:
            try:
                with engine.connect() as conn:
                    conn.execute(text(f"ALTER TABLE whatsapp_messages ADD COLUMN {_wcol} {_wtype} DEFAULT {_wdef}"))
                    conn.commit()
                    logger.info(f"Added {_wcol} column to whatsapp_messages table")
            except Exception as e:
                if "duplicate column" not in str(e).lower():
                    logger.warning(f"Could not add {_wcol} to whatsapp_messages: {e}")

    # ── Masko (AI skin generation) provider columns ──
    for _wcol, _wtype, _wdef in [
        ("masko_enabled", "BOOLEAN", "0"),
        ("masko_key", "VARCHAR", "''"),
        ("tensology_enabled", "BOOLEAN", "0"),
        ("tensology_url", "VARCHAR", "'https://www.tensology.com'"),
        ("tensology_key", "VARCHAR", "''"),
    ]:
        try:
            with Session() as s:
                s.execute(text(f"SELECT {_wcol} FROM settings LIMIT 1"))
        except Exception:
            try:
                with engine.connect() as conn:
                    conn.execute(text(f"ALTER TABLE settings ADD COLUMN {_wcol} {_wtype} DEFAULT {_wdef}"))
                    conn.commit()
                    logger.info(f"Added {_wcol} column to settings table")
            except Exception as e:
                if "duplicate column" not in str(e).lower():
                    logger.warning(f"Could not add {_wcol} column to settings: {e}")

    # ── Audit fixes: new columns ────────────────────────────────────────────────
    # KanbanTicket: workflow_status mirrors latest run status; parent_ticket_id for subagent hierarchy
    for _wcol, _wtype, _wdef in [
        ("workflow_status", "VARCHAR", "NULL"),
        ("parent_ticket_id", "INTEGER", "NULL"),
        ("source_chat_id", "INTEGER", "NULL"),
        ("complexity", "VARCHAR", "'medium'"),
        ("source_provider", "VARCHAR", "NULL"),
        ("source_external_id", "VARCHAR", "NULL"),
        ("source_thread_id", "VARCHAR", "NULL"),
        ("source_contact", "VARCHAR", "NULL"),
        ("source_url", "VARCHAR", "NULL"),
        ("source_label", "VARCHAR", "NULL"),
    ]:
        try:
            with Session() as s:
                s.execute(text(f"SELECT {_wcol} FROM kanban_tickets LIMIT 1"))
        except Exception:
            try:
                with engine.connect() as conn:
                    conn.execute(text(f"ALTER TABLE kanban_tickets ADD COLUMN {_wcol} {_wtype} DEFAULT {_wdef}"))
                    conn.commit()
                    logger.info(f"Added {_wcol} column to kanban_tickets table")
            except Exception as e:
                if "duplicate column" not in str(e).lower():
                    logger.warning(f"Could not add {_wcol} to kanban_tickets: {e}")

    # AutoWorkflowRun: parent_run_id for subagent run hierarchy
    try:
        with Session() as s:
            s.execute(text("SELECT parent_run_id FROM auto_workflow_runs LIMIT 1"))
    except Exception:
        try:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE auto_workflow_runs ADD COLUMN parent_run_id INTEGER DEFAULT NULL"))
                conn.commit()
                logger.info("Added parent_run_id column to auto_workflow_runs table")
        except Exception as e:
            if "duplicate column" not in str(e).lower():
                logger.warning(f"Could not add parent_run_id to auto_workflow_runs: {e}")

    # ── Indexes for high-frequency query patterns ───────────────────────────────
    _index_ddl = [
        "CREATE INDEX IF NOT EXISTS ix_kanban_tickets_lane_id ON kanban_tickets (lane_id)",
        "CREATE INDEX IF NOT EXISTS ix_kanban_tickets_position ON kanban_tickets (position)",
        "CREATE INDEX IF NOT EXISTS ix_kanban_lanes_board_id ON kanban_lanes (board_id)",
        "CREATE INDEX IF NOT EXISTS ix_autoworkflowrun_workflow_id ON auto_workflow_runs (workflow_id)",
        "CREATE INDEX IF NOT EXISTS ix_autoworkflowrun_ticket_id ON auto_workflow_runs (ticket_id)",
        "CREATE INDEX IF NOT EXISTS ix_autoworkflowrun_board_id ON auto_workflow_runs (board_id)",
        "CREATE INDEX IF NOT EXISTS ix_autoworkflowrun_status ON auto_workflow_runs (status)",
    ]
    try:
        with engine.connect() as conn:
            for _ddl in _index_ddl:
                try:
                    conn.execute(text(_ddl))
                    conn.commit()
                except Exception as _ie:
                    logger.debug("Index migration skipped: %s", _ie)
    except Exception as _idx_err:
        logger.warning("Index migration block failed: %s", _idx_err)

    # ── Ticket audit entries table (normalized per-ticket audit rows) ──
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kanban_ticket_audit_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER NOT NULL REFERENCES kanban_tickets(id),
                    run_id INTEGER REFERENCES auto_workflow_runs(id),
                    step_id INTEGER REFERENCES auto_workflow_steps(id),
                    step_result_id INTEGER REFERENCES auto_workflow_step_results(id),
                    execution_lane VARCHAR NOT NULL DEFAULT 'cursor',
                    status VARCHAR NOT NULL DEFAULT 'pending',
                    final_verdict VARCHAR,
                    summary TEXT,
                    details TEXT,
                    created_date DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_ticket_audit_ticket_created "
                "ON kanban_ticket_audit_entries (ticket_id, created_date)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_ticket_audit_run_step "
                "ON kanban_ticket_audit_entries (run_id, step_id)"
            ))
            conn.commit()
            logger.info("Ensured kanban_ticket_audit_entries table exists")
    except Exception as e:
        logger.warning("Could not create kanban_ticket_audit_entries table: %s", e)

    # ── BUG-4: Add board_id and ticket_id to AutoWorkflowRun for concurrency ──
    for _wcol, _wtype, _wdef in [
        ("board_id", "INTEGER", "NULL"),
        ("ticket_id", "INTEGER", "NULL"),
    ]:
        try:
            with Session() as s:
                s.execute(text(f"SELECT {_wcol} FROM auto_workflow_runs LIMIT 1"))
        except Exception:
            try:
                with engine.connect() as conn:
                    conn.execute(text(f"ALTER TABLE auto_workflow_runs ADD COLUMN {_wcol} {_wtype} DEFAULT {_wdef}"))
                    conn.commit()
                    logger.info(f"Added {_wcol} column to auto_workflow_runs table")
            except Exception as e:
                if "duplicate column" not in str(e).lower():
                    logger.warning(f"Could not add {_wcol} to auto_workflow_runs: {e}")

    # ── Drop legacy template/job-card Workflow tables (superseded by auto_workflows) ──
    try:
        with engine.connect() as conn:
            inspector = inspect(engine)
            if inspector.has_table("trello_tickets"):
                cols = [c["name"] for c in inspector.get_columns("trello_tickets")]
                if "workflow_id" in cols:
                    dropped = False
                    for pragmas in (
                        (),
                        ("PRAGMA legacy_alter_table=ON",),
                    ):
                        try:
                            for p in pragmas:
                                conn.execute(text(p))
                            conn.execute(text("ALTER TABLE trello_tickets DROP COLUMN workflow_id"))
                            conn.commit()
                            dropped = True
                            logger.info("Dropped legacy trello_tickets.workflow_id column")
                            break
                        except Exception:
                            conn.rollback()
                            continue
                    if not dropped:
                        # SQLite FK metadata can block DROP COLUMN; rebuild without workflow_id
                        try:
                            conn.execute(text("PRAGMA foreign_keys=OFF"))
                            conn.execute(text("""
                                CREATE TABLE trello_tickets__wf_drop (
                                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                                    trello_card_id VARCHAR NOT NULL UNIQUE,
                                    title VARCHAR,
                                    description TEXT,
                                    chat_id INTEGER REFERENCES chats(id),
                                    members TEXT,
                                    attachments TEXT,
                                    status VARCHAR,
                                    created_date DATETIME,
                                    modified_date DATETIME
                                )
                            """))
                            conn.execute(text("""
                                INSERT INTO trello_tickets__wf_drop (
                                    id, trello_card_id, title, description, chat_id,
                                    members, attachments, status, created_date, modified_date
                                )
                                SELECT id, trello_card_id, title, description, chat_id,
                                       members, attachments, status, created_date, modified_date
                                FROM trello_tickets
                            """))
                            conn.execute(text("DROP TABLE trello_tickets"))
                            conn.execute(text(
                                "ALTER TABLE trello_tickets__wf_drop RENAME TO trello_tickets"
                            ))
                            conn.commit()
                            conn.execute(text("PRAGMA foreign_keys=ON"))
                            logger.info(
                                "Rebuilt trello_tickets without workflow_id (SQLite fallback)"
                            )
                        except Exception as e2:
                            try:
                                conn.execute(text("PRAGMA foreign_keys=ON"))
                            except Exception:
                                pass
                            logger.warning(
                                "Could not remove trello_tickets.workflow_id: %s",
                                e2,
                            )
            conn.execute(text("DROP TABLE IF EXISTS workflows"))
            conn.commit()
            conn.execute(text("DROP TABLE IF EXISTS workflow_projects"))
            conn.commit()
            logger.info("Dropped legacy workflows / workflow_projects tables if present")
    except Exception as e:
        logger.warning("Legacy Workflow table cleanup failed: %s", e)

    # Snippets are owned by the DecisionsAI app. Remote control clients should
    # read/write hotkey metadata through the app API rather than browser storage.
    try:
        with engine.connect() as conn:
            inspector = inspect(engine)
            if inspector.has_table("snippets"):
                cols = [c["name"] for c in inspector.get_columns("snippets")]
                if "remote_hotkey" not in cols:
                    conn.execute(text("ALTER TABLE snippets ADD COLUMN remote_hotkey VARCHAR DEFAULT ''"))
                    conn.commit()
                    logger.info("Added remote_hotkey column to snippets table")
    except Exception as e:
        logger.warning("Could not add snippets.remote_hotkey column: %s", e)

    # Board Hermes policy overrides
    try:
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE kanban_boards ADD COLUMN orchestrator_policy TEXT"))
                conn.commit()
                logger.info("Added orchestrator_policy column to kanban_boards table")
            except Exception as e:
                if "duplicate column" not in str(e).lower():
                    logger.debug(f"Could not add orchestrator_policy to kanban_boards: {e}")
    except Exception as e:
        logger.debug(f"Ticket Board orchestrator_policy migration: {e}")

    # Hermes learned rules table
    try:
        from distr.core.orchestrator import ensure_orchestrator_tables

        ensure_orchestrator_tables()
        logger.info("Ensured orchestrator learned rules tables exist")
    except Exception as e:
        logger.debug(f"Orchestrator learned rules migration: {e}")

    _migrate_legacy_hermes_schema_to_orchestrator(engine)

    try:
        from distr.core.db import schedule_blocks as _schedule_blocks  # noqa: F401

        Base.metadata.create_all(engine)
        logger.info("Ensured schedule_blocks table exists")
    except Exception as e:
        logger.debug(f"schedule_blocks migration: {e}")


def _migrate_legacy_hermes_schema_to_orchestrator(engine) -> None:
    """Merge legacy Hermes data into the model-agnostic orchestrator schema."""
    table_renames = [
        ("hermes_events", "orchestrator_events"),
        ("hermes_user_memories", "orchestrator_user_memories"),
        ("hermes_machine_activity", "orchestrator_machine_activity"),
        ("hermes_maintenance_state", "orchestrator_maintenance_state"),
        ("hermes_validation_records", "orchestrator_validation_records"),
        ("hermes_visual_baseline_sets", "orchestrator_visual_baseline_sets"),
        ("hermes_visual_baseline_screens", "orchestrator_visual_baseline_screens"),
        ("hermes_correction_attempts", "orchestrator_correction_attempts"),
        ("hermes_learned_rules", "orchestrator_learned_rules"),
    ]
    settings_renames = [
        ("hermes_enabled", "orchestrator_enabled"),
        ("hermes_orchestrator_provider", "orchestrator_provider"),
        ("hermes_orchestrator_model", "orchestrator_model"),
        ("hermes_validator_provider", "orchestrator_validator_provider"),
        ("hermes_validator_model", "orchestrator_validator_model"),
        ("hermes_correction_provider", "orchestrator_correction_provider"),
        ("hermes_correction_model", "orchestrator_correction_model"),
        ("hermes_memory_export_enabled", "orchestrator_memory_export_enabled"),
    ]
    reference_offsets = {
        ("hermes_events", "parent_event_id"): "hermes_events",
        ("hermes_visual_baseline_screens", "baseline_set_id"): "hermes_visual_baseline_sets",
        ("hermes_correction_attempts", "validation_record_id"): "hermes_validation_records",
    }
    try:
        with engine.begin() as conn:
            existing_tables = {
                row[0]
                for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            }
            offsets = {
                old_name: int(
                    conn.execute(text(f'SELECT COALESCE(MAX(id), 0) FROM "{new_name}"')).scalar()
                    or 0
                )
                for old_name, new_name in table_renames
                if old_name in existing_tables and new_name in existing_tables
            }
            for old_name, new_name in table_renames:
                if old_name in existing_tables and new_name not in existing_tables:
                    conn.execute(text(f'ALTER TABLE "{old_name}" RENAME TO "{new_name}"'))
                    existing_tables.discard(old_name)
                    existing_tables.add(new_name)
                    logger.info("Renamed table %s -> %s", old_name, new_name)
                    continue
                if old_name not in existing_tables or new_name not in existing_tables:
                    continue

                old_columns = [row[1] for row in conn.execute(text(f'PRAGMA table_info("{old_name}")'))]
                new_columns = {row[1] for row in conn.execute(text(f'PRAGMA table_info("{new_name}")'))}
                missing_columns = set(old_columns) - new_columns
                if missing_columns:
                    raise RuntimeError(
                        f"Cannot merge {old_name}: current schema is missing "
                        f"{sorted(missing_columns)}"
                    )
                columns = [column for column in old_columns if column in new_columns]
                if columns:
                    expressions = []
                    for column in columns:
                        if column == "id":
                            expressions.append(f'"id" + {offsets.get(old_name, 0)}')
                        elif (old_name, column) in reference_offsets:
                            parent_table = reference_offsets[(old_name, column)]
                            parent_offset = offsets.get(parent_table, 0)
                            expressions.append(
                                f'CASE WHEN "{column}" IS NULL THEN NULL '
                                f'ELSE "{column}" + {parent_offset} END'
                            )
                        else:
                            expressions.append(f'"{column}"')
                    quoted_columns = ", ".join(f'"{column}"' for column in columns)
                    conn.execute(
                        text(
                            f'INSERT OR IGNORE INTO "{new_name}" ({quoted_columns}) '
                            f'SELECT {", ".join(expressions)} FROM "{old_name}"'
                        )
                    )

            # Re-link imported event trees by stable uid when a duplicate parent
            # already existed in the current table and therefore kept its old id.
            if "hermes_events" in existing_tables and "orchestrator_events" in existing_tables:
                conn.execute(text("""
                    UPDATE orchestrator_events AS child
                    SET parent_event_id = (
                        SELECT current_parent.id
                        FROM hermes_events AS legacy_child
                        JOIN hermes_events AS legacy_parent
                          ON legacy_parent.id = legacy_child.parent_event_id
                        JOIN orchestrator_events AS current_parent
                          ON current_parent.event_uid = legacy_parent.event_uid
                        WHERE legacy_child.event_uid = child.event_uid
                    )
                    WHERE child.event_uid IN (
                        SELECT event_uid FROM hermes_events WHERE parent_event_id IS NOT NULL
                    )
                """))

            # Drop children before parents after every copy has succeeded.
            for old_name, _new_name in reversed(table_renames):
                if old_name in existing_tables:
                    conn.execute(text(f'DROP TABLE "{old_name}"'))
                    logger.info("Merged and removed legacy table %s", old_name)

            settings_cols = set()
            if "settings" in existing_tables:
                settings_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(settings)"))}
                for old_col, new_col in settings_renames:
                    if old_col in settings_cols and new_col not in settings_cols:
                        try:
                            conn.execute(text(f'ALTER TABLE settings RENAME COLUMN "{old_col}" TO "{new_col}"'))
                            settings_cols.discard(old_col)
                            settings_cols.add(new_col)
                            logger.info("Renamed settings column %s -> %s", old_col, new_col)
                        except Exception as exc:
                            logger.debug("Could not rename settings.%s: %s", old_col, exc)
                    elif old_col in settings_cols and new_col in settings_cols:
                        is_boolean = old_col in {"hermes_enabled", "hermes_memory_export_enabled"}
                        empty_current = f'"{new_col}" IS NULL OR "{new_col}" = {1 if old_col == "hermes_enabled" else 0}' if is_boolean else f'"{new_col}" IS NULL OR "{new_col}" = \'\''
                        useful_legacy = f'"{old_col}" IS NOT NULL AND "{old_col}" != {1 if old_col == "hermes_enabled" else 0}' if is_boolean else f'"{old_col}" IS NOT NULL AND "{old_col}" != \'\''
                        conn.execute(text(
                            f'UPDATE settings SET "{new_col}" = "{old_col}" '
                            f'WHERE ({empty_current}) AND ({useful_legacy})'
                        ))
                        conn.execute(text(f'ALTER TABLE settings DROP COLUMN "{old_col}"'))
                        settings_cols.discard(old_col)
                        logger.info("Merged and removed settings.%s", old_col)

            if "kanban_boards" in existing_tables:
                board_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(kanban_boards)"))}
                if "hermes_policy" in board_cols and "orchestrator_policy" not in board_cols:
                    try:
                        conn.execute(text('ALTER TABLE kanban_boards RENAME COLUMN hermes_policy TO orchestrator_policy'))
                        logger.info("Renamed kanban_boards.hermes_policy -> orchestrator_policy")
                    except Exception as exc:
                        logger.debug("Could not rename kanban_boards.hermes_policy: %s", exc)
                elif "hermes_policy" in board_cols and "orchestrator_policy" in board_cols:
                    conn.execute(text("""
                        UPDATE kanban_boards
                        SET orchestrator_policy = hermes_policy
                        WHERE (orchestrator_policy IS NULL OR orchestrator_policy = '')
                          AND hermes_policy IS NOT NULL AND hermes_policy != ''
                    """))
                    conn.execute(text("ALTER TABLE kanban_boards DROP COLUMN hermes_policy"))
                    logger.info("Merged and removed kanban_boards.hermes_policy")
    except Exception as exc:
        logger.warning("Legacy Hermes schema migration failed: %s", exc, exc_info=True)

    try:
        from distr.core.automation.scheduler import ensure_automation_schema

        ensure_automation_schema()
    except Exception as exc:
        logger.warning("Automation schema migration failed: %s", exc)

    # OpenAI / ElevenLabs selectable TTS model settings (+ optional OpenAI STT hints)
    _openai_elevenlabs_tts_columns = [
        ("openai_tts_model", "VARCHAR DEFAULT 'tts-1'"),
        ("openai_tts_instructions", "VARCHAR DEFAULT ''"),
        ("elevenlabs_tts_model", "VARCHAR DEFAULT 'eleven_flash_v2_5'"),
        ("openai_stt_prompt", "VARCHAR DEFAULT ''"),
        ("openai_stt_noise_reduction", "VARCHAR DEFAULT ''"),
    ]
    try:
        with engine.connect() as conn:
            for col, col_def in _openai_elevenlabs_tts_columns:
                try:
                    conn.execute(text(f"ALTER TABLE settings ADD COLUMN {col} {col_def}"))
                    conn.commit()
                    logger.info("Added %s column to settings table", col)
                except Exception as e:
                    if "duplicate column" not in str(e).lower():
                        logger.debug("Could not add %s to settings: %s", col, e)
    except Exception as e:
        logger.debug("OpenAI/ElevenLabs TTS model settings migration: %s", e)
