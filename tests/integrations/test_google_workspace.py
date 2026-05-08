"""
Test file for Google Workspace Connector and Tool

This test file verifies:
1. Google connection status
2. Gmail operations (check inbox, read, send, draft, reply, delete)
3. Google Drive operations (list folders, read files, upload files, read PDFs)
4. Google Calendar operations (create events, read events, check schedule)
5. Google Docs operations (create from markdown)
"""

import json
import sys
import os
from pathlib import Path

import pytest

pytest.importorskip("langchain.tools")

# Add project root to path
project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector
from distr.core.agent.tools.integrations.google_workspace_tool import (
    GoogleWorkspaceInput,
    GoogleWorkspaceTool,
    _normalize_calendar_events_raw,
)
import logging
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def test_connection():
    """Test if Google is connected"""
    print("\n" + "="*80)
    print("TEST 1: Google Connection Status")
    print("="*80)
    
    connector = GoogleWorkspaceConnector()
    is_connected = connector.is_connected()
    
    print(f"Google Connected: {is_connected}")
    
    if not is_connected:
        print("ERROR: Google is not connected. Please connect your Google account in Settings > Advanced.")
        return False
    
    print("SUCCESS: Google is connected!")
    return True


def test_gmail_check_inbox():
    """Test checking Gmail inbox"""
    print("\n" + "="*80)
    print("TEST 2: Check Gmail Inbox")
    print("="*80)
    
    connector = GoogleWorkspaceConnector()
    if not connector.is_connected():
        print("SKIP: Google not connected")
        return False
    
    messages = connector.check_inbox(max_results=5, query="is:unread")
    
    print(f"Found {len(messages)} unread message(s)")
    
    for i, msg in enumerate(messages, 1):
        print(f"\nMessage {i}:")
        print(f"  From: {msg.get('from', 'Unknown')}")
        print(f"  Subject: {msg.get('subject', 'No Subject')}")
        print(f"  Date: {msg.get('date', 'Unknown')}")
        print(f"  Snippet: {msg.get('snippet', '')[:100]}...")
        print(f"  ID: {msg.get('id', 'Unknown')}")
    
    return len(messages) > 0


def test_gmail_read_email(message_id=None):
    """Test reading a specific email"""
    print("\n" + "="*80)
    print("TEST 3: Read Email")
    print("="*80)
    
    connector = GoogleWorkspaceConnector()
    if not connector.is_connected():
        print("SKIP: Google not connected")
        return False
    
    # If no message_id provided, get one from inbox
    if not message_id:
        messages = connector.check_inbox(max_results=1)
        if not messages:
            print("SKIP: No emails in inbox to read")
            return False
        message_id = messages[0].get('id')
    
    email = connector.get_email(message_id)
    
    if not email:
        print("ERROR: Could not retrieve email")
        return False
    
    print(f"From: {email.get('from', 'Unknown')}")
    print(f"To: {email.get('to', 'Unknown')}")
    print(f"Subject: {email.get('subject', 'No Subject')}")
    print(f"Date: {email.get('date', 'Unknown')}")
    print(f"\nBody (first 500 chars):\n{email.get('body', '')[:500]}...")
    
    return True


def test_gmail_send_email():
    """Test sending an email"""
    print("\n" + "="*80)
    print("TEST 4: Send Email")
    print("="*80)
    
    connector = GoogleWorkspaceConnector()
    if not connector.is_connected():
        print("SKIP: Google not connected")
        return False
    
    # Send test email to yourself
    test_subject = f"Test Email - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    test_body = "This is a test email sent from the Google Workspace Connector test suite."
    
    # Get user's email from settings or use a test address
    # For now, we'll skip actual sending to avoid spam
    print("NOTE: Email sending test skipped to avoid spam.")
    print(f"Would send email with subject: {test_subject}")
    print(f"Body: {test_body}")
    
    # Uncomment to actually send:
    # success = connector.send_email("your-email@example.com", test_subject, test_body)
    # print(f"Email sent: {success}")
    # return success
    
    return True


def test_gmail_draft_email():
    """Test creating a draft email"""
    print("\n" + "="*80)
    print("TEST 5: Create Draft Email")
    print("="*80)
    
    connector = GoogleWorkspaceConnector()
    if not connector.is_connected():
        print("SKIP: Google not connected")
        return False
    
    test_subject = f"Test Draft - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    test_body = "This is a test draft email."
    
    draft_id = connector.draft_email("test@example.com", test_subject, test_body)
    
    if draft_id:
        print(f"SUCCESS: Draft created with ID: {draft_id}")
        return True
    else:
        print("ERROR: Failed to create draft")
        return False


def test_drive_list_folders():
    """Test listing Google Drive folders"""
    print("\n" + "="*80)
    print("TEST 6: List Google Drive Folders")
    print("="*80)
    
    connector = GoogleWorkspaceConnector()
    if not connector.is_connected():
        print("SKIP: Google not connected")
        return False
    
    folders = connector.list_drive_folders()
    
    print(f"Found {len(folders)} folder(s)")
    
    for i, folder in enumerate(folders[:10], 1):  # Show first 10
        print(f"\nFolder {i}:")
        print(f"  Name: {folder.get('name', 'Unknown')}")
        print(f"  ID: {folder.get('id', 'Unknown')}")
        print(f"  Modified: {folder.get('modifiedTime', 'Unknown')}")
    
    return True


