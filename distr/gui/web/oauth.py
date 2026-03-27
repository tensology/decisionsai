"""
OAuth Server for Google OAuth Callback Handling

This server handles OAuth callbacks from Google and other OAuth providers.
It runs in a separate process and is started when the settings window opens.
"""
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import asyncio
from pathlib import Path
import logging
import multiprocessing
import uvicorn
from typing import Optional, Dict, Any
import time
import requests
import socket
import os
import json

logger = logging.getLogger(__name__)

# Server configuration
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8768  # Base port for OAuth server (increments if in use)


def load_google_oauth_config() -> Optional[Dict[str, Any]]:
    """
    Load Google OAuth client configuration from file.
    
    Looks for the file in this order:
    1. Path from GOOGLE_OAUTH_CLIENT_SECRET environment variable
    2. secrets/google_oauth_client_secret.json (relative to project root)
    3. ~/.decisionsai/google_oauth_client_secret.json
    
    Returns:
        Dict with OAuth config or None if not found
    """
    # Try environment variable first
    env_path = os.environ.get('GOOGLE_OAUTH_CLIENT_SECRET')
    if env_path and os.path.exists(env_path):
        try:
            with open(env_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading OAuth config from {env_path}: {e}")
    
    # Try project root secrets folder
    project_root = Path(__file__).parent.parent.parent.parent
    secrets_path = project_root / "secrets" / "google_oauth_client_secret.json"
    if secrets_path.exists():
        try:
            with open(secrets_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading OAuth config from {secrets_path}: {e}")
    
    # Try user home directory
    home_secrets = Path.home() / ".decisionsai" / "google_oauth_client_secret.json"
    if home_secrets.exists():
        try:
            with open(home_secrets, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading OAuth config from {home_secrets}: {e}")
    
    if not getattr(load_google_oauth_config, '_warned', False):
        logger.warning("Google OAuth client secret file not found. OAuth features will not work.")
        load_google_oauth_config._warned = True
    return None


def get_templates_dir() -> Path:
    """Get the path to the templates directory"""
    return Path(__file__).parent / "templates" / "oauth"


def load_template(template_name: str, **kwargs) -> str:
    """
    Load and render an HTML template.
    
    Args:
        template_name: Name of the template file (without .html extension)
        **kwargs: Variables to pass to the template
        
    Returns:
        Rendered HTML string
    """
    templates_dir = get_templates_dir()
    template_path = templates_dir / f"{template_name}.html"
    
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            template_content = f.read()
        
        # Render template with provided variables
        return template_content.format(**kwargs)
    except FileNotFoundError:
        logger.error(f"Template not found: {template_path}")
        return f"<html><body><h1>Error</h1><p>Template {template_name} not found.</p></body></html>"
    except Exception as e:
        logger.error(f"Error loading template {template_name}: {e}")
        return f"<html><body><h1>Error</h1><p>Error loading template: {e}</p></body></html>"


def render_page(template_name: str, title: str = "OAuth", title_color: str = "#ffffff", scripts: str = "", **kwargs) -> str:
    """
    Render a complete page using the base template and a content template.
    
    Args:
        template_name: Name of the content template (without .html extension)
        title: Page title
        title_color: Color for the h1 title
        scripts: Additional scripts to include (e.g., for success page auto-close)
        **kwargs: Additional variables for the content template
        
    Returns:
        Rendered HTML string
    """
    # Load content template
    content = load_template(template_name, **kwargs)
    
    # Load base template and insert content
    base_template = load_template("base", 
                                   title=title,
                                   title_color=title_color,
                                   content=content,
                                   scripts=scripts)
    
    return base_template


def create_oauth_app() -> FastAPI:
    """
    Create and configure the FastAPI app for OAuth callbacks
    
    Returns:
        Configured FastAPI app
    """
    app = FastAPI(title="OAuth Callback Server")
    
    # Mount static files directory
    static_dir = Path(__file__).parent / "static" / "oauth"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    
    # Load Google OAuth config at startup
    oauth_config = load_google_oauth_config()
    if oauth_config:
        web_config = oauth_config.get('web', {})
        app.state.google_client_id = web_config.get('client_id')
        app.state.google_client_secret = web_config.get('client_secret')
        app.state.google_redirect_uris = web_config.get('redirect_uris', [])
        app.state.oauth_tokens = None  # Will store tokens after exchange
        app.state.oauth_code = None  # Will store code temporarily
        logger.info("Google OAuth configuration loaded")
    else:
        app.state.google_client_id = None
        app.state.google_client_secret = None
        app.state.google_redirect_uris = []
        app.state.oauth_tokens = None
        app.state.oauth_code = None
        logger.warning("Google OAuth configuration not available")
    
    @app.get("/")
    async def root():
        """Root endpoint - health check"""
        return {
            "status": "ok",
            "service": "oauth_callback",
            "google_oauth_configured": app.state.google_client_id is not None
        }
    
    @app.get("/oauth/tokens")
    async def get_tokens():
        """Get OAuth tokens if available (for polling by client)"""
        tokens = getattr(app.state, 'oauth_tokens', None)
        code = getattr(app.state, 'oauth_code', None)
        if tokens:
            # Clear tokens after retrieval (one-time use)
            app.state.oauth_tokens = None
            app.state.oauth_code = None
            return {"tokens": tokens, "code": code}
        return {"tokens": None, "code": None}
    
    @app.get("/oauth/missing-config")
    async def missing_config(request: Request):
        """Show page for missing OAuth configuration"""
        # Get the server URL from the request
        server_url = f"{request.url.scheme}://{request.url.netloc}"
        base_url = server_url.rstrip('/')
        javascript_origin = base_url
        redirect_uri = f"{base_url}/auth/google/"
        
        html_content = render_page(
            "missing_config",
            title="OAuth Configuration Required",
            title_color="#ff8800",
            javascript_origin=javascript_origin,
            redirect_uri=redirect_uri
        )
        return HTMLResponse(content=html_content)
    
    @app.post("/oauth/upload-config")
    async def upload_config(file: UploadFile = File(...)):
        """Handle OAuth config file upload"""
        try:
            # Validate file is JSON
            if not file.filename.endswith('.json'):
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "File must be a JSON file"}
                )
            
            # Read file content
            content = await file.read()
            
            # Validate JSON
            try:
                json_data = json.loads(content.decode('utf-8'))
            except json.JSONDecodeError:
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "Invalid JSON file"}
                )
            
            # Get project root and secrets directory
            project_root = Path(__file__).parent.parent.parent.parent
            secrets_dir = project_root / "secrets"
            secrets_dir.mkdir(exist_ok=True)
            
            # Save file
            target_path = secrets_dir / "google_oauth_client_secret.json"
            with open(target_path, 'wb') as f:
                f.write(content)
            
            logger.info(f"OAuth config file saved to {target_path}")
            
            return JSONResponse(
                content={"success": True, "message": "Configuration file saved successfully"}
            )
            
        except Exception as e:
            logger.error(f"Error uploading config file: {e}")
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": str(e)}
            )
    
    @app.get("/oauth/test-connection-stream")
    async def test_connection_stream():
        """Stream Google Workspace connection test results in real-time"""
        import subprocess
        import sys
        import json
        from pathlib import Path
        
        async def generate():
            # Get the project root
            project_root = Path(__file__).parent.parent.parent.parent
            test_file = project_root / "tests" / "test_google_workspace.py"
            
            if not test_file.exists():
                yield f"data: {json.dumps({'type': 'error', 'content': 'Test file not found'})}\n\n"
                return
            
            try:
                # Start the process
                process = subprocess.Popen(
                    [sys.executable, str(test_file)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True,
                    cwd=str(project_root)
                )
                
                # Send status
                yield f"data: {json.dumps({'type': 'status', 'content': 'Running tests...'})}\n\n"
                
                # Stream output line by line
                for line in process.stdout:
                    if line:
                        # Escape the line for JSON
                        escaped_line = json.dumps(line.rstrip())[1:-1]  # Remove quotes
                        yield f"data: {json.dumps({'type': 'output', 'content': line})}\n\n"
                        await asyncio.sleep(0.01)  # Small delay to prevent overwhelming
                
                # Wait for process to complete
                process.wait()
                
                # Send completion status
                if process.returncode == 0:
                    yield f"data: {json.dumps({'type': 'done', 'content': '✓ All tests completed successfully!', 'success': True})}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'done', 'content': f'Tests completed with exit code {process.returncode}', 'success': False})}\n\n"
                    
            except Exception as e:
                logger.error(f"Error running test: {e}", exc_info=True)
                yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
        
        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )
    
    @app.get("/auth/google/")
    async def google_oauth_callback(request: Request):
        """
        Handle Google OAuth callback
        
        This endpoint receives the authorization code from Google OAuth flow.
        """
        # Get query parameters
        code = request.query_params.get("code")
        error = request.query_params.get("error")
        state = request.query_params.get("state")
        
        if error:
            logger.error(f"Google OAuth error: {error}")
            # Get the actual redirect URI that was used
            actual_redirect_uri = f"{request.url.scheme}://{request.url.netloc}/auth/google/"
            error_details = error
            if "redirect_uri_mismatch" in error.lower():
                error_details = f"{error}\n\nExpected redirect URI: {actual_redirect_uri}\n\nMake sure this exact URL is added to Google Cloud Console in the 'Authorized redirect URIs' section."
            html_content = render_page(
                "error",
                title="OAuth Error",
                title_color="#ff4444",
                error_message=error_details
            )
            return HTMLResponse(content=html_content, status_code=400)
        
        if code:
            logger.info(f"Google OAuth callback received with code (state: {state})")
            
            # Exchange authorization code for tokens
            if not hasattr(request.app.state, 'google_client_id') or not request.app.state.google_client_id:
                html_content = render_page(
                    "config_error",
                    title="OAuth Error",
                    title_color="#ff4444"
                )
                return HTMLResponse(content=html_content, status_code=500)
            
            # Exchange code for tokens
            token_response = None
            try:
                token_uri = "https://oauth2.googleapis.com/token"
                actual_redirect_uri = f"{request.url.scheme}://{request.url.netloc}/auth/google/"
                token_data = {
                    'code': code,
                    'client_id': request.app.state.google_client_id,
                    'client_secret': request.app.state.google_client_secret,
                    'redirect_uri': actual_redirect_uri,
                    'grant_type': 'authorization_code'
                }
                
                token_response = requests.post(token_uri, data=token_data)
                token_response.raise_for_status()
                tokens = token_response.json()
                
                # Store tokens in app state temporarily (will be picked up by AdvancedTab)
                request.app.state.oauth_tokens = tokens
                request.app.state.oauth_code = code
                
                logger.info("Successfully exchanged code for tokens")
                
                html_content = render_page(
                    "success",
                    title="OAuth Success",
                    title_color="#ffffff"
                )
                return HTMLResponse(content=html_content)
            except Exception as e:
                logger.error(f"Failed to exchange code for tokens: {e}")
                # Get the actual redirect URI that was used
                actual_redirect_uri = f"{request.url.scheme}://{request.url.netloc}/auth/google/"
                error_msg = str(e)
                # Check if it's a redirect_uri mismatch error
                if token_response is not None:
                    try:
                        error_data = token_response.json()
                        if 'error' in error_data:
                            error_msg = error_data.get('error_description', error_data.get('error', str(e)))
                            if 'error' in error_data:
                                error_msg = f"{error_data['error']}: {error_msg}"
                    except (json.JSONDecodeError, ValueError, KeyError):
                        pass
                if "redirect_uri_mismatch" in error_msg.lower() or "invalid_grant" in error_msg.lower():
                    error_msg = (
                        f"{error_msg}\n\n"
                        f"Redirect URI used: {actual_redirect_uri}\n\n"
                        f"Make sure this EXACT URL is added to Google Cloud Console in the "
                        f"'Authorized redirect URIs' section.\n\n"
                        f"Go to: https://console.cloud.google.com/auth/clients"
                    )
                html_content = render_page(
                    "token_exchange_failed",
                    title="OAuth Error",
                    title_color="#ff4444",
                    error_message=error_msg
                )
                return HTMLResponse(content=html_content, status_code=500)
        
        html_content = render_page(
            "callback_error",
            title="OAuth Callback",
            title_color="#ff8800"
        )
        return HTMLResponse(content=html_content, status_code=400)
    
    return app


