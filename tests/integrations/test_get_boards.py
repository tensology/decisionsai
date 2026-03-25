#!/usr/bin/env python3
"""
Test script to fetch and display Jira boards and tickets
Uses saved Jira credentials from the database
"""

import json
import requests
import sqlite3
import os
from base64 import b64encode
from typing import List, Dict, Optional, Any

# Get the database path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, 'db', 'settings.db')

class JiraAPI:
    """Client for interacting with Jira API"""
    
    def __init__(self, server_url: str, email: str, api_token: str):
        """
        Initialize Jira API client
        
        Args:
            server_url: Jira server URL (e.g., https://your-domain.atlassian.net)
            email: Jira account email
            api_token: Jira API token
        """
        # Ensure server URL has https:// and no trailing slash
        if not server_url.startswith('http://') and not server_url.startswith('https://'):
            server_url = f"https://{server_url}"
        self.server_url = server_url.rstrip('/')
        self.email = email
        self.api_token = api_token
        
        # Basic auth with email and API token
        auth_string = f"{email}:{api_token}"
        auth_bytes = auth_string.encode('ascii')
        auth_b64 = b64encode(auth_bytes).decode('ascii')
        
        self.headers = {
            'Authorization': f'Basic {auth_b64}',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }
    
    def _make_request(self, endpoint: str, method: str = 'GET', **kwargs) -> Optional[Any]:
        """
        Make a request to Jira API
        
        Args:
            endpoint: API endpoint (e.g., '/rest/api/3/myself')
            method: HTTP method (GET, POST, etc.)
            **kwargs: Additional parameters for requests
            
        Returns:
            JSON response, or None if error
        """
        url = f"{self.server_url}{endpoint}"
        
        try:
            if method.upper() == 'GET':
                response = requests.get(url, headers=self.headers, timeout=30, **kwargs)
            elif method.upper() == 'POST':
                response = requests.post(url, headers=self.headers, timeout=30, **kwargs)
            elif method.upper() == 'PUT':
                response = requests.put(url, headers=self.headers, timeout=30, **kwargs)
            elif method.upper() == 'DELETE':
                response = requests.delete(url, headers=self.headers, timeout=30, **kwargs)
            else:
                print(f"Unsupported HTTP method: {method}")
                return None
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 401:
                print(f"Jira API: Unauthorized - invalid credentials (HTTP 401)")
                return None
            elif response.status_code == 403:
                print(f"Jira API: Forbidden - insufficient permissions (HTTP 403)")
                return None
            else:
                print(f"Jira API error: HTTP {response.status_code} - {response.text[:200]}")
                return None
                
        except requests.exceptions.RequestException as e:
            print(f"Jira API request error: {e}")
            return None
        except Exception as e:
            print(f"Jira API unexpected error: {e}")
            return None
    
    def test_connection(self) -> bool:
        """Test if the API credentials are valid"""
        result = self._make_request('/rest/api/3/myself')
        return result is not None
    
    def get_boards(self) -> List[Dict[str, Any]]:
        """
        Get all boards for the authenticated user using Agile API
        
        Returns:
            List of board dictionaries
        """
        endpoint = '/rest/agile/1.0/board'
        params = {
            'maxResults': 1000,  # Get up to 1000 boards
            'type': 'scrum,kanban'  # Get both Scrum and Kanban boards
        }
        result = self._make_request(endpoint, params=params)
        if result and 'values' in result:
            return result['values']
        return []
    
    def get_board_issues(self, board_id: int) -> List[Dict[str, Any]]:
        """
        Get all issues for a specific board
        
        Args:
            board_id: Jira board ID
            
        Returns:
            List of issue dictionaries
        """
        endpoint = f'/rest/agile/1.0/board/{board_id}/issue'
        params = {
            'maxResults': 1000,  # Get up to 1000 issues
            'jql': '',  # Empty JQL to get all issues
            'fields': 'summary,status,assignee,priority,issuetype,created,updated,description,key'
        }
        result = self._make_request(endpoint, params=params)
        if result and 'issues' in result:
            return result['issues']
        return []
    
    def get_all_issues(self, jql: str = '') -> List[Dict[str, Any]]:
        """
        Get all issues using JQL search
        
        Args:
            jql: JQL query string (empty for all issues)
            
        Returns:
            List of issue dictionaries
        """
        endpoint = '/rest/api/3/search'
        params = {
            'maxResults': 1000,
            'fields': 'summary,status,assignee,priority,issuetype,created,updated,description,key,project'
        }
        if jql:
            params['jql'] = jql
        
        result = self._make_request(endpoint, params=params)
        if result and 'issues' in result:
            return result['issues']
        return []


