"""
API routes/endpoints for Board server
"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import List, Optional
from pathlib import Path
import logging

from distr.core.integrations.board_manager import BoardManager

logger = logging.getLogger(__name__)


# Pydantic models for request/response
class TicketCreate(BaseModel):
    column_id: str
    title: str
    description: Optional[str] = None
    assignee: Optional[str] = None
    due_date: Optional[str] = None
    priority: Optional[str] = None
    tags: Optional[List[str]] = None
    time_estimate: Optional[str] = None


class TicketUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    assignee: Optional[str] = None
    due_date: Optional[str] = None
    priority: Optional[str] = None
    tags: Optional[List[str]] = None
    time_estimate: Optional[str] = None


class TicketMove(BaseModel):
    new_column_id: str
    position: Optional[int] = 0


class ColumnCreate(BaseModel):
    name: str
    position: Optional[int] = 0


class ColumnUpdate(BaseModel):
    name: str


class ColumnReorder(BaseModel):
    new_position: int


def create_routes(templates_dir: Path, base_path: str = "") -> APIRouter:
    """
    Create and configure API routes

    Args:
        templates_dir: Path to templates directory
        base_path: Base path prefix for static files (e.g., "/board" when mounted under /board)

    Returns:
        Configured APIRouter
    """
    router = APIRouter()

    def get_project_id(request: Request) -> int:
        """Get project ID from query params or app state"""
        # First try query params
        project_id = request.query_params.get('project_id')
        if project_id:
            try:
                return int(project_id)
            except ValueError:
                pass

        # Fall back to app state
        project_id = getattr(request.app.state, 'project_id', None)
        if not project_id:
            raise HTTPException(status_code=400, detail="Project ID not specified")
        return project_id

    def get_board_manager(request: Request) -> BoardManager:
        """Get BoardManager instance for current project"""
        project_id = get_project_id(request)
        return BoardManager(project_id)

    @router.get("/test", response_class=HTMLResponse)
    async def test_page():
        """Simple test page to verify WebEngine works"""
        return """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Board Test</title>
    <style>
        body { background: #1a1f3a; color: white; font-family: sans-serif; padding: 40px; }
        h1 { color: #3b82f6; }
    </style>
</head>
<body>
    <h1>Board Server Test</h1>
    <p>If you can see this, the WebEngine is working!</p>
    <p>Time: <span id="time"></span></p>
    <script>document.getElementById('time').textContent = new Date().toLocaleString();</script>
</body>
</html>"""

    @router.get("/", response_class=HTMLResponse)
    async def root():
        """Serve the main Board HTML page"""
        try:
            html_path = templates_dir / "index.html"
            with open(html_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
            
            # Fix static file paths if we have a base_path
            if base_path:
                html_content = html_content.replace('href="/css/', f'href="{base_path}/css/')
                html_content = html_content.replace('src="/js/', f'src="{base_path}/js/')
            
            # Add inline fallback styles to ensure page is visible even if CSS fails to load
            css_path = f'{base_path}/css/board.css' if base_path else '/css/board.css'
            html_content = html_content.replace(
                f'<link rel="stylesheet" href="{css_path}">',
                f'''<link rel="stylesheet" href="{css_path}">
    <style>
        /* Fallback styles if CSS file fails to load */
        body {{ background: #1a1f3a !important; color: #ececf1 !important; margin: 0; padding: 0; }}
        .board-container {{ display: flex; flex-direction: column; height: 100vh; padding: 20px; }}
        .board-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }}
        .board-title {{ color: #ffffff; font-size: 28px; }}
        .board-content {{ flex: 1; overflow-y: auto; }}
        .loading {{ color: #ececf1; padding: 20px; }}
    </style>'''
            )
            return html_content
        except Exception as e:
            logger.error(f"Failed to read board HTML: {e}")
            return "<html><body><h1>Error loading board page</h1></body></html>"

    @router.get("/api/board")
    async def get_board(request: Request):
        """Get board structure with columns and tickets"""
        try:
            board_manager = get_board_manager(request)
            board_data = board_manager.load_board()
            return JSONResponse(content=board_data)
        except Exception as e:
            logger.error(f"Error loading board: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/api/board/sync")
    async def sync_board(request: Request):
        """Sync board from Trello (if Trello-linked)"""
        try:
            board_manager = get_board_manager(request)
            board_data = board_manager.sync_from_trello()
            return JSONResponse(content=board_data or {"message": "Not a Trello board"})
        except Exception as e:
            logger.error(f"Error syncing board: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # ========== Ticket CRUD ==========

    @router.post("/api/tickets")
    async def create_ticket(request: Request, ticket_data: TicketCreate):
        """Create a new ticket"""
        try:
            board_manager = get_board_manager(request)
            ticket = board_manager.create_ticket(
                column_id=ticket_data.column_id,
                ticket_data={
                    'title': ticket_data.title,
                    'description': ticket_data.description,
                    'assignee': ticket_data.assignee,
                    'due_date': ticket_data.due_date,
                    'priority': ticket_data.priority,
                    'tags': ticket_data.tags or [],
                    'time_estimate': ticket_data.time_estimate,
                }
            )
            return JSONResponse(content=ticket)
        except Exception as e:
            logger.error(f"Error creating ticket: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/api/tickets/{ticket_id}")
    async def get_ticket(request: Request, ticket_id: str):
        """Get a specific ticket"""
        try:
            board_manager = get_board_manager(request)
            ticket = board_manager.get_ticket(ticket_id)
            if not ticket:
                raise HTTPException(status_code=404, detail="Ticket not found")
            return JSONResponse(content=ticket)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting ticket: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/api/tickets/{ticket_id}")
    async def update_ticket(request: Request, ticket_id: str, updates: TicketUpdate):
        """Update a ticket"""
        try:
            board_manager = get_board_manager(request)
            update_data = {k: v for k, v in updates.dict().items() if v is not None}
            ticket = board_manager.update_ticket(ticket_id, update_data)
            return JSONResponse(content=ticket)
        except Exception as e:
            logger.error(f"Error updating ticket: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/api/tickets/{ticket_id}/move")
    async def move_ticket(request: Request, ticket_id: str, move_data: TicketMove):
        """Move a ticket to a different column"""
        try:
            board_manager = get_board_manager(request)
            ticket = board_manager.move_ticket(
                ticket_id=ticket_id,
                new_column_id=move_data.new_column_id,
                position=move_data.position
            )
            return JSONResponse(content=ticket)
        except Exception as e:
            logger.error(f"Error moving ticket: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/api/tickets/{ticket_id}")
    async def delete_ticket(request: Request, ticket_id: str):
        """Delete a ticket"""
        try:
            board_manager = get_board_manager(request)
            success = board_manager.delete_ticket(ticket_id)
            if not success:
                raise HTTPException(status_code=404, detail="Ticket not found")
            return JSONResponse(content={"message": "Ticket deleted successfully"})
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error deleting ticket: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # ========== Column CRUD ==========

    @router.post("/api/columns")
    async def create_column(request: Request, column_data: ColumnCreate):
        """Create a new column (local boards only)"""
        try:
            board_manager = get_board_manager(request)
            column = board_manager.create_column(
                name=column_data.name,
                position=column_data.position
            )
            return JSONResponse(content=column)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"Error creating column: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/api/columns/{column_id}")
    async def update_column(request: Request, column_id: int, column_data: ColumnUpdate):
        """Rename a column (local boards only)"""
        try:
            board_manager = get_board_manager(request)
            column = board_manager.update_column(column_id, column_data.name)
            return JSONResponse(content=column)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"Error updating column: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/api/columns/{column_id}")
    async def delete_column(request: Request, column_id: int):
        """Delete a column (local boards only)"""
        try:
            board_manager = get_board_manager(request)
            success = board_manager.delete_column(column_id)
            if not success:
                raise HTTPException(status_code=404, detail="Column not found")
            return JSONResponse(content={"message": "Column deleted successfully"})
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error deleting column: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/api/columns/{column_id}/reorder")
    async def reorder_column(request: Request, column_id: int, reorder_data: ColumnReorder):
        """Change column position (local boards only)"""
        try:
            board_manager = get_board_manager(request)
            column = board_manager.reorder_column(column_id, reorder_data.new_position)
            return JSONResponse(content=column)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"Error reordering column: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/api/tickets/{ticket_id}/send-to-project")
    async def send_ticket_to_project(request: Request, ticket_id: str):
        """Send ticket to active project's .tickets folder"""
        try:
            import os
            from datetime import datetime
            from distr.core.agent.services.rag.project import get_active_project
            
            board_manager = get_board_manager(request)
            ticket = board_manager.get_ticket(ticket_id)
            if not ticket:
                raise HTTPException(status_code=404, detail="Ticket not found")
            
            # Get active project
            project = get_active_project()
            if not project:
                raise HTTPException(status_code=400, detail="No project is currently active. Switch to a project first.")
            
            if not project.get('folder_location'):
                raise HTTPException(status_code=400, detail=f"Project {project['name']} has no folder location set.")
            
            # Ensure .tickets folder exists
            tickets_folder = os.path.join(project['folder_location'], '.tickets')
            os.makedirs(tickets_folder, exist_ok=True)
            
            # Generate ticket filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            ticket_filename = f"ticket_{timestamp}.md"
            ticket_path = os.path.join(tickets_folder, ticket_filename)
            
            # Build comprehensive ticket content with all context
            title = ticket.get('title', 'Untitled Ticket')
            description = ticket.get('description', '')
            assignee = ticket.get('assignee', '')
            priority = ticket.get('priority', '')
            due_date = ticket.get('due_date', '')
            tags = ticket.get('tags', [])
            time_estimate = ticket.get('time_estimate', '')
            
            # Build instruction text from ticket data
            instruction_parts = [title]
            if description:
                instruction_parts.append(description)
            
            instruction_text = "\n\n".join(instruction_parts)
            
            # Build context section
            context_parts = []
            if assignee:
                context_parts.append(f"- **Assignee:** {assignee}")
            if priority:
                context_parts.append(f"- **Priority:** {priority.upper()}")
            if due_date:
                context_parts.append(f"- **Due Date:** {due_date}")
            if tags:
                context_parts.append(f"- **Tags:** {', '.join(tags)}")
            if time_estimate:
                context_parts.append(f"- **Time Estimate:** {time_estimate}")
            
            ticket_content = f"""---
id: ticket_{timestamp}
title: {title}
project: {project['name']}
created: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
status: open
source: board_ticket_{ticket_id}
---
            
## Description
{instruction_text}

## Requirements
<!-- Extract specific requirements from the description above -->

## Context
- **Project:** {project['name']} (ID: {project['id']})
- **Folder:** {project['folder_location']}
{chr(10).join(context_parts) if context_parts else ''}

## Related Files
<!-- List any relevant files mentioned or discovered -->

## Conversation Context
<!-- Relevant excerpts from the conversation -->

---
*Auto-generated from board ticket by DecisionsAI*
"""
            
            # Write ticket to file
            with open(ticket_path, 'w', encoding='utf-8') as f:
                f.write(ticket_content)
            
            logger.info(f"Sent board ticket {ticket_id} to project ticket: {ticket_path}")
            
            return JSONResponse(content={
                "message": "Ticket sent to project successfully",
                "ticket_path": ticket_path,
                "project_name": project['name']
            })
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error sending ticket to project: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/api/tickets/{ticket_id}/archive")
    async def archive_ticket(request: Request, ticket_id: str):
        """Archive a ticket (mark as archived/deleted)"""
        try:
            board_manager = get_board_manager(request)
            # For local boards, delete the ticket
            # For Trello, we could mark it as closed, but for now just delete
            success = board_manager.delete_ticket(ticket_id)
            if not success:
                raise HTTPException(status_code=404, detail="Ticket not found")
            return JSONResponse(content={"message": "Ticket archived successfully"})
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error archiving ticket: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    return router
