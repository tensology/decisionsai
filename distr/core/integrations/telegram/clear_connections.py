"""
Clear all Telegram connections from the local database.

Removes:
- Telegram accounts from connected_accounts
- telegram_pending_connection data

Usage:
    python -m distr.core.integrations.telegram.clear_connections
"""
import json
import logging

logger = logging.getLogger(__name__)


def clear_telegram_connections():
    """Clear all Telegram connections from the database."""
    from distr.core.db import get_session, Settings

    try:
        with get_session() as session:
            settings = session.query(Settings).first()
            if not settings:
                logger.info("No settings found in database — nothing to clear")
                return

            cleared = False

            # Remove Telegram entries from connected_accounts
            if settings.connected_accounts:
                try:
                    accounts = settings.connected_accounts
                    if isinstance(accounts, str):
                        accounts = json.loads(accounts)
                    if isinstance(accounts, dict):
                        accounts = [accounts]
                    if not isinstance(accounts, list):
                        accounts = []

                    original_count = len(accounts)
                    accounts = [
                        a for a in accounts
                        if not (isinstance(a, dict) and a.get("provider") == "telegram")
                    ]

                    if len(accounts) < original_count:
                        settings.connected_accounts = json.dumps(accounts) if accounts else "[]"
                        cleared = True
                        logger.info("Removed %d Telegram account(s)", original_count - len(accounts))
                    else:
                        logger.info("No Telegram accounts found in connected_accounts")
                except Exception as exc:
                    logger.warning("Failed to parse connected_accounts: %s", exc)
                    settings.connected_accounts = "[]"
                    cleared = True

            # Clear pending connection data
            try:
                from distr.core.settings import load_settings_from_db, save_settings_to_db
                s = load_settings_from_db()
                if "telegram_pending_connection" in s:
                    del s["telegram_pending_connection"]
                    save_settings_to_db(s)
                    cleared = True
                    logger.info("Cleared telegram_pending_connection")
            except Exception as exc:
                logger.warning("Failed to clear telegram_pending_connection: %s", exc)

            if cleared:
                session.commit()
                logger.info("Telegram connections cleared successfully")
            else:
                logger.info("No Telegram connections found to clear")

    except Exception as exc:
        logger.error("Error clearing Telegram connections: %s", exc, exc_info=True)
        raise


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    response = input(
        "This will remove all Telegram connections from your local database.\n"
        "Continue? (yes/no): "
    ).strip().lower()

    if response not in ("yes", "y"):
        print("Cancelled.")
        sys.exit(0)

    clear_telegram_connections()
    print("Done.")
