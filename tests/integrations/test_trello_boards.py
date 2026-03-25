#!/usr/bin/env python3
"""
Test script to fetch Trello boards from connected accounts.

Usage:
    python tests/integrations/test_trello_boards.py
"""

import sys
import os
import json
import logging

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from distr.core.trello_api import TrelloAPI
from distr.core.settings import load_settings_from_db

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def load_trello_accounts():
    """Load Trello accounts from database settings."""
    settings = load_settings_from_db()
    accounts_data = settings.get('connected_accounts', '[]')
    
    try:
        if isinstance(accounts_data, str):
            connected_accounts = json.loads(accounts_data)
        else:
            connected_accounts = accounts_data
    except Exception as e:
        logger.error(f"Failed to parse connected_accounts: {e}")
        return []
    
    # Filter to only valid Trello accounts
    trello_accounts = [
        acc for acc in connected_accounts
        if isinstance(acc, dict) and acc.get('provider') == 'trello' and acc.get('is_valid', False)
    ]
    
    return trello_accounts


def test_trello_connection(api_key: str, api_token: str) -> bool:
    """Test Trello API connection."""
    try:
        trello_api = TrelloAPI(api_key, api_token)
        return trello_api.test_connection()
    except Exception as e:
        logger.error(f"Connection test failed: {e}")
        return False


def get_trello_boards(api_key: str, api_token: str, include_closed: bool = False):
    """Get all Trello boards for an account."""
    try:
        trello_api = TrelloAPI(api_key, api_token)
        if include_closed:
            # Get all boards including closed
            endpoint = "/members/me/boards"
            params = {
                'filter': 'all',  # Get all boards
                'fields': 'id,name,closed,desc,url'
            }
            result = trello_api._make_request(endpoint, params=params)
            if result:
                return result if isinstance(result, list) else []
            return []
        else:
            boards = trello_api.get_boards()
            return boards
    except Exception as e:
        logger.error(f"Failed to get boards: {e}")
        return []


def print_board_info(board: dict, index: int):
    """Print formatted board information."""
    print(f"\n  Board {index}:")
    print(f"    ID: {board.get('id', 'N/A')}")
    print(f"    Name: {board.get('name', 'Unnamed Board')}")
    print(f"    URL: {board.get('url', 'N/A')}")
    if board.get('desc'):
        desc = board.get('desc', '').strip()
        if desc:
            # Truncate long descriptions
            if len(desc) > 100:
                desc = desc[:100] + "..."
            print(f"    Description: {desc}")
    print(f"    Closed: {board.get('closed', False)}")


def main():
    """Main function to test Trello board fetching."""
    print("=" * 80)
    print("Trello Boards Test")
    print("=" * 80)
    print()
    
    # Load Trello accounts
    print("Loading Trello accounts from database...")
    trello_accounts = load_trello_accounts()
    
    if not trello_accounts:
        print("❌ No valid Trello accounts found in database.")
        print("\nTo add a Trello account:")
        print("  1. Open DecisionsAI")
        print("  2. Go to Settings > Advanced")
        print("  3. Click 'Trello' button")
        print("  4. Add your Trello API key and token")
        return
    
    print(f"✓ Found {len(trello_accounts)} Trello account(s)\n")
    
    all_boards = []
    
    # Test each account
    for i, account in enumerate(trello_accounts, 1):
        account_name = account.get('name', f'Trello Account {i}')
        api_key = account.get('api_key', '')
        api_token = account.get('api_token', '')
        
        print("-" * 80)
        print(f"Account {i}: {account_name}")
        print("-" * 80)
        
        if not api_key or not api_token:
            print(f"❌ Missing credentials for {account_name}")
            continue
        
        # Test connection
        print("Testing connection...")
        if not test_trello_connection(api_key, api_token):
            print(f"❌ Connection failed for {account_name}")
            print("   Please check your API key and token.")
            continue
        
        print("✓ Connection successful!")
        
        # Get member info
        try:
            trello_api = TrelloAPI(api_key, api_token)
            member_info = trello_api._make_request("/members/me", params={'fields': 'username,fullName,email'})
            if member_info:
                print(f"  Account: {member_info.get('fullName', member_info.get('username', 'Unknown'))}")
                print(f"  Username: {member_info.get('username', 'N/A')}")
        except Exception as e:
            logger.debug(f"Could not get member info: {e}")
        
        # Get boards
        print(f"\nFetching boards for {account_name}...")
        boards = get_trello_boards(api_key, api_token)
        
        if not boards:
            print(f"⚠ No open boards found for {account_name}")
            # Try to get all boards including closed ones
            print("  Checking for closed boards...")
            all_boards_result = get_trello_boards(api_key, api_token, include_closed=True)
            if all_boards_result:
                total = len(all_boards_result)
                closed = sum(1 for b in all_boards_result if b.get('closed', False))
                open_count = total - closed
                print(f"  Total boards: {total} (Open: {open_count}, Closed: {closed})")
                if closed > 0:
                    print(f"\n  Closed Boards (showing first 5):")
                    for j, board in enumerate([b for b in all_boards_result if b.get('closed', False)][:5], 1):
                        print(f"    {j}. {board.get('name', 'Unnamed')} (ID: {board.get('id', 'N/A')})")
            else:
                print("  No boards found (including closed)")
            continue
        
        # Filter out closed boards
        open_boards = [b for b in boards if not b.get('closed', False)]
        closed_boards = [b for b in boards if b.get('closed', False)]
        
        print(f"✓ Found {len(open_boards)} open board(s)")
        if closed_boards:
            print(f"  (and {len(closed_boards)} closed board(s) - not shown)")
        
        # Print board details
        if open_boards:
            print(f"\n  Open Boards:")
            for j, board in enumerate(open_boards, 1):
                print_board_info(board, j)
                all_boards.append({
                    'account': account_name,
                    'board': board
                })
    
    # Summary
    print("\n" + "=" * 80)
    print("Summary")
    print("=" * 80)
    print(f"Total accounts tested: {len(trello_accounts)}")
    print(f"Total open boards found: {len(all_boards)}")
    
    if all_boards:
        print(f"\nAll Boards:")
        for i, item in enumerate(all_boards, 1):
            board = item['board']
            print(f"  {i}. [{item['account']}] {board.get('name', 'Unnamed')} (ID: {board.get('id', 'N/A')})")
    
    print("\n✓ Test completed!")


if __name__ == "__main__":
    main()
