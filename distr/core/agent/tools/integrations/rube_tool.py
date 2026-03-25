"""
Rube Tool for LangChain

This tool allows the LLM to execute tasks using Rube MCP tools in a separate thread.
Rube can create Google Docs, set calendar dates, and perform other cross-app automation tasks.
"""

import logging
import os
import threading
import queue
import time
from typing import Optional, Dict, Any
from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class RubeInput(BaseModel):
    """Input schema for rube tool."""
    task: str = Field(description="The complete task description for Rube to execute. Examples: 'create a google doc with the content from my clipboard', 'take the document I dropped and build a google document out of it', 'set a calendar date for tomorrow at 2pm'")
    context: Optional[str] = Field(default=None, description="Optional additional context or instructions")


def get_clipboard_content():
    """Get content from clipboard using platform-specific methods."""
    try:
        import platform
        system = platform.system()
        
        if system == "Darwin":  # macOS
            import subprocess
            result = subprocess.run(
                ['pbpaste'],
                capture_output=True,
                text=True,
                timeout=1
            )
            return result.stdout if result.returncode == 0 else None
        elif system == "Windows":
            import subprocess
            result = subprocess.run(
                ['powershell', '-command', 'Get-Clipboard'],
                capture_output=True,
                text=True,
                timeout=1
            )
            return result.stdout.strip() if result.returncode == 0 else None
        else:  # Linux
            try:
                import subprocess
                result = subprocess.run(
                    ['xclip', '-selection', 'clipboard', '-o'],
                    capture_output=True,
                    text=True,
                    timeout=1
                )
                if result.returncode == 0:
                    return result.stdout
            except Exception:
                pass
            try:
                result = subprocess.run(
                    ['xsel', '--clipboard', '--output'],
                    capture_output=True,
                    text=True,
                    timeout=1
                )
                return result.stdout if result.returncode == 0 else None
            except Exception:
                pass
            return None
    except Exception as e:
        logger.error(f"Error getting clipboard content: {e}", exc_info=True)
        return None


def get_dropped_files() -> Optional[list]:
    """Get files that were dropped on the oracle ball."""
    import json
    storage_dir = os.path.join(os.path.expanduser("~"), ".decisionsai", "dropped_files")
    storage_file = os.path.join(storage_dir, "current_files.json")
    
    if not os.path.exists(storage_file):
        return None
    
    try:
        with open(storage_file, 'r') as f:
            data = json.load(f)
            files = data.get("files", [])
            # Only return files that still exist
            existing_files = [f for f in files if os.path.exists(f)]
            return existing_files if existing_files else None
    except Exception as e:
        logger.error(f"Error reading dropped files: {e}")
        return None


def _is_email_inbox_query(task: str) -> bool:
    """Detect if the task is an email/inbox query that needs special handling."""
    task_lower = task.lower()
    email_keywords = [
        "check my inbox", "check inbox", "check mail", "get my mail", "go get my mail",
        "read my email", "read email", "show my email", "show email",
        "list my email", "list email", "fetch email", "fetch my email",
        "unread", "unread messages", "unread email", "new email", "new messages",
        "gmail", "inbox", "email messages", "my messages"
    ]
    return any(keyword in task_lower for keyword in email_keywords)


def _generate_gmail_pagination_workbench_code(tool_slug: str, session_id: str) -> str:
    """
    Generate Python code for Gmail pagination using RUBE_REMOTE_WORKBENCH.
    
    This implements the comprehensive Gmail pagination strategy:
    1. Fetches all unread messages with proper pagination (handles 500+ messages)
    2. Deduplicates messages by message ID
    3. Fetches message details (subject, sender, date) in parallel batches
    4. Formats output for user display
    5. Saves results to temp file for potential download
    
    Follows the methodology from Rube MCP Gmail pagination instructions.
    """
    return f'''
import json
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

# Storage for all messages (deduplicated by message ID)
all_messages = {{}}
page_token = None
page_count = 0
total_fetched = 0
fetch_error = None

# Pagination loop
while True:
    page_count += 1
    print(f"[{{time.strftime('%H:%M:%S')}}] Fetching page {{page_count}}...")
    
    # Build arguments for this page
    args = {{
        "query": "is:unread",
        "maxResults": 500,
        "includeSpamTrash": False
    }}
    
    # Add pageToken if we have one
    if page_token:
        args["pageToken"] = page_token
    
    # Fetch this page using the discovered tool
    try:
        result, error = run_composio_tool(
            tool_slug="{tool_slug}",
            arguments=args
        )
        
        if error:
            fetch_error = error
            error_lower = str(error).lower()
            print(f"Error fetching page {{page_count}}: {{error}}")
            # Check for connection/auth errors
            if "connection" in error_lower or "auth" in error_lower or "unauthorized" in error_lower or "401" in error_lower or "403" in error_lower:
                print("Gmail connection or authentication error detected")
            break
        
        # Extract messages from response
        # Handle nested data structure (Rube may nest responses)
        data = result.get("data", {{}})
        if "data" in data:
            data = data["data"]
        
        # Try multiple possible response structures
        messages = data.get("messages", [])
        if not messages and isinstance(data, list):
            messages = data
        
        next_page_token = data.get("nextPageToken") or data.get("next_page_token")
        result_size_estimate = data.get("resultSizeEstimate", data.get("result_size_estimate", 0))
        
        print(f"Page {{page_count}}: Got {{len(messages)}} messages, estimate: {{result_size_estimate}}")
        
        # Add to our deduplicated collection
        for msg in messages:
            msg_id = msg.get("id")
            if msg_id and msg_id not in all_messages:
                all_messages[msg_id] = {{
                    "id": msg_id,
                    "threadId": msg.get("threadId")
                }}
                total_fetched += 1
        
        # Check if there are more pages
        if not next_page_token:
            print(f"No more pages. Total unique messages: {{total_fetched}}")
            break
        
        # Connector limitation check
        if len(messages) == 1 and result_size_estimate > 10:
            print("WARNING: Connector may not be honoring pagination properly")
            # Continue anyway - we'll get what we can
        
        # Set token for next page
        page_token = next_page_token
        
        # Safety limit to prevent infinite loops
        if page_count > 100:
            print("WARNING: Reached 100 pages, stopping to prevent infinite loop")
            break
            
    except Exception as e:
        fetch_error = str(e)
        error_lower = str(e).lower()
        print(f"Exception fetching page {{page_count}}: {{fetch_error}}")
        # Check for connection/auth errors
        if "connection" in error_lower or "auth" in error_lower or "unauthorized" in error_lower or "401" in error_lower or "403" in error_lower:
            print("Gmail connection or authentication error detected")
        break

print(f"\\nTotal pages fetched: {{page_count}}")
print(f"Total unique messages: {{total_fetched}}")

# Check if we had a connection/auth error
if fetch_error:
    error_lower = str(fetch_error).lower()
    if "connection" in error_lower or "auth" in error_lower or "unauthorized" in error_lower or "401" in error_lower or "403" in error_lower:
        output_text = f"Error: Gmail connection or authentication failed. Please check your Gmail connection in Rube settings. Error details: {{fetch_error}}"
    else:
        output_text = f"Error fetching emails: {{fetch_error}}"
elif len(all_messages) > 0:
    print(f"\\nFetching details for {{len(all_messages)}} messages...")
    
    def fetch_message_details(msg_id):
        """Fetch full message details including subject and sender"""
        try:
            # Try GMAIL_GET_MESSAGE tool
            result, error = run_composio_tool(
                tool_slug="GMAIL_GET_MESSAGE",
                arguments={{
                    "id": msg_id,
                    "format": "metadata",
                    "metadataHeaders": ["Subject", "From", "Date"]
                }}
            )
            
            if error:
                # Try alternative tool name
                result, error = run_composio_tool(
                    tool_slug="GMAIL_FETCH_EMAIL",
                    arguments={{
                        "message_id": msg_id
                    }}
                )
            
            if error:
                return None
            
            # Extract metadata
            # Handle nested data structure
            data = result.get("data", {{}})
            if "data" in data:
                data = data["data"]
            
            # Try multiple possible payload structures
            payload = data.get("payload", {{}})
            if not payload and "message" in data:
                payload = data["message"].get("payload", {{}})
            
            headers = payload.get("headers", [])
            if not headers and "message" in data:
                headers = data["message"].get("headers", [])
            
            subject = next((h["value"] for h in headers if h.get("name", "").lower() == "subject"), "No Subject")
            sender = next((h["value"] for h in headers if h.get("name", "").lower() == "from"), "Unknown Sender")
            date = next((h["value"] for h in headers if h.get("name", "").lower() == "date"), "Unknown Date")
            
            return {{
                "id": msg_id,
                "threadId": data.get("threadId"),
                "subject": subject,
                "sender": sender,
                "date": date
            }}
        except Exception as e:
            print(f"Error fetching message {{msg_id}}: {{str(e)}}")
            return None
    
    # Fetch details in parallel (batches of 50 to avoid rate limits)
    batch_size = 50
    message_ids = list(all_messages.keys())
    detailed_messages = []
    
    for i in range(0, len(message_ids), batch_size):
        batch = message_ids[i:i+batch_size]
        batch_num = i//batch_size + 1
        total_batches = (len(message_ids)-1)//batch_size + 1
        print(f"Processing batch {{batch_num}}/{{total_batches}}...")
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(fetch_message_details, batch))
        
        detailed_messages.extend([r for r in results if r is not None])
        
        # Small delay between batches to avoid rate limits
        if i + batch_size < len(message_ids):
            time.sleep(0.5)
    
    print(f"\\nSuccessfully fetched details for {{len(detailed_messages)}} messages")
    
    # Format output
    if len(detailed_messages) == 0:
        output_text = "No unread messages found in your inbox."
    else:
        plural = "s" if len(detailed_messages) != 1 else ""
        output_lines = [f"Found {{len(detailed_messages)}} unread message{{plural}}:\\n"]
        for i, msg in enumerate(detailed_messages[:50], 1):  # Show first 50
            sender = msg.get('sender', 'Unknown Sender')
            subject = msg.get('subject', 'No Subject')
            # Clean up sender (remove email if it's in angle brackets)
            if '<' in sender and '>' in sender:
                sender = sender.split('<')[0].strip() or sender.split('<')[1].split('>')[0]
            output_lines.append(f"{{i}}. From: {{sender}} - '{{subject}}'")
        
        if len(detailed_messages) > 50:
            remaining = len(detailed_messages) - 50
            plural_remaining = "s" if remaining != 1 else ""
            output_lines.append(f"\\n... and {{remaining}} more message{{plural_remaining}}.")
        
        output_text = "\\n".join(output_lines)
    
    # Save to temp file (cross-platform)
    output_file = os.path.join(tempfile.gettempdir(), "unread_gmail_messages.json")
    with open(output_file, "w") as f:
        json.dump({{
            "total_count": len(detailed_messages),
            "messages": detailed_messages
        }}, f, indent=2)
    
    print(f"Results saved to {{output_file}}")
elif not fetch_error:
    output_text = "No unread messages found."

output_text
'''


