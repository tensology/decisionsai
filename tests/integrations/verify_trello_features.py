import sys
import os
from sqlalchemy import inspection
from PyQt6.QtWidgets import QApplication

# Add project root to path
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, _project_root)

from distr.core.db import engine, TrelloTicket, init_db
from distr.gui.trello_ticket_dialog import TrelloTicketDialog

def verify_db():
    print("Verifying Database Schema...")
    init_db()  # Ensure tables are created
    inspector = inspection.inspect(engine)
    tables = inspector.get_table_names()
    
    if 'trello_tickets' in tables:
        print("[PASS] Table 'trello_tickets' exists.")
        columns = [c['name'] for c in inspector.get_columns('trello_tickets')]
        expected_columns = ['id', 'trello_card_id', 'title', 'description', 'chat_id', 'workflow_id', 'members', 'attachments', 'status']
        missing = [c for c in expected_columns if c not in columns]
        if not missing:
            print("[PASS] All expected columns exist.")
        else:
            print(f"[FAIL] Missing columns: {missing}")
    else:
        print("[FAIL] Table 'trello_tickets' does not exist.")

def verify_dialog_import():
    print("\nVerifying Dialog Instantiation...")
    app = QApplication(sys.argv)
    mock_data = {'id': '123', 'name': 'Test Card', 'desc': 'Test Description', 'idMembers': []}
    try:
        dialog = TrelloTicketDialog(mock_data)
        print("[PASS] TrelloTicketDialog instantiated successfully.")
    except Exception as e:
        print(f"[FAIL] TrelloTicketDialog instantiation failed: {e}")

if __name__ == "__main__":
    verify_db()
    verify_dialog_import()