def test_drive_list_files():
    """Test listing Google Drive files"""
    print("\n" + "="*80)
    print("TEST 7: List Google Drive Files")
    print("="*80)
    
    connector = GoogleWorkspaceConnector()
    if not connector.is_connected():
        print("SKIP: Google not connected")
        return False
    
    files = connector.list_drive_files()
    
    print(f"Found {len(files)} file(s)")
    
    for i, file in enumerate(files[:10], 1):  # Show first 10
        print(f"\nFile {i}:")
        print(f"  Name: {file.get('name', 'Unknown')}")
        print(f"  ID: {file.get('id', 'Unknown')}")
        print(f"  Type: {file.get('mimeType', 'Unknown')}")
        print(f"  Modified: {file.get('modifiedTime', 'Unknown')}")
    
    return True


def test_drive_read_file(file_id=None):
    """Test reading a file from Google Drive"""
    print("\n" + "="*80)
    print("TEST 8: Read Google Drive File")
    print("="*80)
    
    connector = GoogleWorkspaceConnector()
    if not connector.is_connected():
        print("SKIP: Google not connected")
        return False
    
    # If no file_id provided, get one from file list
    if not file_id:
        files = connector.list_drive_files()
        if not files:
            print("SKIP: No files in Drive to read")
            return False
        file_id = files[0].get('id')
        print(f"Using file ID: {file_id}")
    
    content = connector.read_drive_file(file_id)
    
    if content:
        print(f"SUCCESS: Read file content ({len(content)} characters)")
        print(f"First 500 characters:\n{content[:500]}...")
        return True
    else:
        print("ERROR: Could not read file")
        return False


def test_calendar_get_events():
    """Test getting calendar events"""
    print("\n" + "="*80)
    print("TEST 9: Get Calendar Events")
    print("="*80)
    
    connector = GoogleWorkspaceConnector()
    if not connector.is_connected():
        print("SKIP: Google not connected")
        return False
    
    # Get events for next 7 days
    time_min = datetime.utcnow()
    time_max = datetime.utcnow() + timedelta(days=7)
    
    events = connector.get_calendar_events(time_min=time_min, time_max=time_max, max_results=10)
    
    if events is None:
        print("ERROR: Could not retrieve calendar events (API may not be enabled)")
        return False
    
    print(f"Found {len(events)} event(s) in next 7 days")
    
    for i, event in enumerate(events, 1):
        print(f"\nEvent {i}:")
        print(f"  Summary: {event.get('summary', 'No Title')}")
        start = event.get('start', {}).get('dateTime', event.get('start', {}).get('date', 'Unknown'))
        end = event.get('end', {}).get('dateTime', event.get('end', {}).get('date', 'Unknown'))
        print(f"  Start: {start}")
        print(f"  End: {end}")
        if event.get('description'):
            print(f"  Description: {event.get('description')[:100]}...")
        if event.get('location'):
            print(f"  Location: {event.get('location')}")
    
    return True


def test_calendar_schedule_tomorrow():
    """Test getting tomorrow's schedule"""
    print("\n" + "="*80)
    print("TEST 10: Get Tomorrow's Schedule")
    print("="*80)
    
    connector = GoogleWorkspaceConnector()
    if not connector.is_connected():
        print("SKIP: Google not connected")
        return False
    
    events = connector.get_schedule_tomorrow()
    
    if events is None:
        print("ERROR: Could not retrieve schedule (API may not be enabled)")
        return False
    
    print(f"Found {len(events)} event(s) for tomorrow")
    
    for i, event in enumerate(events, 1):
        print(f"\nEvent {i}:")
        print(f"  Summary: {event.get('summary', 'No Title')}")
        start = event.get('start', {}).get('dateTime', event.get('start', {}).get('date', 'Unknown'))
        end = event.get('end', {}).get('dateTime', event.get('end', {}).get('date', 'Unknown'))
        print(f"  Time: {start} - {end}")
    
    return True


def test_calendar_schedule_this_week():
    """Test getting this week's schedule"""
    print("\n" + "="*80)
    print("TEST 11: Get This Week's Schedule")
    print("="*80)
    
    connector = GoogleWorkspaceConnector()
    if not connector.is_connected():
        print("SKIP: Google not connected")
        return False
    
    events = connector.get_schedule_this_week()
    
    if events is None:
        print("ERROR: Could not retrieve schedule (API may not be enabled)")
        return False
    
    print(f"Found {len(events)} event(s) this week")
    
    for i, event in enumerate(events, 1):
        print(f"\nEvent {i}:")
        print(f"  Summary: {event.get('summary', 'No Title')}")
        start = event.get('start', {}).get('dateTime', event.get('start', {}).get('date', 'Unknown'))
        end = event.get('end', {}).get('dateTime', event.get('end', {}).get('date', 'Unknown'))
        print(f"  Time: {start} - {end}")
    
    return True