class OAuthServer:
    """
    Manages the OAuth FastAPI server lifecycle in a separate process
    """
    def __init__(self, host: str = DEFAULT_HOST, port: Optional[int] = None):
        self.host = host
        
        # Start from base port and increment if needed
        if port is None:
            self.port = DEFAULT_PORT
        else:
            self.port = port
        
        self.app: Optional[FastAPI] = None
        self.server_process: Optional[multiprocessing.Process] = None
        self.is_running = False
    
    def _run_server_process(self, host: str, port: int):
        """Run the server in a separate process (entry point for multiprocessing)"""
        try:
            # Create FastAPI app
            app = create_oauth_app()
            
            config = uvicorn.Config(
                app,
                host=host,
                port=port,
                log_level="info",
                reload=False
            )
            server = uvicorn.Server(config)
            server.run()
        except Exception as e:
            logger.error(f"OAuth server process error: {e}")
    
    def start(self):
        """Start the server in a separate process"""
        if self.is_running and self.server_process and self.server_process.is_alive():
            logger.warning("OAuth server is already running")
            return
        
        # Clean up any existing process first
        if self.server_process and self.server_process.is_alive():
            logger.warning("Stopping existing OAuth server process before starting new one")
            self.stop()
            time.sleep(0.5)  # Wait for process to fully terminate
        
        try:
            # Check if port is already in use and try to find an available port
            test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            original_port = self.port
            port_found = False
            
            # Try up to 10 ports starting from DEFAULT_PORT
            for _ in range(10):
                try:
                    test_socket.bind((self.host, self.port))
                    test_socket.close()
                    port_found = True
                    if self.port != original_port:
                        logger.info(f"Port {original_port} was in use, using port {self.port} instead")
                    break
                except OSError:
                    self.port += 1
                    test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            
            if not port_found:
                logger.error("Could not find an available port for OAuth server")
                self.is_running = False
                return
            
            # Start server in a separate process
            self.server_process = multiprocessing.Process(
                target=self._run_server_process,
                args=(self.host, self.port),
                daemon=True
            )
            self.server_process.start()
            self.is_running = True
            logger.info(f"OAuth server process started on {self.host}:{self.port}")
            
            # Wait a moment for the server to initialize
            time.sleep(0.5)
            
        except Exception as e:
            logger.error(f"Failed to start OAuth server: {e}")
            self.is_running = False
    
    def stop(self):
        """Stop the server process"""
        if not self.is_running or self.server_process is None:
            return
        
        try:
            if self.server_process.is_alive():
                self.server_process.terminate()
                # Wait for process to terminate (with timeout)
                self.server_process.join(timeout=2.0)
                if self.server_process.is_alive():
                    # Force kill if it didn't terminate gracefully
                    logger.warning("OAuth server process didn't terminate, forcing kill")
                    self.server_process.kill()
                    self.server_process.join()
            self.is_running = False
            logger.info("OAuth server process stopped")
        except Exception as e:
            logger.error(f"Error stopping OAuth server: {e}")
            self.is_running = False
    
    def get_url(self) -> str:
        """Get the base URL of the server"""
        return f"http://{self.host}:{self.port}"
    
    def get_callback_url(self) -> str:
        """Get the Google OAuth callback URL"""
        return f"{self.get_url()}/auth/google/"
    
    def is_ready(self) -> bool:
        """Check if the server is running and ready by attempting to connect"""
        if not self.is_running or self.server_process is None:
            return False
        
        # Check if process is still alive
        if not self.server_process.is_alive():
            self.is_running = False
            return False
        
        # Try to connect to the server
        try:
            url = f"http://{self.host}:{self.port}/"
            response = requests.get(url, timeout=0.5)
            return response.status_code == 200
        except Exception:
            return False