def _execute_rube_task_in_thread(
    task: str,
    context: Dict[str, Any],
    result_queue: queue.Queue,
    rube_token: str
):
    """
    Execute Rube task in a separate thread using MCP tools.
    
    Args:
        task: The task description
        context: Additional context (clipboard, dropped files, etc.)
        result_queue: Queue to put results in
        rube_token: Rube MCP token
    """
    try:
        logger.info(f"Executing Rube task with token (length: {len(rube_token) if rube_token else 0})")
        
        # Set environment variable for MCP tools
        os.environ["RUBE_TOKEN"] = rube_token
        
        # Parse task to understand what needs to be done
        task_lower = task.lower()
        
        # Check if task involves clipboard
        clipboard_content = None
        if "clipboard" in task_lower:
            clipboard_content = get_clipboard_content()
            if clipboard_content:
                logger.info(f"Retrieved clipboard content ({len(clipboard_content)} chars)")
        
        # Check if task involves dropped files
        dropped_files = None
        if "dropped" in task_lower or "drop" in task_lower or "document" in task_lower:
            dropped_files = get_dropped_files()
            if dropped_files:
                logger.info(f"Found {len(dropped_files)} dropped file(s)")
        
        # Use MCP SDK with Streamable HTTP transport to connect to Rube MCP server
        try:
            logger.info(f"Connecting to Rube MCP server for task: {task}")
            
            # Import MCP SDK and HTTP client
            try:
                from mcp import ClientSession
                import httpx
                import asyncio
                
                # Try to import streamable HTTP client
                # The exact import path may vary depending on MCP SDK version
                streamable_http_client = None
                try:
                    from mcp.client.streamable_http import streamable_http_client
                except ImportError:
                    try:
                        # Alternative import path
                        from mcp.client import streamable_http_client
                    except ImportError:
                        try:
                            # Try SSE client as fallback
                            from mcp.client.sse import sse_client
                            streamable_http_client = sse_client
                        except ImportError:
                            logger.warning("Could not import streamable_http_client, will try alternative approach")
                            streamable_http_client = None
                
                if streamable_http_client is None:
                    raise ImportError("streamable_http_client not available in MCP SDK")
                    
            except ImportError as import_err:
                logger.error(f"MCP SDK or httpx not available: {import_err}")
                result_queue.put({
                    "success": False,
                    "output": "",
                    "error": f"Required packages not installed. Please install: pip install mcp httpx. Error: {str(import_err)}"
                })
                return
            
            if not rube_token:
                error_msg = "Rube API key not found"
                logger.error(f"[RubeTool] {error_msg}")
                result_queue.put({
                    "success": False,
                    "output": "",
                    "error": "Rube API key is required. Please configure it in settings."
                })
                return
            
            logger.info(f"[RubeTool] Rube token found (length: {len(rube_token) if rube_token else 0})")
            
            # Set environment variable for Rube token
            os.environ["RUBE_TOKEN"] = rube_token
            os.environ["COMPOSIO_API_KEY"] = rube_token
            logger.info("[RubeTool] Set RUBE_TOKEN and COMPOSIO_API_KEY environment variables")
            
            # Rube MCP server configuration
            # Rube uses Streamable HTTP transport at https://rube.app/mcp
            rube_server_url = "https://rube.app/mcp"
            logger.info(f"[RubeTool] Rube server URL: {rube_server_url}")
            
            async def execute_rube_task_async():
                try:
                    logger.info(f"[RubeTool] Starting async task execution")
                    logger.info(f"[RubeTool] Connecting to Rube MCP server at {rube_server_url}")
                    
                    # Create HTTP client with proper configuration
                    logger.info("[RubeTool] Creating httpx.AsyncClient...")
                    # Use shorter timeouts to prevent indefinite blocking
                    # Connect: 10s, Read: 60s, Write: 30s, Pool: 10s
                    async with httpx.AsyncClient(
                        timeout=httpx.Timeout(60.0, connect=10.0, read=60.0, write=30.0, pool=10.0),
                        follow_redirects=True,
                        headers={
                            "Authorization": f"Bearer {rube_token}",
                            "Content-Type": "application/json"
                        }
                    ) as http_client:
                        logger.info("[RubeTool] HTTP client created successfully")
                        
                        # Connect to Rube using Streamable HTTP transport
                        logger.info(f"[RubeTool] Connecting via streamable_http_client to {rube_server_url}...")
                        # streamable_http_client returns a tuple: (read, write, close)
                        async with streamable_http_client(
                            rube_server_url,
                            http_client=http_client
                        ) as transport_tuple:
                            logger.info(f"[RubeTool] Streamable HTTP client connected, transport type: {type(transport_tuple)}")
                            
                            # Extract read and write from the tuple
                            # The tuple structure is (read, write, close) or (read, write)
                            if isinstance(transport_tuple, tuple):
                                logger.info(f"[RubeTool] Transport is tuple with {len(transport_tuple)} elements")
                                if len(transport_tuple) >= 2:
                                    read, write = transport_tuple[0], transport_tuple[1]
                                    logger.info("[RubeTool] Extracted read and write from tuple")
                                else:
                                    error_msg = f"Transport tuple has insufficient elements: {len(transport_tuple)}"
                                    logger.error(f"[RubeTool] {error_msg}")
                                    raise ValueError(error_msg)
                            else:
                                logger.info(f"[RubeTool] Transport is not a tuple, trying to get read/write attributes")
                                # If it's not a tuple, try to get read/write attributes
                                read = getattr(transport_tuple, 'read', None)
                                write = getattr(transport_tuple, 'write', None)
                                if read is None or write is None:
                                    error_msg = f"Could not extract read/write from transport: {type(transport_tuple)}"
                                    logger.error(f"[RubeTool] {error_msg}")
                                    raise ValueError(error_msg)
                                logger.info("[RubeTool] Extracted read and write from transport attributes")
                            
                            # Create MCP session
                            logger.info("[RubeTool] Creating ClientSession...")
                            async with ClientSession(read, write) as session:
                                logger.info("[RubeTool] ClientSession created")
                                
                                # Initialize connection
                                logger.info("[RubeTool] Initializing MCP session with Rube...")
                                try:
                                    await session.initialize()
                                    logger.info("[RubeTool] MCP session initialized successfully")
                                except Exception as init_err:
                                    logger.error(f"[RubeTool] Failed to initialize MCP session: {init_err}")
                                    logger.error(f"[RubeTool] Initialization error traceback:", exc_info=True)
                                    raise
                                
                                # Search for tools using Rube's search_tools functionality
                                # We'll use the MCP protocol to call the search tool
                                logger.info(f"Searching for Rube tools for task: {task}")
                                
                                # List available tools first to see what's available
                                tools_list = await session.list_tools()
                                logger.info(f"Found {len(tools_list.tools)} available tools")
                                
                                # Look for Rube-specific tools (search_tools, multi_execute_tool, get_tool_schemas, remote_workbench)
                                logger.info("[RubeTool] Searching for Rube-specific tools (search_tools, multi_execute_tool, get_tool_schemas, remote_workbench)...")
                                search_tool = None
                                execute_tool = None
                                get_schemas_tool = None
                                workbench_tool = None
                                
                                for tool in tools_list.tools:
                                    tool_name_lower = tool.name.lower()
                                    if "search" in tool_name_lower and "tool" in tool_name_lower:
                                        search_tool = tool
                                        logger.info(f"[RubeTool] Found search tool: {tool.name}")
                                    elif "execute" in tool_name_lower and "tool" in tool_name_lower:
                                        execute_tool = tool
                                        logger.info(f"[RubeTool] Found execute tool: {tool.name}")
                                    elif "schema" in tool_name_lower and "tool" in tool_name_lower:
                                        get_schemas_tool = tool
                                        logger.info(f"[RubeTool] Found get schemas tool: {tool.name}")
                                    elif "workbench" in tool_name_lower or "remote" in tool_name_lower:
                                        workbench_tool = tool
                                        logger.info(f"[RubeTool] Found workbench tool: {tool.name}")
                                
                                if not search_tool:
                                    logger.warning("[RubeTool] No search_tools found in available tools")
                                if not execute_tool:
                                    logger.warning("[RubeTool] No multi_execute_tool found in available tools")
                                
                                # Check if this is an email/inbox query that needs special pagination handling
                                is_email_query = _is_email_inbox_query(task)
                                logger.info(f"[RubeTool] Is email/inbox query: {is_email_query}")
                                
                                # For email queries, check Gmail connection first
                                if is_email_query:
                                    logger.info("[RubeTool] Checking Gmail connection status...")
                                    manage_connections_tool = None
                                    for tool in tools_list.tools:
                                        tool_name_lower = tool.name.lower()
                                        if "manage" in tool_name_lower and "connection" in tool_name_lower:
                                            manage_connections_tool = tool
                                            logger.info(f"[RubeTool] Found manage connections tool: {tool.name}")
                                            break
                                    
                                    if manage_connections_tool:
                                        try:
                                            connection_check = await session.call_tool(
                                                manage_connections_tool.name,
                                                arguments={
                                                    "toolkits": ["gmail"]
                                                }
                                            )
                                            logger.info(f"[RubeTool] Gmail connection check completed")
                                            # Note: We continue even if connection check fails - let the actual tool call handle auth errors
                                        except Exception as conn_err:
                                            logger.warning(f"[RubeTool] Could not check Gmail connection: {conn_err}, proceeding anyway")
                                
                                # If we have search_tools, use it to find appropriate tools
                                # Workflow pattern: SEARCH_TOOLS -> (optionally CREATE_PLAN) -> MULTI_EXECUTE_TOOL or REMOTE_WORKBENCH
                                # For email queries, we use REMOTE_WORKBENCH for proper pagination
                                # For simple tasks, we skip CREATE_PLAN and execute directly
                                # For complex tasks, CREATE_PLAN could be added here
                                if search_tool:
                                    logger.info(f"[RubeTool] Using search tool: {search_tool.name}")
                                    # Prepare search parameters according to Rube MCP docs
                                    # queries is an array with use_case and optional known_fields
                                    known_fields_str = ""
                                    if clipboard_content:
                                        known_fields_str += f"clipboard_content_length:{len(clipboard_content)},"
                                    if dropped_files:
                                        known_fields_str += f"dropped_files_count:{len(dropped_files)},"
                                    if context:
                                        known_fields_str += f"context:{str(context)[:100]},"
                                    
                                    # For email queries, enhance the use_case to ensure we get Gmail tools
                                    search_use_case = task
                                    if is_email_query:
                                        search_use_case = f"list all unread Gmail messages with pagination support. {task}"
                                        known_fields_str += "query:is:unread,need_pagination:true,"
                                    
                                    search_params = {
                                        "queries": [{
                                            "use_case": search_use_case,
                                            "known_fields": known_fields_str.rstrip(",") if known_fields_str else ""
                                        }],
                                        "session": {"generate_id": True}
                                    }
                                    logger.info(f"[RubeTool] Calling search tool with params: {search_params}")
                                    try:
                                        search_result = await session.call_tool(
                                            search_tool.name,
                                            arguments=search_params
                                        )
                                        logger.info(f"[RubeTool] Search tool returned result: {type(search_result)}")
                                    except Exception as search_err:
                                        logger.error(f"[RubeTool] Failed to call search tool: {search_err}")
                                        logger.error(f"[RubeTool] Search tool error traceback:", exc_info=True)
                                        raise
                                    
                                    # Extract tools from search result
                                    logger.info("[RubeTool] Processing search result...")
                                    # The result structure may vary, so we'll handle it flexibly
                                    if search_result and search_result.content:
                                        logger.info(f"[RubeTool] Search result has content: {len(search_result.content)} items")
                                        # Parse the result - it might be text or structured data
                                        result_data = search_result.content[0].text if hasattr(search_result.content[0], 'text') else str(search_result.content[0])
                                        logger.info(f"[RubeTool] Extracted result data type: {type(result_data)}, length: {len(str(result_data)) if result_data else 0}")
                                        
                                        # Try to parse as JSON if possible
                                        import json
                                        try:
                                            if isinstance(result_data, str):
                                                logger.info("[RubeTool] Parsing result data as JSON string...")
                                                result_json = json.loads(result_data)
                                            else:
                                                logger.info("[RubeTool] Result data is already a dict/object")
                                                result_json = result_data
                                            
                                            logger.info(f"[RubeTool] Parsed JSON structure keys: {list(result_json.keys()) if isinstance(result_json, dict) else 'not a dict'}")
                                            
                                            # Extract tools and session_id from the result
                                            # Try multiple paths for session_id extraction
                                            session_id = None
                                            if "session_id" in result_json:
                                                session_id = result_json["session_id"]
                                            elif "session" in result_json:
                                                session_id = result_json["session"].get("id") if isinstance(result_json["session"], dict) else None
                                            elif "data" in result_json:
                                                data = result_json["data"]
                                                if isinstance(data, dict):
                                                    if "session_id" in data:
                                                        session_id = data["session_id"]
                                                    elif "session" in data:
                                                        session_id = data["session"].get("id") if isinstance(data["session"], dict) else None
                                                    # Try nested data path
                                                    elif "data" in data and isinstance(data["data"], dict):
                                                        if "session_id" in data["data"]:
                                                            session_id = data["data"]["session_id"]
                                                        elif "session" in data["data"]:
                                                            session_id = data["data"]["session"].get("id") if isinstance(data["data"]["session"], dict) else None
                                            
                                            if not session_id:
                                                logger.warning("[RubeTool] Could not extract session_id from search result, using default")
                                                session_id = "unit"
                                            
                                            # Extract tools from the result
                                            tools_data = result_json.get("data", {}).get("data", {}).get("results", [])
                                            if not tools_data:
                                                logger.info("[RubeTool] Trying alternative path: result_json.get('results')")
                                                tools_data = result_json.get("results", [])
                                            
                                            logger.info(f"[RubeTool] Found {len(tools_data)} tool results, session_id: {session_id}")
                                            
                                            if tools_data:
                                                tool_result = tools_data[0]
                                                tools = tool_result.get("primary_tool_slugs", [])
                                                logger.info(f"[RubeTool] Extracted tools: {tools}, session_id: {session_id}")
                                                
                                                if tools:
                                                    # For email queries, use REMOTE_WORKBENCH for pagination
                                                    if is_email_query and workbench_tool:
                                                        logger.info(f"[RubeTool] Email query detected - using REMOTE_WORKBENCH for pagination")
                                                        # Find Gmail list tool from search results
                                                        gmail_list_tool = None
                                                        for tool_slug_candidate in tools:
                                                            if "gmail" in tool_slug_candidate.lower() and ("list" in tool_slug_candidate.lower() or "fetch" in tool_slug_candidate.lower() or "search" in tool_slug_candidate.lower()):
                                                                gmail_list_tool = tool_slug_candidate
                                                                logger.info(f"[RubeTool] Found Gmail list tool: {gmail_list_tool}")
                                                                break
                                                        
                                                        if not gmail_list_tool and tools:
                                                            # Use first tool if no specific list tool found
                                                            gmail_list_tool = tools[0]
                                                            logger.info(f"[RubeTool] Using first tool as Gmail tool: {gmail_list_tool}")
                                                        
                                                        if gmail_list_tool:
                                                            # Generate workbench code for pagination
                                                            workbench_code = _generate_gmail_pagination_workbench_code(gmail_list_tool, session_id)
                                                            logger.info(f"[RubeTool] Executing workbench code for Gmail pagination...")
                                                            
                                                            try:
                                                                workbench_result = await session.call_tool(
                                                                    workbench_tool.name,
                                                                    arguments={
                                                                        "code_to_execute": workbench_code,
                                                                        "session_id": session_id,
                                                                        "current_step": "FETCHING_EMAILS",
                                                                        "current_step_metric": "0/n pages",
                                                                        "next_step": "PROCESSING_RESULTS"
                                                                    }
                                                                )
                                                                
                                                                if workbench_result and workbench_result.content:
                                                                    output = workbench_result.content[0].text if hasattr(workbench_result.content[0], 'text') else str(workbench_result.content[0])
                                                                    output_str = str(output)
                                                                    logger.info(f"[RubeTool] Workbench execution returned, output length: {len(output_str)}")
                                                                    
                                                                    # Check if the output contains error indicators
                                                                    output_lower = output_str.lower()
                                                                    if "syntaxerror" in output_lower or "syntax error" in output_lower or "syntaxerror:" in output_lower:
                                                                        logger.error(f"[RubeTool] Workbench output contains syntax error: {output_str[:500]}")
                                                                        return {"success": False, "error": f"Syntax error in workbench code: {output_str[:500]}"}
                                                                    
                                                                    logger.info(f"[RubeTool] Workbench execution successful, output length: {len(output_str)}")
                                                                    return {"success": True, "output": output_str}
                                                                else:
                                                                    error_msg = "No result returned from workbench execution"
                                                                    logger.error(f"[RubeTool] {error_msg}")
                                                                    return {"success": False, "error": error_msg}
                                                            except Exception as workbench_err:
                                                                logger.error(f"[RubeTool] Failed to execute workbench: {workbench_err}")
                                                                logger.error(f"[RubeTool] Workbench error traceback:", exc_info=True)
                                                                # Fall through to regular execution as fallback
                                                                logger.info(f"[RubeTool] Falling back to regular tool execution")
                                                    
                                                    # Execute the first tool (regular execution or fallback)
                                                    tool_slug = tools[0]
                                                    logger.info(f"[RubeTool] Executing Rube tool: {tool_slug}")
                                                    
                                                    # Prepare tool arguments
                                                    logger.info("[RubeTool] Preparing tool arguments...")
                                                    tool_args = _prepare_tool_arguments(task, clipboard_content, dropped_files, context, tool_result)
                                                    logger.info(f"[RubeTool] Tool arguments prepared: {list(tool_args.keys()) if isinstance(tool_args, dict) else type(tool_args)}")
                                                    
                                                    # Find the execute tool or use the tool directly
                                                    logger.info("[RubeTool] Finding target tool in available tools...")
                                                    target_tool = None
                                                    for tool in tools_list.tools:
                                                        if tool.name == tool_slug or tool_slug in tool.name:
                                                            target_tool = tool
                                                            logger.info(f"[RubeTool] Found target tool: {tool.name}")
                                                            break
                                                    
                                                    if target_tool:
                                                        logger.info(f"[RubeTool] Calling tool: {target_tool.name} with arguments: {tool_args}")
                                                        try:
                                                            execute_result = await session.call_tool(
                                                                target_tool.name,
                                                                arguments=tool_args
                                                            )
                                                            logger.info(f"[RubeTool] Tool execution completed, result type: {type(execute_result)}")
                                                            
                                                            if execute_result and execute_result.content:
                                                                output = execute_result.content[0].text if hasattr(execute_result.content[0], 'text') else str(execute_result.content[0])
                                                                logger.info(f"[RubeTool] Tool execution successful, output length: {len(str(output))}")
                                                                return {"success": True, "output": str(output)}
                                                            else:
                                                                error_msg = "No result returned from tool execution"
                                                                logger.error(f"[RubeTool] {error_msg}")
                                                                return {"success": False, "error": error_msg}
                                                        except Exception as exec_err:
                                                            logger.error(f"[RubeTool] Failed to execute tool: {exec_err}")
                                                            logger.error(f"[RubeTool] Tool execution error traceback:", exc_info=True)
                                                            return {"success": False, "error": f"Tool execution failed: {str(exec_err)}"}
                                                    else:
                                                        # Tool not found in MCP tools - try executing via RUBE_MULTI_EXECUTE_TOOL
                                                        logger.info(f"[RubeTool] Tool {tool_slug} not in MCP tools, trying RUBE_MULTI_EXECUTE_TOOL")
                                                        if execute_tool:
                                                            logger.info(f"[RubeTool] Using {execute_tool.name} to execute {tool_slug}")
                                                            try:
                                                                # Try to get tool schema first if available, to ensure correct arguments
                                                                final_tool_args = tool_args
                                                                if get_schemas_tool:
                                                                    try:
                                                                        logger.info(f"[RubeTool] Getting schema for {tool_slug}...")
                                                                        schema_result = await session.call_tool(
                                                                            get_schemas_tool.name,
                                                                            arguments={
                                                                                "tool_slugs": [tool_slug],
                                                                                "session_id": session_id
                                                                            }
                                                                        )
                                                                        if schema_result and schema_result.content:
                                                                            schema_text = schema_result.content[0].text if hasattr(schema_result.content[0], 'text') else str(schema_result.content[0])
                                                                            import json
                                                                            try:
                                                                                schema_data = json.loads(schema_text) if isinstance(schema_text, str) else schema_text
                                                                                # Update tool_args based on schema if needed
                                                                                logger.info(f"[RubeTool] Got schema for {tool_slug}, using it to prepare arguments")
                                                                                final_tool_args = _prepare_tool_arguments(task, clipboard_content, dropped_files, context, schema_data.get("data", {}).get(tool_slug, {}))
                                                                            except (json.JSONDecodeError, ValueError, KeyError):
                                                                                logger.warning(f"[RubeTool] Could not parse schema, using original args")
                                                                    except Exception as schema_err:
                                                                        logger.warning(f"[RubeTool] Could not get schema for {tool_slug}: {schema_err}, proceeding with original args")
                                                                
                                                                # Prepare arguments for RUBE_MULTI_EXECUTE_TOOL
                                                                # According to Rube MCP docs: tools array with tool_slug and arguments
                                                                # session_id and memory are required parameters
                                                                multi_execute_args = {
                                                                    "tools": [{
                                                                        "tool_slug": tool_slug,
                                                                        "arguments": final_tool_args
                                                                    }],
                                                                    "session_id": session_id,
                                                                    "memory": {},
                                                                    "sync_response_to_workbench": False
                                                                }
                                                                logger.info(f"[RubeTool] Calling {execute_tool.name} with args: {list(multi_execute_args.keys())}")
                                                                execute_result = await session.call_tool(
                                                                    execute_tool.name,
                                                                    arguments=multi_execute_args
                                                                )
                                                                logger.info(f"[RubeTool] Multi-execute completed, result type: {type(execute_result)}")
                                                                
                                                                if execute_result and execute_result.content:
                                                                    output = execute_result.content[0].text if hasattr(execute_result.content[0], 'text') else str(execute_result.content[0])
                                                                    logger.info(f"[RubeTool] Tool execution successful via multi-execute, output length: {len(str(output))}")
                                                                    return {"success": True, "output": str(output)}
                                                                else:
                                                                    error_msg = "No result returned from multi-execute tool"
                                                                    logger.error(f"[RubeTool] {error_msg}")
                                                                    return {"success": False, "error": error_msg}
                                                            except Exception as exec_err:
                                                                logger.error(f"[RubeTool] Failed to execute via multi-execute: {exec_err}")
                                                                logger.error(f"[RubeTool] Multi-execute error traceback:", exc_info=True)
                                                                return {"success": False, "error": f"Tool execution via multi-execute failed: {str(exec_err)}"}
                                                        else:
                                                            error_msg = f"Tool {tool_slug} not found in available tools and RUBE_MULTI_EXECUTE_TOOL not available"
                                                            logger.error(f"[RubeTool] {error_msg}")
                                                            available_tool_names = [t.name for t in tools_list.tools]
                                                            logger.error(f"[RubeTool] Available tools: {available_tool_names}")
                                                            return {"success": False, "error": error_msg}
                                                else:
                                                    error_msg = "No tools found in search results"
                                                    logger.error(f"[RubeTool] {error_msg}")
                                                    logger.error(f"[RubeTool] Tool result structure: {tool_result}")
                                                    return {"success": False, "error": error_msg}
                                            else:
                                                error_msg = "No tools found in search result"
                                                logger.error(f"[RubeTool] {error_msg}")
                                                logger.error(f"[RubeTool] Result JSON structure: {list(result_json.keys()) if isinstance(result_json, dict) else type(result_json)}")
                                                return {"success": False, "error": error_msg}
                                        except json.JSONDecodeError as json_err:
                                            logger.warning(f"[RubeTool] Result is not JSON, returning as text. JSON error: {json_err}")
                                            # If not JSON, return as text
                                            logger.info(f"[RubeTool] Returning result as text: {str(result_data)[:200]}...")
                                            return {"success": True, "output": str(result_data)}
                                    else:
                                        error_msg = "No result from search tool"
                                        logger.error(f"[RubeTool] {error_msg}")
                                        logger.error(f"[RubeTool] Search result: {search_result}")
                                        return {"success": False, "error": error_msg}
                                
                                # If we don't have search_tools, try to find and use tools directly
                                # Look for tools that match the task
                                task_lower = task.lower()
                                matching_tools = []
                                
                                for tool in tools_list.tools:
                                    tool_name_lower = tool.name.lower()
                                    tool_desc_lower = (tool.description or "").lower()
                                    
                                    # Check if tool matches task keywords
                                    if any(keyword in tool_name_lower or keyword in tool_desc_lower 
                                           for keyword in ["email", "gmail", "inbox", "message"] 
                                           if keyword in task_lower):
                                        matching_tools.append(tool)
                                
                                if matching_tools:
                                    # Use the first matching tool
                                    tool = matching_tools[0]
                                    logger.info(f"[RubeTool] Using tool: {tool.name} for task: {task}")
                                    
                                    # Prepare basic arguments
                                    logger.info("[RubeTool] Preparing tool arguments...")
                                    tool_args = _prepare_tool_arguments(task, clipboard_content, dropped_files, context, {})
                                    logger.info(f"[RubeTool] Tool arguments: {list(tool_args.keys()) if isinstance(tool_args, dict) else type(tool_args)}")
                                    
                                    logger.info(f"[RubeTool] Calling tool: {tool.name}...")
                                    try:
                                        execute_result = await session.call_tool(
                                            tool.name,
                                            arguments=tool_args
                                        )
                                        logger.info(f"[RubeTool] Tool call completed, result type: {type(execute_result)}")
                                        
                                        if execute_result and execute_result.content:
                                            output = execute_result.content[0].text if hasattr(execute_result.content[0], 'text') else str(execute_result.content[0])
                                            logger.info(f"[RubeTool] Tool execution successful, output length: {len(str(output))}")
                                            return {"success": True, "output": str(output)}
                                        else:
                                            error_msg = "No result returned from tool execution"
                                            logger.error(f"[RubeTool] {error_msg}")
                                            return {"success": False, "error": error_msg}
                                    except Exception as tool_err:
                                        logger.error(f"[RubeTool] Failed to call tool: {tool_err}")
                                        logger.error(f"[RubeTool] Tool call error traceback:", exc_info=True)
                                        return {"success": False, "error": f"Tool call failed: {str(tool_err)}"}
                                else:
                                    available_tools = [t.name for t in tools_list.tools[:10]]
                                    error_msg = f"No matching tools found for task: {task}. Available tools: {available_tools}"
                                    logger.error(f"[RubeTool] {error_msg}")
                                    return {
                                        "success": False,
                                        "error": error_msg
                                    }
                                
                except httpx.ReadTimeout as timeout_err:
                    error_msg = "Connection to Rube server timed out. The server may be slow or unavailable. Please try again."
                    logger.error(f"[RubeTool] Read timeout error: {timeout_err}")
                    logger.error(f"[RubeTool] Timeout error traceback:", exc_info=True)
                    return {
                        "success": False,
                        "error": error_msg
                    }
                except httpx.ConnectTimeout as timeout_err:
                    error_msg = "Connection to Rube server timed out while connecting. Please check your internet connection and try again."
                    logger.error(f"[RubeTool] Connect timeout error: {timeout_err}")
                    logger.error(f"[RubeTool] Timeout error traceback:", exc_info=True)
                    return {
                        "success": False,
                        "error": error_msg
                    }
                except httpx.HTTPStatusError as http_err:
                    if http_err.response.status_code in [401, 403]:
                        error_msg = "Authentication failed. Please ensure your Rube API token is valid. You may need to complete OAuth authentication in your browser."
                        logger.error(f"[RubeTool] Authentication error: {http_err}")
                        logger.error(f"[RubeTool] HTTP status: {http_err.response.status_code}")
                        logger.error(f"[RubeTool] HTTP response: {http_err.response.text}")
                        logger.error(f"[RubeTool] Authentication error traceback:", exc_info=True)
                        return {
                            "success": False,
                            "error": error_msg
                        }
                    else:
                        error_msg = f"HTTP error: {http_err.response.status_code} - {http_err.response.text}"
                        logger.error(f"[RubeTool] {error_msg}")
                        logger.error(f"[RubeTool] HTTP error traceback:", exc_info=True)
                        return {"success": False, "error": error_msg}
                except Exception as e:
                    error_msg = str(e)
                    # Check if it's a timeout-related error in the exception message
                    if "timeout" in error_msg.lower() or "ReadTimeout" in error_msg or "ConnectTimeout" in error_msg:
                        error_msg = "Connection to Rube server timed out. Please try again."
                    logger.error(f"[RubeTool] Error executing Rube task: {e}")
                    logger.error(f"[RubeTool] Full error traceback:", exc_info=True)
                    return {"success": False, "error": error_msg}
            
            # Run the async function with timeout to prevent indefinite blocking
            logger.info("[RubeTool] Running async task execution...")
            result = None  # Initialize result to avoid NameError
            async_task_timeout = 60  # 1 minute max for the entire async operation (reduced to prevent hanging)
            try:
                # Use asyncio.run with timeout wrapper
                async def run_with_timeout():
                    logger.info(f"[RubeTool] Starting async task with {async_task_timeout}s timeout...")
                    return await asyncio.wait_for(execute_rube_task_async(), timeout=async_task_timeout)
                
                logger.info(f"[RubeTool] Calling asyncio.run with {async_task_timeout}s timeout...")
                result = asyncio.run(run_with_timeout())
                logger.info(f"[RubeTool] Async task completed, success: {result.get('success', False)}")
            except asyncio.TimeoutError:
                error_msg = f"❌ Rube task timed out after {async_task_timeout} seconds. The Rube server may be slow or unresponsive. Please try again later or check your internet connection."
                logger.error(f"[RubeTool] {error_msg}")
                result = {"success": False, "error": error_msg}
            except RuntimeError as runtime_err:
                logger.warning(f"[RubeTool] RuntimeError (likely event loop issue): {runtime_err}")
                logger.info("[RubeTool] Creating new event loop...")
                try:
                    # If there's already an event loop running, create a new one
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        # Use wait_for with timeout to prevent indefinite blocking
                        async def run_with_timeout_in_loop():
                            logger.info(f"[RubeTool] Starting async task in new loop with {async_task_timeout}s timeout...")
                            return await asyncio.wait_for(execute_rube_task_async(), timeout=async_task_timeout)
                        
                        logger.info(f"[RubeTool] Calling loop.run_until_complete with {async_task_timeout}s timeout...")
                        result = loop.run_until_complete(run_with_timeout_in_loop())
                        logger.info(f"[RubeTool] Async task completed in new loop, success: {result.get('success', False)}")
                    except asyncio.TimeoutError:
                        error_msg = f"❌ Rube task timed out after {async_task_timeout} seconds. The Rube server may be slow or unresponsive. Please try again later or check your internet connection."
                        logger.error(f"[RubeTool] {error_msg}")
                        result = {"success": False, "error": error_msg}
                    finally:
                        try:
                            loop.close()
                        except Exception as close_err:
                            logger.warning(f"[RubeTool] Error closing event loop: {close_err}")
                except Exception as loop_err:
                    logger.error(f"[RubeTool] Error in RuntimeError handler: {loop_err}")
                    result = {"success": False, "error": f"Failed to run async task: {str(loop_err)}"}
            except Exception as run_err:
                error_msg = f"❌ Rube task failed: {str(run_err)}"
                logger.error(f"[RubeTool] Error running async task: {run_err}")
                logger.error(f"[RubeTool] Run error traceback:", exc_info=True)
                result = {"success": False, "error": error_msg}
            
            # Ensure result is always set
            if result is None:
                error_msg = "❌ Rube task failed: Unknown error - result was not set. The operation may have crashed or failed silently."
                logger.error(f"[RubeTool] {error_msg}")
                result = {"success": False, "error": error_msg}
            
            if result.get("success"):
                output = result.get("output", "Task completed successfully.")
                logger.info(f"[RubeTool] Task completed successfully, output length: {len(str(output))}")
                result_queue.put({
                    "success": True,
                    "output": output,
                    "error": ""
                })
            else:
                error = result.get("error", "Unknown error")
                # Ensure error message is clear and visible with ❌ prefix
                if not error.startswith("❌"):
                    error = f"❌ Rube task failed: {error}"
                logger.error(f"[RubeTool] Task failed with error: {error}")
                result_queue.put({
                    "success": False,
                    "output": "",
                    "error": error
                })
                
        except ImportError as import_error:
            logger.error(f"MCP SDK not available: {import_error}")
            result_queue.put({
                "success": False,
                "output": "",
                "error": f"MCP SDK not installed. Please install: pip install mcp"
            })
        except NameError as name_error:
            # MCP tools not found in Python environment
            logger.error(f"MCP Rube tools not available: {name_error}")
            result_queue.put({
                "success": False,
                "output": "",
                "error": (
                    "Rube MCP tools are not available in the Python environment. "
                    "The MCP server needs to be connected and the tools need to be accessible. "
                    "Please ensure the Rube MCP server is running and properly configured."
                )
            })
        except Exception as e:
            logger.error(f"Error executing Rube task: {e}", exc_info=True)
            result_queue.put({
                "success": False,
                "output": "",
                "error": f"Error executing Rube task: {str(e)}"
            })
            
    except Exception as e:
        logger.error(f"Fatal error in Rube task thread: {e}", exc_info=True)
        result_queue.put({
            "success": False,
            "output": "",
            "error": f"Fatal error: {str(e)}"
        })


