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
        with Session() as session:
            settings = session.query(Settings).first()
            if settings:
                # Check if we have old Jira/Trello columns with data
                has_jira_data = False
                has_trello_data = False
                
                try:
                    # Try to read old columns (they may not exist in newer installs)
                    inspector = inspect(engine)
                    columns = [col['name'] for col in inspector.get_columns('settings')]
                    
                    if 'jira_server_url' in columns:
                        try:
                            jira_url = getattr(settings, 'jira_server_url', None) or ''
                            jira_email = getattr(settings, 'jira_email', None) or ''
                            jira_token = getattr(settings, 'jira_api_token', None) or ''
                            has_jira_data = bool(jira_url.strip() and jira_email.strip() and jira_token.strip())
                        except Exception:
                            has_jira_data = False
                    else:
                        has_jira_data = False
                    
                    if 'trello_api_key' in columns:
                        try:
                            trello_key = getattr(settings, 'trello_api_key', None) or ''
                            trello_token = getattr(settings, 'trello_api_token', None) or ''
                            has_trello_data = bool(trello_key.strip() and trello_token.strip())
                        except Exception:
                            has_trello_data = False
                    else:
                        has_trello_data = False
                except Exception as e:
                    logger.debug(f"Could not check old columns: {e}")
                    has_jira_data = False
                    has_trello_data = False
                
                # Load existing connected_accounts
                connected_accounts = []
                if settings.connected_accounts:
                    try:
                        if isinstance(settings.connected_accounts, str):
                            connected_accounts = json.loads(settings.connected_accounts)
                        else:
                            connected_accounts = settings.connected_accounts
                        if not isinstance(connected_accounts, list):
                            connected_accounts = [connected_accounts] if isinstance(connected_accounts, dict) else []
                    except Exception as e:
                        logger.warning(f"Failed to parse connected_accounts: {e}")
                        connected_accounts = []
                
                # Migrate Jira account if exists
                if has_jira_data:
                    # Check if Jira account already exists in connected_accounts
                    jira_exists = any(
                        isinstance(acc, dict) and acc.get('provider') == 'jira'
                        for acc in connected_accounts
                    )
                    
                    if not jira_exists:
                        try:
                            jira_account = {
                                'provider': 'jira',
                                'name': 'Default Jira Account',
                                'server_url': getattr(settings, 'jira_server_url', '') or '',
                                'email': getattr(settings, 'jira_email', '') or '',
                                'api_token': getattr(settings, 'jira_api_token', '') or '',
                                'is_valid': bool(getattr(settings, 'is_jira_valid', False)),
                                'created_at': datetime.utcnow().isoformat()
                            }
                            connected_accounts.append(jira_account)
                            logger.info("Migrated Jira account to connected_accounts")
                        except Exception as e:
                            logger.warning(f"Could not migrate Jira account: {e}")
                
                # Migrate Trello account if exists
                if has_trello_data:
                    # Check if Trello account already exists in connected_accounts
                    trello_exists = any(
                        isinstance(acc, dict) and acc.get('provider') == 'trello'
                        for acc in connected_accounts
                    )
                    
                    if not trello_exists:
                        try:
                            trello_account = {
                                'provider': 'trello',
                                'name': 'Default Trello Account',
                                'api_key': getattr(settings, 'trello_api_key', '') or '',
                                'api_token': getattr(settings, 'trello_api_token', '') or '',
                                'is_valid': bool(getattr(settings, 'is_trello_valid', False)),
                                'created_at': datetime.utcnow().isoformat()
                            }
                            connected_accounts.append(trello_account)
                            logger.info("Migrated Trello account to connected_accounts")
                        except Exception as e:
                            logger.warning(f"Could not migrate Trello account: {e}")
                
                # Save updated connected_accounts if we migrated anything
                if has_jira_data or has_trello_data:
                    settings.connected_accounts = json.dumps(connected_accounts)
                    session.commit()
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

    # Step Runner scheduled sessions (add columns if table exists and columns missing)
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
        logger.debug(f"Step Runner migration: {e}")

    # Step Runner: timezone, schedule_time, step_runner_runs table
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
        logger.debug(f"Step Runner migration 2: {e}")

    # Step Runner: add verification column to steps
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
        logger.debug(f"Step Runner migration 3: {e}")

    # Step Runner: add step_type, config, code columns to step_runner_steps
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
        logger.debug(f"Step Runner migration 4 (step_type/config/code): {e}")

    # Step Runner: add context_rules and workflow_input columns to step_runner_sessions
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
        logger.debug(f"Step Runner migration 5 (context_rules/workflow_input): {e}")

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

    # Kanban boards: add agent check-in columns
    _kanban_agent_columns = [
        ("agent_enabled", "BOOLEAN DEFAULT 0"),
        ("agent_frequency", "VARCHAR DEFAULT 'daily'"),
        ("agent_time", "VARCHAR DEFAULT '09:00'"),
        ("agent_days", "TEXT DEFAULT '[]'"),
        ("agent_monthly_day", "INTEGER DEFAULT 1"),
        ("agent_orchestrator_provider", "VARCHAR DEFAULT ''"),
        ("agent_orchestrator_model", "VARCHAR DEFAULT ''"),
        ("agent_coder_provider", "VARCHAR DEFAULT ''"),
        ("agent_coder_model", "VARCHAR DEFAULT ''"),
        ("agent_sub_provider", "VARCHAR DEFAULT ''"),
        ("agent_sub_model", "VARCHAR DEFAULT ''"),
        ("agent_source_lane", "VARCHAR DEFAULT ''"),
        ("agent_done_lane", "VARCHAR DEFAULT ''"),
        ("default_workflow_id", "INTEGER DEFAULT NULL"),
        ("default_project_id", "INTEGER DEFAULT NULL"),
        ("default_snippet_id", "INTEGER DEFAULT NULL"),
        ("default_action_id", "INTEGER DEFAULT NULL"),
    ]
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='kanban_boards'"))
            if result.fetchone():
                for col, col_def in _kanban_agent_columns:
                    try:
                        conn.execute(text(f"ALTER TABLE kanban_boards ADD COLUMN {col} {col_def}"))
                        conn.commit()
                        logger.info(f"Added {col} column to kanban_boards table")
                    except Exception as e:
                        if "duplicate column" not in str(e).lower():
                            logger.warning(f"Could not add {col} column to kanban_boards: {e}")
    except Exception as e:
        logger.debug(f"Kanban agent columns migration: {e}")

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

    # Seed empty workflows with default steps
    try:
        from distr.core.db.seed_workflows import seed_workflows
        seed_workflows()
    except Exception as e:
        logger.warning(f"Could not seed workflows: {e}")

    # Kanban agent global settings columns on settings table
    _kanban_settings_columns = [
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
        logger.debug(f"Kanban settings migration: {e}")

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
        logger.debug(f"Kanban board color migration: {e}")

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
        logger.debug(f"Kanban board position migration: {e}")

    # Initiative settings columns
    initiative_columns = [
        ("initiative_level", "VARCHAR DEFAULT 'assist'"),
        ("initiative_allow_telegram", "BOOLEAN DEFAULT 0"),
        ("initiative_allow_routine_tasks", "BOOLEAN DEFAULT 0"),
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
        logger.debug(f"Kanban board send_to_cli migration: {e}")

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
        logger.debug(f"Kanban board archived migration: {e}")
