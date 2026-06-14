from distr.core.paths import MODELS_DIR
from distr.core.db import Session, Settings, ScreenPosition
from distr.core.db import get_session
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QPoint
from typing import Dict, Any
import base64
import hashlib
import hmac
import logging
import json
import os
import platform

logger = logging.getLogger(__name__)

# Incremented when every decrypt key candidate fails MAC; reset in load_settings_from_db.
_mac_validation_failure_count = 0
# Avoid spamming logs: load_settings_from_db is called many times per session.
_mac_decrypt_bulk_warning_logged = False

SETTINGS_DIR = os.path.join(MODELS_DIR, "settings")
ENCRYPTION_PREFIX = "enc:v1:"
SECRET_SETTINGS_FIELDS = {
    "assemblyai_key",
    "speechmatics_key",
    "openai_key",
    "anthropic_key",
    "cursor_key",
    "aws_polly_key",
    "elevenlabs_key",
    "openrouter_key",
    "groq_key",
    "kilo_key",
    "gemini_key",
}
CONNECTED_ACCOUNT_SECRET_FIELDS = {
    "api_token",
    "api_key",
    "token",
    "access_token",
    "refresh_token",
    "client_secret",
    "bot_token",
    "signing_secret",
}


def _primary_settings_key_material() -> str:
    """Key material used when encrypting new secrets (matches legacy single-key behavior)."""
    key_material = (os.getenv("DECISIONSAI_SETTINGS_SECRET") or "").strip()
    if not key_material:
        key_material = f"{os.path.expanduser('~')}|{platform.node()}|decisionsai-settings"
    return key_material


def _primary_settings_key_bytes() -> bytes:
    return hashlib.sha256(_primary_settings_key_material().encode("utf-8")).digest()


def _iter_decrypt_key_bytes() -> list[bytes]:
    """Ordered unique keys to try when decrypting (migration / hostname / secret changes)."""
    seen: set[bytes] = set()
    keys: list[bytes] = []

    def add_material(material: str) -> None:
        if not material:
            return
        digest = hashlib.sha256(material.encode("utf-8")).digest()
        if digest not in seen:
            seen.add(digest)
            keys.append(digest)

    env = (os.getenv("DECISIONSAI_SETTINGS_SECRET") or "").strip()
    if env:
        add_material(env)
    add_material(f"{os.path.expanduser('~')}|{platform.node()}|decisionsai-settings")
    legacy_secret = (os.getenv("DECISIONSAI_SETTINGS_LEGACY_SECRET") or "").strip()
    if legacy_secret:
        add_material(legacy_secret)
    legacy_node = (os.getenv("DECISIONSAI_SETTINGS_LEGACY_NODE") or "").strip()
    if legacy_node:
        add_material(f"{os.path.expanduser('~')}|{legacy_node}|decisionsai-settings")
    return keys


def _stream_xor(data: bytes, key: bytes, nonce: bytes) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < len(data):
        block = hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(a ^ b for a, b in zip(data, out[: len(data)]))