def _prepare_tool_arguments(task: str, clipboard_content: Optional[str], dropped_files: Optional[list], context: Dict[str, Any], tool_info: Dict[str, Any]) -> Dict[str, Any]:
    """Prepare arguments for Rube tool execution based on task and context."""
    args = {}
    
    # Add task description
    if "task" in tool_info.get("input_schema", {}).get("properties", {}):
        args["task"] = task
    
    # Add clipboard content if available
    if clipboard_content:
        if "clipboard_content" in tool_info.get("input_schema", {}).get("properties", {}):
            args["clipboard_content"] = clipboard_content
        elif "content" in tool_info.get("input_schema", {}).get("properties", {}):
            args["content"] = clipboard_content
    
    # Add dropped files if available
    if dropped_files:
        if "file_path" in tool_info.get("input_schema", {}).get("properties", {}):
            args["file_path"] = dropped_files[0] if dropped_files else None
        elif "files" in tool_info.get("input_schema", {}).get("properties", {}):
            args["files"] = dropped_files
    
    # Add any additional context
    if context:
        args.update(context)
    
    return args


def _create_google_doc(task: str, clipboard_content: Optional[str], dropped_files: Optional[list], context: Dict[str, Any]) -> Dict[str, Any]:
    """Create a Google Doc using Rube MCP tools."""
    try:
        # Get content from clipboard or dropped file
        content_source = None
        content = None
        
        if clipboard_content:
            content_source = "clipboard"
            content = clipboard_content
        elif dropped_files:
            content_source = "dropped file"
            # Read the first dropped file
            try:
                with open(dropped_files[0], 'r', encoding='utf-8') as f:
                    content = f.read()
            except Exception as e:
                return {
                    "success": False,
                    "output": "",
                    "error": f"Could not read dropped file: {str(e)}"
                }
        else:
            return {
                "success": False,
                "output": "",
                "error": "No clipboard content or dropped files found. Please copy content to clipboard or drop a file first."
            }
        
        # Use Rube MCP tools to create Google Doc
        # Note: MCP tools are accessed through the MCP client which should be configured
        # For now, we'll return a message indicating the task was received
        # The actual MCP integration will be implemented once we confirm the MCP client setup
        try:
            # Try to use MCP tools if available
            # The MCP tools should be callable through the MCP client
            # For now, return a structured response that can be processed
            return {
                "success": True,
                "output": f"Rube will create a Google Doc with content from {content_source} ({len(content)} characters). Content ready for processing.",
                "error": "",
                "data": {
                    "content": content,
                    "content_source": content_source,
                    "task": "create_google_doc"
                }
            }
            
        except Exception as e:
            logger.error(f"Error preparing Google Doc creation: {e}", exc_info=True)
            return {
                "success": False,
                "output": "",
                "error": f"Error preparing Google Doc creation: {str(e)}"
            }
        
    except Exception as e:
        logger.error(f"Error creating Google Doc: {e}", exc_info=True)
        return {
            "success": False,
            "output": "",
            "error": str(e)
        }