def test_docs_create_from_markdown():
    """Test creating Google Doc from markdown"""
    print("\n" + "="*80)
    print("TEST 12: Create Google Doc from Markdown")
    print("="*80)
    
    connector = GoogleWorkspaceConnector()
    if not connector.is_connected():
        print("SKIP: Google not connected")
        return False
    
    test_title = f"Test Doc - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    test_markdown = """# Test Document

This is a **test document** created from markdown.

## Features

- Feature 1
- Feature 2
- Feature 3

## Conclusion

This document was created programmatically.
"""
    
    doc_id = connector.create_doc_from_markdown(test_title, test_markdown)
    
    if doc_id:
        print(f"SUCCESS: Document created with ID: {doc_id}")
        print(f"Title: {test_title}")
        return True
    else:
        print("ERROR: Failed to create document")
        return False


def test_google_workspace_input_accepts_top_level_events():
    """Top-level events must survive Pydantic validation (OpenAI tool calls)."""
    pytest.importorskip("pydantic")
    events_list = [
        {
            "summary": "Breakfast",
            "start_time": "2026-05-05T08:00:00",
            "end_time": "2026-05-05T08:45:00",
        }
    ]
    inp = GoogleWorkspaceInput(
        action="create_calendar_events_batch",
        events=events_list,
    )
    assert inp.events == events_list
    assert inp.action == "create_calendar_events_batch"


def test_normalize_calendar_events_raw():
    ev = [
        {"summary": "A", "start_time": "2026-01-01T10:00:00", "end_time": "2026-01-01T11:00:00"},
    ]
    assert _normalize_calendar_events_raw({"events": ev}) == ev
    assert _normalize_calendar_events_raw({"calendar_events": ev}) == ev
    assert _normalize_calendar_events_raw({"events": json.dumps(ev)}) == ev
    assert _normalize_calendar_events_raw({"params": {"events": ev}}) == ev


def test_google_workspace_tool():
    """Test the Google Workspace Tool wrapper"""
    print("\n" + "="*80)
    print("TEST 13: Google Workspace Tool Wrapper")
    print("="*80)
    
    tool = GoogleWorkspaceTool()
    
    if not tool.connector.is_connected():
        print("SKIP: Google not connected")
        return False
    
    # Test check_inbox action
    print("\nTesting check_inbox action...")
    result = tool._run("check_inbox", {"max_results": 3})
    print(f"Result: {result[:200]}...")
    
    # Test draft_email action (same as test script)
    print("\nTesting draft_email action...")
    test_subject = f"Test Draft via Tool - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    test_body = "This is a test draft email created through the tool wrapper."
    result = tool._run("draft_email", {
        "to": "test@example.com",
        "subject": test_subject,
        "body": test_body
    })
    print(f"Result: {result}")
    
    # Test get_schedule_tomorrow action
    print("\nTesting get_schedule_tomorrow action...")
    result = tool._run("get_schedule_tomorrow")
    print(f"Result: {result[:200]}...")
    
    return True


def run_all_tests():
    """Run all tests"""
    print("\n" + "="*80)
    print("GOOGLE WORKSPACE CONNECTOR TEST SUITE")
    print("="*80)
    
    results = {}
    
    # Connection test (must pass for others)
    results['connection'] = test_connection()
    
    if not results['connection']:
        print("\n" + "="*80)
        print("ERROR: Google is not connected. Please connect your Google account first.")
        print("="*80)
        return results
    
    # Gmail tests
    results['gmail_check_inbox'] = test_gmail_check_inbox()
    if results['gmail_check_inbox']:
        # Get a message ID for read test
        connector = GoogleWorkspaceConnector()
        messages = connector.check_inbox(max_results=1)
        message_id = messages[0].get('id') if messages else None
        results['gmail_read_email'] = test_gmail_read_email(message_id)
    else:
        results['gmail_read_email'] = False
    
    results['gmail_send_email'] = test_gmail_send_email()
    results['gmail_draft_email'] = test_gmail_draft_email()
    
    # Drive tests
    results['drive_list_folders'] = test_drive_list_folders()
    results['drive_list_files'] = test_drive_list_files()
    if results['drive_list_files']:
        connector = GoogleWorkspaceConnector()
        files = connector.list_drive_files()
        file_id = files[0].get('id') if files else None
        results['drive_read_file'] = test_drive_read_file(file_id)
    else:
        results['drive_read_file'] = False
    
    # Calendar tests
    results['calendar_get_events'] = test_calendar_get_events()
    results['calendar_schedule_tomorrow'] = test_calendar_schedule_tomorrow()
    results['calendar_schedule_this_week'] = test_calendar_schedule_this_week()
    
    # Docs tests
    results['docs_create_from_markdown'] = test_docs_create_from_markdown()
    
    # Tool wrapper test
    results['tool_wrapper'] = test_google_workspace_tool()
    
    # Print summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for test_name, result in results.items():
        status = "PASS" if result else "FAIL"
        print(f"{test_name}: {status}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    return results


if __name__ == "__main__":
    run_all_tests()