def _encrypt_secret(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if raw.startswith(ENCRYPTION_PREFIX):
        return raw
    key = _primary_settings_key_bytes()
    nonce = os.urandom(16)
    plaintext = raw.encode("utf-8")
    ciphertext = _stream_xor(plaintext, key, nonce)
    mac = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    payload = base64.urlsafe_b64encode(nonce + ciphertext + mac).decode("ascii")
    return f"{ENCRYPTION_PREFIX}{payload}"


def _decrypt_secret(value: str) -> str:
    global _mac_validation_failure_count
    raw = (value or "").strip()
    if not raw:
        return ""
    if not raw.startswith(ENCRYPTION_PREFIX):
        return raw
    try:
        payload = base64.urlsafe_b64decode(raw[len(ENCRYPTION_PREFIX):].encode("ascii"))
        if len(payload) < 16 + 32:
            return raw
        nonce = payload[:16]
        mac = payload[-32:]
        ciphertext = payload[16:-32]
        for key in _iter_decrypt_key_bytes():
            expected_mac = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
            if hmac.compare_digest(mac, expected_mac):
                return _stream_xor(ciphertext, key, nonce).decode("utf-8")
        _mac_validation_failure_count += 1
        logger.debug("Settings secret MAC validation failed for one field")
        return ""
    except Exception as exc:
        logger.warning("Failed to decrypt settings secret: %s", exc)
        return ""


def _encrypt_connected_accounts(accounts: Any) -> Any:
    if not isinstance(accounts, list):
        return accounts
    encrypted_accounts = []
    for account in accounts:
        if not isinstance(account, dict):
            encrypted_accounts.append(account)
            continue
        cloned = dict(account)
        for field in CONNECTED_ACCOUNT_SECRET_FIELDS:
            if field in cloned and isinstance(cloned[field], str):
                cloned[field] = _encrypt_secret(cloned[field])
        encrypted_accounts.append(cloned)
    return encrypted_accounts


def _decrypt_connected_accounts(accounts: Any) -> Any:
    if not isinstance(accounts, list):
        return accounts
    decrypted_accounts = []
    for account in accounts:
        if not isinstance(account, dict):
            decrypted_accounts.append(account)
            continue
        cloned = dict(account)
        for field in CONNECTED_ACCOUNT_SECRET_FIELDS:
            if field in cloned and isinstance(cloned[field], str):
                cloned[field] = _decrypt_secret(cloned[field])
        decrypted_accounts.append(cloned)
    return decrypted_accounts

def save_settings_to_db(settings_dict: Dict[str, Any]) -> None:
    """Save settings to database"""
    normalized_settings = dict(settings_dict)
    for field in SECRET_SETTINGS_FIELDS:
        if field in normalized_settings and isinstance(normalized_settings[field], str):
            normalized_settings[field] = _encrypt_secret(normalized_settings[field])
    if 'connected_accounts' in normalized_settings:
        normalized_settings['connected_accounts'] = _encrypt_connected_accounts(normalized_settings['connected_accounts'])

    with Session() as session:
        settings = session.query(Settings).first()
        if not settings:
            settings = Settings()
            session.add(settings)
        
        # Convert lists to JSON strings before saving
        if 'indexed_folders' in normalized_settings:
            normalized_settings['indexed_folders'] = json.dumps(normalized_settings['indexed_folders'])
        if 'connected_accounts' in normalized_settings:
            normalized_settings['connected_accounts'] = json.dumps(normalized_settings['connected_accounts'])
        
        # Update all settings from the dictionary
        for key, value in normalized_settings.items():
            if hasattr(settings, key):
                old_value = getattr(settings, key)
                setattr(settings, key, value)
                if old_value != value:
                    logger.debug(f"Setting {key} = {value} (was {old_value})")
            else:
                logger.warning(f"Settings model does not have attribute: {key}")
        
        try:
            session.commit()
            # Force flush to ensure changes are written to disk
            session.flush()
            # Verify critical settings were saved
            if 'accepted_eula' in normalized_settings:
                # Expire the object to force a fresh read from database
                session.expire(settings)
                session.refresh(settings)
                saved_value = getattr(settings, 'accepted_eula', None)
                logger.debug(f"Settings committed. Verified accepted_eula={saved_value}")
            else:
                logger.debug("Settings committed to database successfully")
        except Exception as e:
            session.rollback()
            logger.error(f"Error saving settings: {str(e)}")
            raise

def load_settings_from_db() -> Dict[str, Any]:
    """Load settings from database and return as dictionary"""
    global _mac_validation_failure_count, _mac_decrypt_bulk_warning_logged
    _mac_validation_failure_count = 0
    with Session() as session:
        # Query settings directly - each new session gets fresh data from database
        # Avoid session.refresh() as it can cause SQLAlchemy recursion errors with complex annotations
        # The session context manager ensures we get a fresh query each time
        settings = session.query(Settings).first()
        if not settings:
            return {}
        
        # No need to refresh - we're using a fresh session, so data is already current
        
        # Convert SQLAlchemy model to dictionary
        settings_dict = {}
        migration_needed = False
        for column in Settings.__table__.columns:
            if column.name != 'id':
                try:
                    value = getattr(settings, column.name)
                    # Parse JSON strings back to lists
                    if column.name in ['indexed_folders', 'connected_accounts'] and value:
                        try:
                            value = json.loads(value)
                        except json.JSONDecodeError:
                            value = []
                    if column.name == 'connected_accounts':
                        if isinstance(value, list):
                            for account in value:
                                if not isinstance(account, dict):
                                    continue
                                for field in CONNECTED_ACCOUNT_SECRET_FIELDS:
                                    field_value = account.get(field)
                                    if isinstance(field_value, str) and field_value and not field_value.startswith(ENCRYPTION_PREFIX):
                                        migration_needed = True
                        value = _decrypt_connected_accounts(value)
                    if column.name in SECRET_SETTINGS_FIELDS and isinstance(value, str):
                        if value and not value.startswith(ENCRYPTION_PREFIX):
                            migration_needed = True
                        value = _decrypt_secret(value)
                    settings_dict[column.name] = value
                except Exception as e:
                    # Column might not exist in database yet (migration pending)
                    # Log and continue - the migration will add it
                    logger.debug(f"Could not load column {column.name} from database (may not exist yet): {e}")
                    # Set to None so it can use defaults
                    settings_dict[column.name] = None
        
        # Log EULA status for debugging
        if 'accepted_eula' in settings_dict:
            logger.debug(f"load_settings_from_db: accepted_eula={settings_dict.get('accepted_eula')}")
        else:
            logger.warning("load_settings_from_db: accepted_eula not found in database settings!")

        if migration_needed:
            try:
                save_settings_to_db(dict(settings_dict))
                logger.info("Migrated legacy plaintext settings secrets to encrypted format")
            except Exception as exc:
                logger.warning("Failed to migrate settings secrets: %s", exc)

        failures = _mac_validation_failure_count
        _mac_validation_failure_count = 0
        if failures:
            if not _mac_decrypt_bulk_warning_logged:
                _mac_decrypt_bulk_warning_logged = True
                logger.warning(
                    "Could not decrypt %s encrypted setting field(s) (MAC mismatch). "
                    "Typical causes: copied database from another machine, hostname change, or "
                    "DECISIONSAI_SETTINGS_SECRET mismatch. Fix: set DECISIONSAI_SETTINGS_LEGACY_NODE "
                    "to the old computer name, or DECISIONSAI_SETTINGS_LEGACY_SECRET to the prior "
                    "secret string, then restart — or re-enter API keys in Settings.",
                    failures,
                )
            else:
                logger.debug(
                    "Encrypted settings still unreadable (%s field(s)); see earlier MAC warning.",
                    failures,
                )
        else:
            _mac_decrypt_bulk_warning_logged = False

        return settings_dict



def load_preferences_config():    
    path = os.path.join(SETTINGS_DIR, "preferences.json")
    try:
        with open(path, "r") as f:
            config = json.load(f)
    except FileNotFoundError:
        logger.error(f"Error: Preferences file not found at {path}")
        config = {}
    except json.JSONDecodeError:
        logger.error(f"Error: Invalid JSON in config file at {path}")
        config = {}
    return config


def get_screens_hash():
    screens = QApplication.screens()
    screen_info = sorted([
        f"{screen.name()}:{screen.geometry().width()}x{screen.geometry().height()}+{screen.geometry().x()}+{screen.geometry().y()}"
        for screen in screens
    ])
    screens_string = "|".join(screen_info)
    return hashlib.md5(screens_string.encode()).hexdigest()

def get_screen_names():
    return [screen.name() for screen in QApplication.screens()]

def save_oracle_position(x, y, screen=None):
    settings = load_settings_from_db()
    if not settings.get('restore_position'):
        logging.debug("Position not saved - restore_position setting is disabled")
        return
    
    if not screen:
        screen = QApplication.screenAt(QPoint(int(x), int(y)))
        if not screen:
            logging.warning(f"Could not find screen for position {x}, {y}")
            return
    
    # Get screen geometry for validation
    screen_geo = screen.geometry()
    
    # Ensure coordinates are within screen bounds and non-negative
    x = max(0, min(x, screen_geo.width()))
    y = max(0, min(y, screen_geo.height()))
    
    screens_id = get_screens_hash()
    logging.debug(f"\n=== Saving Oracle Position ===")
    logging.debug(f"Screen Configuration Hash: {screens_id}")
    logging.debug(f"Current Screen: {screen.name()}")
    logging.debug(f"Screen Geometry: {screen_geo}")
    logging.debug(f"Relative Position: ({x}, {y})")
    
    with get_session() as session:
        try:
            # Try to get existing record
            position = session.query(ScreenPosition).filter_by(screens_id=screens_id).first()
            
            if position:
                # Update existing record
                position.screen_name = screen.name()
                position.pos_x = x
                position.pos_y = y
            else:
                # Create new record
                position = ScreenPosition(
                    screens_id=screens_id,
                    screen_name=screen.name(),
                    pos_x=x,
                    pos_y=y
                )
                session.add(position)
            
            session.commit()
            logging.debug("Position saved successfully")
            
        except Exception as e:
            session.rollback()
            logging.error(f"Failed to save position: {e}")
            raise