def load_jira_credentials() -> Optional[Dict[str, str]]:
    """Load Jira credentials from database"""
    try:
        if not os.path.exists(DB_PATH):
            print(f"Database not found at: {DB_PATH}")
            return None
        
        # Connect to database
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Get connected_accounts from settings
        cursor.execute("SELECT connected_accounts FROM settings LIMIT 1")
        row = cursor.fetchone()
        conn.close()
        
        if not row or not row[0]:
            print("No connected_accounts found in database.")
            print("Please add a Jira account in Settings > Advanced > Third Party Providers")
            return None
        
        # Parse connected_accounts
        connected_accounts_str = row[0]
        if isinstance(connected_accounts_str, str):
            connected_accounts = json.loads(connected_accounts_str)
        else:
            connected_accounts = connected_accounts_str
        
        if not isinstance(connected_accounts, list):
            connected_accounts = [connected_accounts] if isinstance(connected_accounts, dict) else []
        
        # Find Jira accounts
        jira_accounts = [
            acc for acc in connected_accounts
            if isinstance(acc, dict) and acc.get('provider') == 'jira' and acc.get('is_valid')
        ]
        
        if not jira_accounts:
            print("No valid Jira accounts found in database.")
            print("Please add a Jira account in Settings > Advanced > Third Party Providers")
            return None
        
        # Use the first valid account
        account = jira_accounts[0]
        print(f"Using Jira account: {account.get('name', 'Unnamed Account')}")
        print(f"Server URL: {account.get('server_url', 'N/A')}\n")
        
        return {
            'server_url': account.get('server_url', ''),
            'email': account.get('email', ''),
            'api_token': account.get('api_token', '')
        }
    except Exception as e:
        print(f"Error loading Jira credentials: {e}")
        import traceback
        traceback.print_exc()
        return None


def print_board_info(board: Dict[str, Any]):
    """Print board information"""
    print(f"  Board ID: {board.get('id')}")
    print(f"  Name: {board.get('name', 'N/A')}")
    print(f"  Type: {board.get('type', 'N/A')}")
    print(f"  Location: {board.get('location', {}).get('displayName', 'N/A')}")
    print(f"  URL: {board.get('self', 'N/A')}")
    print()


def print_issue_info(issue: Dict[str, Any], indent: str = "    "):
    """Print issue/ticket information"""
    key = issue.get('key', 'N/A')
    fields = issue.get('fields', {})
    summary = fields.get('summary', 'No summary')
    status = fields.get('status', {}).get('name', 'N/A')
    assignee = fields.get('assignee', {})
    assignee_name = assignee.get('displayName', 'Unassigned') if assignee else 'Unassigned'
    priority = fields.get('priority', {}).get('name', 'N/A')
    issue_type = fields.get('issuetype', {}).get('name', 'N/A')
    created = fields.get('created', 'N/A')
    updated = fields.get('updated', 'N/A')
    description = fields.get('description', '')
    
    print(f"{indent}Key: {key}")
    print(f"{indent}Summary: {summary}")
    print(f"{indent}Type: {issue_type}")
    print(f"{indent}Status: {status}")
    print(f"{indent}Priority: {priority}")
    print(f"{indent}Assignee: {assignee_name}")
    print(f"{indent}Created: {created}")
    print(f"{indent}Updated: {updated}")
    if description:
        desc_text = str(description)
        if len(desc_text) > 200:
            desc_text = desc_text[:200] + "..."
        print(f"{indent}Description: {desc_text}")
    print()


def main():
    """Main function to fetch and display Jira boards and tickets"""
    print("=" * 80)
    print("Jira Boards and Tickets Test")
    print("=" * 80)
    print()
    
    # Load credentials
    credentials = load_jira_credentials()
    if not credentials:
        return
    
    # Initialize Jira API
    jira = JiraAPI(
        server_url=credentials['server_url'],
        email=credentials['email'],
        api_token=credentials['api_token']
    )
    
    # Test connection
    print("Testing connection...")
    if not jira.test_connection():
        print("Failed to connect to Jira. Please check your credentials.")
        return
    print("✓ Connection successful!\n")
    
    # Get boards
    print("Fetching boards...")
    boards = jira.get_boards()
    print(f"Found {len(boards)} board(s)\n")
    
    if not boards:
        print("No boards found.")
        return
    
    # Print boards and their issues
    for i, board in enumerate(boards, 1):
        print("=" * 80)
        print(f"BOARD {i}: {board.get('name', 'Unnamed Board')}")
        print("=" * 80)
        print_board_info(board)
        
        # Get issues for this board
        board_id = board.get('id')
        if board_id:
            print(f"  Fetching issues for board {board_id}...")
            issues = jira.get_board_issues(board_id)
            print(f"  Found {len(issues)} issue(s)\n")
            
            if issues:
                print(f"  ISSUES:")
                print(f"  {'-' * 76}")
                for j, issue in enumerate(issues, 1):
                    print(f"  Issue {j}:")
                    print_issue_info(issue)
            else:
                print(f"  No issues found in this board.\n")
    
    # Also get all issues across all projects
    print("\n" + "=" * 80)
    print("ALL ISSUES (Across All Projects)")
    print("=" * 80)
    print("\nFetching all issues...")
    all_issues = jira.get_all_issues()
    print(f"Found {len(all_issues)} total issue(s)\n")
    
    if all_issues:
        for i, issue in enumerate(all_issues, 1):
            print(f"Issue {i}:")
            print_issue_info(issue, indent="  ")
    
    print("=" * 80)
    print("Done!")
    print("=" * 80)


if __name__ == '__main__':
    main()