def _create_calendar_event(task: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Create a calendar event using Rube MCP tools."""
    try:
        # TODO: Use actual MCP tools to create calendar event
        # Parse task for date/time information
        return {
            "success": True,
            "output": f"Rube would create a calendar event based on: {task}. (MCP integration pending)",
            "error": ""
        }
    except Exception as e:
        logger.error(f"Error creating calendar event: {e}", exc_info=True)
        return {
            "success": False,
            "output": "",
            "error": str(e)
        }


def _execute_generic_rube_task(task: str, context: Dict[str, Any], clipboard_content: Optional[str], dropped_files: Optional[list]) -> Dict[str, Any]:
    """Execute a generic Rube task using MCP tools."""
    try:
        # For generic tasks, we'll prepare the task for Rube to process
        # The actual MCP tool execution will be handled by the MCP client
        # For now, return a structured response
        task_data = {
            "task": task,
            "context": context,
            "has_clipboard": clipboard_content is not None,
            "has_dropped_files": dropped_files is not None
        }
        
        if clipboard_content:
            task_data["clipboard_length"] = len(clipboard_content)
        if dropped_files:
            task_data["dropped_files_count"] = len(dropped_files)
        
        return {
            "success": True,
            "output": f"Rube task received: {task}. Task prepared for execution via Rube MCP tools.",
            "error": "",
            "data": task_data
        }
            
    except Exception as e:
        logger.error(f"Error executing generic Rube task: {e}", exc_info=True)
        return {
            "success": False,
            "output": "",
            "error": str(e)
        }


class RubeTool(BaseTool):
    """Tool for executing tasks with Rube MCP tools in a separate thread."""
    
    name: str = "rube"
    description: str = (
        "RUBE AUTOMATION TOOL - For cross-app automation and Notion interactions.\n"
        "\n"
        "DO NOT USE FOR EMAIL/GMAIL IF GOOGLE IS CONNECTED - Use 'google_workspace' tool instead.\n"
        "CRITICAL: If Google is connected, DO NOT use this tool for email/Gmail - use 'google_workspace' instead.\n"
        "\n"
        "Rube should be used for:\n"
        "- Notion (creating pages, updating pages, any Notion operations)\n"
        "- Other cross-app automation tasks\n"
        "- Google services ONLY if Google is NOT connected (fallback)\n"
        "\n"
        "FORBIDDEN: If Google IS connected, DO NOT use this tool for:\n"
        "- Email / Gmail (CRITICAL: 'email' = Gmail, NEVER use Rube for email when Google is connected)\n"
        "- Gmail operations (sending emails, reading emails, checking email, replying to emails)\n"
        "- Google Calendar (creating events, scheduling, setting dates)\n"
        "- Google Docs (creating documents, editing documents)\n"
        "- Google Drive (uploading, saving, creating files)\n"
        "- Google Sheets (creating spreadsheets)\n"
        "- Google Slides (creating presentations)\n"
        "- ANY Google Workspace product or service\n"
        "\n"
        "REMEMBER: When user says 'email', they mean Gmail. If Google is connected, use 'google_workspace' tool, NOT this tool.\n"
        "\n"
        "Rube can also perform actions across other apps like Slack, GitHub, etc.\n"
        "\n"
        "WHEN TO USE RUBE:\n"
        "- User mentions 'notion' or 'notion app' -> ALWAYS use Rube\n"
        "- User wants to interact with Notion in any way -> ALWAYS use Rube\n"
        "- Other cross-app automation tasks (Slack, GitHub, etc.) -> Use Rube\n"
        "- Google services ONLY if Google is NOT connected (fallback) -> Use Rube\n"
        "\n"
        "WHEN NOT TO USE RUBE (USE GOOGLE_WORKSPACE INSTEAD):\n"
        "If Google is connected, DO NOT use Rube for:\n"
        "- 'email' (NOTE: email = Gmail, always use google_workspace for email when Google is connected)\n"
        "- 'gmail', 'send email', 'check email', 'read email', 'check inbox', 'check my inbox'\n"
        "- 'send an email', 'read my emails', 'inbox', ANY mention of email when Google is connected\n"
        "- 'google calendar', 'google docs', 'google drive', 'google sheets', 'google slides'\n"
        "- 'create a google doc', 'set a calendar date', 'add to my calendar'\n"
        "- ANY Google Workspace product or service\n"
        "\n"
        "EXAMPLES:\n"
        "- 'create a notion page' -> Use Rube tool\n"
        "- 'add this to notion' -> Use Rube tool\n"
        "- 'send an email' -> Use google_workspace tool (if Google connected) OR Rube (if not connected)\n"
        "- 'check my email' -> Use google_workspace tool (if Google connected) OR Rube (if not connected)\n"
        "- 'read my emails' -> Use google_workspace tool (if Google connected) OR Rube (if not connected)\n"
        "- 'create a google doc' -> Use google_workspace tool (if Google connected) OR Rube (if not connected)\n"
        "- 'set a calendar date' -> Use google_workspace tool (if Google connected) OR Rube (if not connected)\n"
        "\n"
        "The 'task' parameter is REQUIRED and should contain the complete task description.\n"
        "Rube will automatically access clipboard content or dropped files if mentioned in the task.\n"
        "\n"
        "If Rube is not configured, the tool will return 'Rube needs a key' - inform the user they need to configure Rube in settings."
    )
    args_schema: type[BaseModel] = RubeInput
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Don't check config in __init__ - check at runtime instead
    
    def get_triggers(self) -> list[str]:
        """Get trigger keywords for Rube tool."""
        return [
            "email", "gmail", "send email", "check email", "read email", "create email", "reply to email",
            "google calendar", "google docs", "google drive", "google sheets", "google slides",
            "google doc", "google document", "google spreadsheet", "google presentation",
            "create google doc", "make google doc", "create google document",
            "set calendar", "calendar date", "schedule", "add to calendar", "create calendar event",
            "notion", "notion app", "create notion", "add to notion", "notion page",
            "tell rube", "use rube", "rube create", "rube set"
        ]
    
    def _check_rube_config(self) -> tuple[bool, Optional[str]]:
        """Check if Rube is configured in settings.
        
        Requires BOTH rube_enabled=True AND rube_token to be present.
        If rube_enabled is False, Rube will not be used even if token exists.
        """
        try:
            from distr.core.utils import load_settings_from_db
            settings = load_settings_from_db()
            
            # Get values, handling None properly
            rube_enabled = settings.get('rube_enabled')
            rube_token = settings.get('rube_token')
            
            # Handle None values - convert to False/empty string
            if rube_enabled is None:
                rube_enabled = False
            if rube_token is None:
                rube_token = ''
            
            # Convert to bool if it's not already
            rube_enabled = bool(rube_enabled)
            
            # Strip whitespace from token
            if rube_token:
                rube_token = str(rube_token).strip()
            else:
                rube_token = ''
            
            # Log what we found for debugging
            logger.info(f"Rube config check: enabled={rube_enabled}, token_length={len(rube_token) if rube_token else 0}, token_exists={bool(rube_token)}")
            
            # REQUIRE BOTH: enabled must be True AND token must exist
            if not rube_enabled:
                logger.warning("Rube is disabled in settings (rube_enabled=False)")
                return False, "Rube is disabled in settings. Please enable Rube in settings to use it."
            
            if not rube_token:
                logger.warning("Rube token is missing or empty")
                return False, "Rube needs a key. Please configure Rube token in settings."
            
            # Both conditions met - Rube is configured
            logger.info(f"Rube is configured and enabled. Token length: {len(rube_token)}")
            return True, rube_token
            
        except Exception as e:
            logger.error(f"Error checking Rube config: {e}", exc_info=True)
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False, f"Error checking Rube configuration: {str(e)}"
    
    def _run(self, task: str = "", context: Optional[str] = None, **kwargs) -> str:
        """Execute Rube task synchronously (but runs in separate thread internally)."""
        # Check if Rube is configured
        is_configured, config_result = self._check_rube_config()
        if not is_configured:
            return config_result  # Returns error message
        
        rube_token = config_result  # If configured, this is the token
        
        # Create result queue
        result_queue = queue.Queue()
        
        # Prepare context
        context_dict = {}
        if context:
            context_dict["additional_context"] = context
        
        # Start thread to execute task
        thread = threading.Thread(
            target=_execute_rube_task_in_thread,
            args=(task, context_dict, result_queue, rube_token),
            daemon=True
        )
        thread.start()
        
        # Wait for result (with timeout) - reduced to prevent hanging
        timeout = 90  # 90 seconds max (1.5 minutes) - reduced from 5 minutes
        start_time = time.time()
        logger.info(f"[RubeTool] Waiting for result with {timeout}s timeout...")
        
        while time.time() - start_time < timeout:
            try:
                result = result_queue.get(timeout=1.0)
                elapsed = time.time() - start_time
                logger.info(f"[RubeTool] Received result after {elapsed:.1f}s")
                if result.get("success"):
                    return result.get("output", "Task completed successfully.")
                else:
                    error = result.get('error', 'Unknown error')
                    # Error already has ❌ prefix from thread, just return it
                    return error if error.startswith("❌") else f"❌ {error}"
            except queue.Empty:
                elapsed = time.time() - start_time
                if not thread.is_alive():
                    logger.error(f"[RubeTool] Thread died after {elapsed:.1f}s")
                    return "❌ Rube task failed: The execution thread died unexpectedly. This may indicate a crash or connection issue. Please check the logs for details."
                # Log progress every 10 seconds
                if int(elapsed) % 10 == 0 and elapsed > 0:
                    logger.info(f"[RubeTool] Still waiting... {elapsed:.0f}s elapsed, {timeout - elapsed:.0f}s remaining")
                continue
        
        elapsed = time.time() - start_time
        logger.error(f"[RubeTool] Timeout after {elapsed:.1f}s - thread may still be running")
        return f"❌ Rube task timed out after {timeout} seconds. The operation took too long and was cancelled. This may indicate the Rube server is slow or unresponsive. Please try again later."
    
    async def _arun(self, task: str = "", context: Optional[str] = None, **kwargs) -> str:
        """Async run method."""
        return self._run(task=task, context=context)

