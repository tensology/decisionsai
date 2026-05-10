"""
Trello API Client

Provides methods to interact with Trello API to fetch boards, lists, and cards.
"""

import requests
import logging
from typing import List, Dict, Optional, Any

logger = logging.getLogger(__name__)


class TrelloAPI:
    """Client for interacting with Trello API"""
    
    BASE_URL = "https://api.trello.com/1"
    
    def __init__(self, api_key: str, api_token: str):
        """
        Initialize Trello API client
        
        Args:
            api_key: Trello API key
            api_token: Trello API token
        """
        self.api_key = api_key
        self.api_token = api_token
        self.params = {
            'key': api_key,
            'token': api_token
        }
    
    def _make_request(self, endpoint: str, method: str = 'GET', **kwargs) -> Optional[Dict[str, Any]]:
        """
        Make a request to Trello API
        
        Args:
            endpoint: API endpoint (e.g., '/members/me/boards')
            method: HTTP method (GET, POST, etc.)
            **kwargs: Additional parameters for requests
            
        Returns:
            JSON response as dict, or None if error
        """
        url = f"{self.BASE_URL}{endpoint}"
        
        # Merge params
        request_params = self.params.copy()
        if 'params' in kwargs:
            request_params.update(kwargs.pop('params'))
        kwargs['params'] = request_params
        
        try:
            if method.upper() == 'GET':
                response = requests.get(url, timeout=10, **kwargs)
            elif method.upper() == 'POST':
                response = requests.post(url, timeout=10, **kwargs)
            elif method.upper() == 'PUT':
                response = requests.put(url, timeout=10, **kwargs)
            elif method.upper() == 'DELETE':
                response = requests.delete(url, timeout=10, **kwargs)
            else:
                logger.error(f"Unsupported HTTP method: {method}")
                return None
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 401:
                logger.error("Trello API: Unauthorized - invalid credentials")
                return None
            else:
                logger.error(f"Trello API error: HTTP {response.status_code} - {response.text}")
                return None
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Trello API request error: {e}")
            return None
        except Exception as e:
            logger.error(f"Trello API unexpected error: {e}")
            return None
    
    def get_boards(self) -> List[Dict[str, Any]]:
        """
        Get all boards for the authenticated user
        
        Returns:
            List of board dictionaries
        """
        endpoint = "/members/me/boards"
        params = {
            'filter': 'open',  # Only open boards
            'fields': 'id,name,closed,desc,url'  # Only get necessary fields
        }
        result = self._make_request(endpoint, params=params)
        if result:
            return result if isinstance(result, list) else []
        return []
    
    def get_lists(self, board_id: str) -> List[Dict[str, Any]]:
        """
        Get all lists for a board
        
        Args:
            board_id: Trello board ID
            
        Returns:
            List of list dictionaries
        """
        endpoint = f"/boards/{board_id}/lists"
        params = {
            'filter': 'open',  # Only open lists
            'fields': 'id,name,closed,pos'  # Only get necessary fields
        }
        result = self._make_request(endpoint, params=params)
        if result:
            return result if isinstance(result, list) else []
        return []
    
    def get_cards(self, list_id: str) -> List[Dict[str, Any]]:
        """
        Get all cards for a list
        
        Args:
            list_id: Trello list ID
            
        Returns:
            List of card dictionaries
        """
        endpoint = f"/lists/{list_id}/cards"
        params = {
            'filter': 'open',  # Only open cards
            'fields': 'id,name,closed,desc,url,idList,idBoard,labels,idMembers,pos,due',  # Rich card data
            'customFieldItems': 'true'  # Include custom fields for time estimates
        }
        result = self._make_request(endpoint, params=params)
        if result:
            return result if isinstance(result, list) else []
        return []
    
    def get_board_cards(self, board_id: str) -> List[Dict[str, Any]]:
        """
        Get all cards for a board (across all lists)
        
        Args:
            board_id: Trello board ID
            
        Returns:
            List of card dictionaries
        """
        endpoint = f"/boards/{board_id}/cards"
        params = {
            'filter': 'open',  # Only open cards
            'fields': 'id,name,closed,desc,url,idList,idBoard,labels,idMembers,pos,due',  # Rich card data
            'customFieldItems': 'true'  # Include custom fields for time estimates
        }
        result = self._make_request(endpoint, params=params)
        if result:
            return result if isinstance(result, list) else []
        return []
    
    def test_connection(self) -> bool:
        """
        Test if the API credentials are valid
        
        Returns:
            True if connection is valid, False otherwise
        """
        endpoint = "/members/me"
        result = self._make_request(endpoint)
        return result is not None

    # --- Card CRUD / Sync ---
    def create_card(self, list_id: str, name: str, desc: str = "", **kwargs) -> Optional[Dict[str, Any]]:
        """
        Create a new card in a list.
        Optional kwargs can include due, idMembers, idLabels, pos, etc.
        """
        endpoint = "/cards"
        params = {
            'idList': list_id,
            'name': name,
            'desc': desc,
        }
        params.update(kwargs)
        return self._make_request(endpoint, method='POST', params=params)

    def update_card(self, card_id: str, name: Optional[str] = None, desc: Optional[str] = None,
                   idList: Optional[str] = None, **kwargs) -> Optional[Dict[str, Any]]:
        """
        Update card fields. Pass only the fields to change.
        """
        endpoint = f"/cards/{card_id}"
        params: Dict[str, Any] = {}
        if name is not None:
            params['name'] = name
        if desc is not None:
            params['desc'] = desc
        if idList is not None:
            params['idList'] = idList
        params.update(kwargs)
        return self._make_request(endpoint, method='PUT', params=params)

    def delete_card(self, card_id: str) -> bool:
        """Delete a card."""
        endpoint = f"/cards/{card_id}"
        result = self._make_request(endpoint, method='DELETE')
        return result is not None

    def move_card(self, card_id: str, list_id: str) -> Optional[Dict[str, Any]]:
        """Move a card to another list."""
        return self.update_card(card_id, idList=list_id)

    def add_member_to_card(self, card_id: str, member_id: str) -> Optional[Dict[str, Any]]:
        """Add a member to a card."""
        endpoint = f"/cards/{card_id}/idMembers"
        params = {'value': member_id}
        return self._make_request(endpoint, method='POST', params=params)

    def set_card_due_date(self, card_id: str, due_date: str) -> Optional[Dict[str, Any]]:
        """Set due date for a card (ISO 8601 string)."""
        return self.update_card(card_id, due=due_date)

    def add_label_to_card(self, card_id: str, label_id: str) -> Optional[Dict[str, Any]]:
        """Add a label to a card."""
        endpoint = f"/cards/{card_id}/idLabels"
        params = {'value': label_id}
        return self._make_request(endpoint, method='POST', params=params)

    def add_comment_to_card(self, card_id: str, text: str) -> Optional[Dict[str, Any]]:
        """Add a comment to a card."""
        endpoint = f"/cards/{card_id}/actions/comments"
        params = {'text': text}
        return self._make_request(endpoint, method='POST', params=params)

    # --- Board Metadata ---
    def get_board_members(self, board_id: str) -> List[Dict[str, Any]]:
        """Get members of a board."""
        endpoint = f"/boards/{board_id}/members"
        params = {
            'fields': 'id,fullName,username'
        }
        result = self._make_request(endpoint, params=params)
        if result:
            return result if isinstance(result, list) else []
        return []

    def get_board_labels(self, board_id: str) -> List[Dict[str, Any]]:
        """Get labels defined on a board."""
        endpoint = f"/boards/{board_id}/labels"
        params = {
            'fields': 'id,name,color'
        }
        result = self._make_request(endpoint, params=params)
        if result:
            return result if isinstance(result, list) else []
        return []

