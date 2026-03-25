import json
import logging
from typing import Dict, List, Optional

from distr.core.db import get_session
from distr.core.db.projects import Project, BoardColumn, BoardTicket
from .trello_api import TrelloAPI
from distr.core.settings import load_settings_from_db

logger = logging.getLogger(__name__)


class BoardManager:
    """
    Handle board operations for a project.
    - For Trello-linked projects: two-way sync using Trello API
    - For local projects: CRUD on database-backed board
    """

    def __init__(self, project_id: int):
        self.project_id = project_id
        self.project = self._load_project()
        self.trello_api = self._init_trello_api() if self.project.provider == 'trello' else None
        self.board_members = self._load_board_members() if self.trello_api and self.project.board_id else []

    # ---------- Public API ----------
    def load_board(self) -> Dict:
        """Load board structure for UI consumption."""
        # Reload project to get latest board_id/board_name from database
        self.project = self._load_project()
        # Re-initialize Trello API if provider changed
        if self.project.provider == 'trello' and not self.trello_api:
            self.trello_api = self._init_trello_api()
            self.board_members = self._load_board_members() if self.trello_api and self.project.board_id else []
        elif self.project.provider != 'trello':
            self.trello_api = None
            self.board_members = []
        elif self.trello_api and self.project.board_id:
            # Reload board members if board_id changed
            self.board_members = self._load_board_members()
        
        if self.trello_api and self.project.board_id:
            return self._load_from_trello()
        return self._load_from_database()

    def sync_from_trello(self) -> None:
        """Refresh local snapshot from Trello."""
        # Reload project to get latest board_id
        self.project = self._load_project()
        # Re-initialize Trello API if needed
        if self.project.provider == 'trello' and not self.trello_api:
            self.trello_api = self._init_trello_api()
            self.board_members = self._load_board_members() if self.trello_api and self.project.board_id else []
        elif self.trello_api and self.project.board_id:
            self.board_members = self._load_board_members()
        
        if not (self.trello_api and self.project.board_id):
            return None
        # Currently sync is on-demand: simply reload from Trello and save a minimal cache locally
        board_data = self._load_from_trello(save_snapshot=True)
        logger.info("Trello board synced to local snapshot")
        return board_data

    def create_ticket(self, column_id: str, ticket_data: Dict) -> Dict:
        """Create a ticket in the appropriate backend and return unified ticket data."""
        if self.trello_api and self.project.board_id:
            created = self.trello_api.create_card(
                list_id=column_id,
                name=ticket_data.get('title', ''),
                desc=ticket_data.get('description', ''),
                due=ticket_data.get('due_date'),
                idMembers=self._member_ids_from_names(ticket_data.get('assignee')),
            )
            return self._format_card(created, {})

        with get_session() as session:
            position = self._next_position(session, column_id)
            ticket = BoardTicket(
                column_id=column_id,
                title=ticket_data.get('title', ''),
                description=ticket_data.get('description'),
                assignee=ticket_data.get('assignee'),
                due_date=ticket_data.get('due_date'),
                priority=ticket_data.get('priority'),
                time_estimate=ticket_data.get('time_estimate'),
                tags=json.dumps(ticket_data.get('tags', [])) if ticket_data.get('tags') else None,
                position=position,
            )
            session.add(ticket)
            session.commit()
            session.refresh(ticket)
            return self._format_ticket(ticket)

    def update_ticket(self, ticket_id: str, updates: Dict) -> Dict:
        """Update a ticket in Trello or DB."""
        if self.trello_api and self.project.board_id:
            self.trello_api.update_card(
                card_id=ticket_id,
                name=updates.get('title'),
                desc=updates.get('description'),
                due=updates.get('due_date'),
                idMembers=self._member_ids_from_names(updates.get('assignee')),
            )
            # Fetch latest card state
            board = self._load_from_trello()
            return self._find_ticket(board, ticket_id)

        with get_session() as session:
            ticket = session.query(BoardTicket).get(ticket_id)
            if not ticket:
                raise ValueError("Ticket not found")
            for field in ('title', 'description', 'assignee', 'priority', 'time_estimate'):
                if field in updates and updates.get(field) is not None:
                    setattr(ticket, field, updates.get(field))
            if updates.get('due_date') is not None:
                ticket.due_date = updates.get('due_date')
            if 'tags' in updates:
                ticket.tags = json.dumps(updates.get('tags') or [])
            session.commit()
            session.refresh(ticket)
            return self._format_ticket(ticket)

    def move_ticket(self, ticket_id: str, new_column_id: str, position: int = 0) -> Dict:
        """Move a ticket to a new column."""
        if self.trello_api and self.project.board_id:
            self.trello_api.move_card(card_id=ticket_id, list_id=new_column_id)
            board = self._load_from_trello()
            return self._find_ticket(board, ticket_id)

        with get_session() as session:
            ticket = session.query(BoardTicket).get(ticket_id)
            if not ticket:
                raise ValueError("Ticket not found")
            ticket.column_id = new_column_id
            ticket.position = position
            session.commit()
            session.refresh(ticket)
            return self._format_ticket(ticket)

    def delete_ticket(self, ticket_id: str) -> bool:
        """Delete a ticket."""
        if self.trello_api and self.project.board_id:
            return self.trello_api.delete_card(ticket_id)

        with get_session() as session:
            ticket = session.query(BoardTicket).get(ticket_id)
            if not ticket:
                return False
            session.delete(ticket)
            session.commit()
            return True

    def create_column(self, name: str, position: int = 0) -> Dict:
        """Create a column (local boards only)."""
        if self.trello_api and self.project.board_id:
            raise ValueError("Cannot create Trello lists from local UI")

        with get_session() as session:
            column = BoardColumn(project_id=self.project_id, name=name, position=position)
            session.add(column)
            session.commit()
            session.refresh(column)
            return self._format_column(column)

    def update_column(self, column_id: int, name: str) -> Dict:
        """Rename a column (local boards only)."""
        if self.trello_api and self.project.board_id:
            raise ValueError("Cannot rename Trello lists from local UI")

        with get_session() as session:
            column = session.query(BoardColumn).get(column_id)
            if not column:
                raise ValueError("Column not found")
            column.name = name
            session.commit()
            session.refresh(column)
            return self._format_column(column)

    def delete_column(self, column_id: int) -> bool:
        """Delete a column (local boards only)."""
        if self.trello_api and self.project.board_id:
            raise ValueError("Cannot delete Trello lists from local UI")

        with get_session() as session:
            column = session.query(BoardColumn).get(column_id)
            if not column:
                return False
            session.delete(column)
            session.commit()
            return True

    def reorder_column(self, column_id: int, new_position: int) -> Dict:
        """Change a column's position (local boards only)."""
        if self.trello_api and self.project.board_id:
            raise ValueError("Cannot reorder Trello lists from local UI")

        with get_session() as session:
            column = session.query(BoardColumn).get(column_id)
            if not column:
                raise ValueError("Column not found")
            column.position = new_position
            session.commit()
            session.refresh(column)
            return self._format_column(column)

    def get_ticket(self, ticket_id: str) -> Optional[Dict]:
        """Fetch a single ticket."""
        if self.trello_api and self.project.board_id:
            board = self._load_from_trello()
            return self._find_ticket(board, ticket_id)

        with get_session() as session:
            ticket = session.query(BoardTicket).get(ticket_id)
            return self._format_ticket(ticket) if ticket else None

    # ---------- Internal helpers ----------
    def _load_project(self) -> Project:
        with get_session() as session:
            project = session.query(Project).get(self.project_id)
            if not project:
                raise ValueError(f"Project {self.project_id} not found")
            session.expunge(project)
            return project

    def _init_trello_api(self) -> Optional[TrelloAPI]:
        settings = load_settings_from_db()
        accounts_data = settings.get('connected_accounts', '[]')
        try:
            connected_accounts = json.loads(accounts_data) if isinstance(accounts_data, str) else accounts_data
        except Exception:
            connected_accounts = []

        trello_accounts = [
            acc for acc in connected_accounts
            if isinstance(acc, dict) and acc.get('provider') == 'trello' and acc.get('is_valid')
        ]
        if not trello_accounts:
            return None

        account = trello_accounts[0]
        api_key = account.get('api_key')
        api_token = account.get('api_token')
        if not api_key or not api_token:
            return None

        return TrelloAPI(api_key, api_token)

    def _load_board_members(self) -> List[Dict]:
        if not (self.trello_api and self.project.board_id):
            return []
        members = self.trello_api.get_board_members(self.project.board_id)
        return members or []

    def _load_from_trello(self, save_snapshot: bool = False) -> Dict:
        lists = self.trello_api.get_lists(self.project.board_id) or []
        cards = self.trello_api.get_board_cards(self.project.board_id) or []
        member_lookup = {m.get('id'): m.get('fullName') for m in self.board_members}

        columns = []
        list_lookup = {lst.get('id'): lst for lst in lists if not lst.get('closed')}
        for trello_list in list_lookup.values():
            list_id = trello_list.get('id')
            column_cards = [c for c in cards if c.get('idList') == list_id and not c.get('closed')]
            tickets = [self._format_card(card, member_lookup) for card in column_cards]
            columns.append({
                'id': list_id,
                'name': trello_list.get('name', 'List'),
                'position': trello_list.get('pos', 0),
                'tickets': tickets,
            })

        # Sort columns by position
        columns.sort(key=lambda c: c.get('position', 0))

        board_data = {
            'provider': 'trello',
            'columns': columns,
            'project_name': self.project.name,
        }

        if save_snapshot:
            self._cache_trello_snapshot(columns)

        return board_data

    def _cache_trello_snapshot(self, columns: List[Dict]) -> None:
        """Persist a lightweight snapshot to local DB for offline view."""
        try:
            with get_session() as session:
                # Clear existing snapshot columns/tickets for this project
                session.query(BoardColumn).filter_by(project_id=self.project_id).delete()
                session.commit()

                for idx, column_data in enumerate(columns):
                    column = BoardColumn(
                        project_id=self.project_id,
                        name=column_data.get('name', 'List'),
                        position=column_data.get('position') or idx,
                        trello_list_id=column_data.get('id'),
                    )
                    session.add(column)
                    session.flush()

                    for pos, ticket in enumerate(column_data.get('tickets', [])):
                        session.add(BoardTicket(
                            column_id=column.id,
                            title=ticket.get('title', ''),
                            description=ticket.get('description'),
                            assignee=ticket.get('assignee'),
                            due_date=ticket.get('due_date'),
                            priority=ticket.get('priority'),
                            tags=json.dumps(ticket.get('tags') or []),
                            position=pos,
                            trello_card_id=ticket.get('id'),
                        ))
                session.commit()
        except Exception as e:
            logger.warning(f"Could not cache Trello snapshot: {e}")

    def _load_from_database(self) -> Dict:
        with get_session() as session:
            columns = session.query(BoardColumn).filter_by(project_id=self.project_id).order_by(BoardColumn.position).all()
            
            # Initialize default columns if none exist
            if not columns:
                default_columns = ["Backlog", "In Progress", "QA/Assess", "Done"]
                for idx, col_name in enumerate(default_columns):
                    column = BoardColumn(
                        project_id=self.project_id,
                        name=col_name,
                        position=idx
                    )
                    session.add(column)
                session.commit()
                columns = session.query(BoardColumn).filter_by(project_id=self.project_id).order_by(BoardColumn.position).all()
            
            board_columns = []
            for column in columns:
                tickets = (
                    session.query(BoardTicket)
                    .filter_by(column_id=column.id)
                    .order_by(BoardTicket.position)
                    .all()
                )
                board_columns.append({
                    'id': column.id,
                    'name': column.name,
                    'position': column.position,
                    'tickets': [self._format_ticket(t) for t in tickets],
                })
            return {
                'provider': 'local',
                'columns': board_columns,
                'project_name': self.project.name,
            }

    def _member_ids_from_names(self, assignee: Optional[str]) -> Optional[str]:
        """Find Trello member id by name. Returns comma-separated ids or None."""
        if not assignee or not self.board_members:
            return None
        matches = [m.get('id') for m in self.board_members if m.get('fullName') == assignee]
        if not matches:
            return None
        return ','.join(matches)

    def _format_card(self, card: Dict, member_lookup: Dict) -> Dict:
        if not card:
            return {}
        member_names = [member_lookup.get(mid) for mid in card.get('idMembers', []) if member_lookup.get(mid)]
        labels = card.get('labels') or []
        # Try to get time estimate from Trello custom fields (if available)
        time_estimate = None
        if card.get('customFieldItems'):
            # Look for time estimate in custom fields
            for field_item in card.get('customFieldItems', []):
                if field_item.get('idCustomField') and 'time' in str(field_item.get('idCustomField', '')).lower():
                    time_estimate = field_item.get('value', {}).get('text') or field_item.get('value')
                    break
        tags = [label.get('name') for label in labels if label.get('name')]
        return {
            'id': card.get('id'),
            'title': card.get('name', ''),
            'description': card.get('desc'),
            'assignee': ', '.join(member_names) if member_names else None,
            'priority': None,
            'tags': tags,
            'due_date': card.get('due'),
            'time_estimate': time_estimate,
            'position': card.get('pos'),
            'column_id': card.get('idList'),
        }

    def _format_ticket(self, ticket: BoardTicket) -> Dict:
        return {
            'id': ticket.id,
            'title': ticket.title,
            'description': ticket.description,
            'assignee': ticket.assignee,
            'priority': ticket.priority,
            'tags': json.loads(ticket.tags) if ticket.tags else [],
            'due_date': ticket.due_date.isoformat() if ticket.due_date else None,
            'time_estimate': ticket.time_estimate,
            'position': ticket.position,
            'column_id': ticket.column_id,
        }

    def _format_column(self, column: BoardColumn) -> Dict:
        return {
            'id': column.id,
            'name': column.name,
            'position': column.position,
        }

    def _find_ticket(self, board_data: Dict, ticket_id: str) -> Optional[Dict]:
        for column in board_data.get('columns', []):
            for ticket in column.get('tickets', []):
                if str(ticket.get('id')) == str(ticket_id):
                    return ticket
        return None

    def _next_position(self, session, column_id: int) -> int:
        """Get next position for ticket within column."""
        max_pos = (
            session.query(BoardTicket.position)
            .filter_by(column_id=column_id)
            .order_by(BoardTicket.position.desc())
            .first()
        )
        return (max_pos[0] + 1) if max_pos and max_pos[0] is not None else 0
