"""
Git Operations Tool

A comprehensive tool for git operations including:
- Reading GitHub repos and READMEs without cloning
- Cloning repositories
- Pull/push operations
- Commit creation
- Branch management
"""

import logging
import os
import re
import subprocess
from typing import Optional, Any, Literal
from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class GitOperationsInput(BaseModel):
    """Input schema for git_operations tool."""
    action: Literal["read_repo", "clone", "pull", "push", "commit", "status", "branch", "checkout", "diff", "log"] = Field(
        description="The git action to perform: read_repo, clone, pull, push, commit, status, branch, checkout, diff, log"
    )
    repo_url: Optional[str] = Field(default=None, description="GitHub repository URL (for read_repo/clone actions)")
    path: Optional[str] = Field(default=None, description="Local path for the repository. If not provided and project_name is set or 'the project' is mentioned, uses the project's folder location.")
    project_name: Optional[str] = Field(default=None, description="Project name to use for git operations. If set, uses that project's folder_location as the path. Use 'current' or leave empty to use the active project.")
    branch: Optional[str] = Field(default=None, description="Branch name (for push, pull, checkout, branch actions). Defaults to current branch for push/pull.")
    message: Optional[str] = Field(default=None, description="Commit message (for commit action)")
    files: Optional[str] = Field(default=None, description="Files to stage for commit (comma-separated, or 'all' for all changes)")
    create_branch: Optional[bool] = Field(default=False, description="Create new branch when checking out (for checkout action)")


def run_git_command(args: list, cwd: str = None, timeout: int = 60) -> tuple[bool, str]:
    """
    Run a git command and return the result.
    
    Args:
        args: Git command arguments (without 'git' prefix)
        cwd: Working directory
        timeout: Command timeout in seconds
    
    Returns:
        Tuple of (success, output)
    """
    try:
        cmd = ['git'] + args
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        output = result.stdout.strip()
        if result.returncode != 0:
            error = result.stderr.strip() or output
            return False, error
        
        return True, output
    except subprocess.TimeoutExpired:
        return False, f"Command timed out after {timeout} seconds"
    except FileNotFoundError:
        return False, "Git is not installed or not in PATH"
    except Exception as e:
        return False, str(e)


def parse_github_url(url: str) -> tuple[str, str, str]:
    """
    Parse a GitHub URL to extract owner, repo, and branch.
    
    Returns:
        Tuple of (owner, repo, branch) - branch may be empty
    """
    url = url.strip().rstrip('/')
    
    # Handle various GitHub URL formats
    patterns = [
        r'github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?(?:/tree/([^/]+))?$',
        r'github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            owner = match.group(1)
            repo = match.group(2).replace('.git', '')
            branch = match.group(3) if len(match.groups()) > 2 and match.group(3) else ''
            return owner, repo, branch
    
    return '', '', ''


def fetch_github_readme(owner: str, repo: str, branch: str = 'main') -> str:
    """Fetch README content from GitHub."""
    try:
        import requests
    except ImportError:
        return "Error: requests library not installed"
    
    # Try common README filenames
    readme_files = ['README.md', 'readme.md', 'README.rst', 'README.txt', 'README']
    branches_to_try = [branch] if branch else ['main', 'master']
    
    for br in branches_to_try:
        for readme in readme_files:
            url = f"https://raw.githubusercontent.com/{owner}/{repo}/{br}/{readme}"
            try:
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    return response.text
            except Exception:
                continue
    
    return f"Could not find README in {owner}/{repo}"


def fetch_github_repo_info(owner: str, repo: str) -> dict:
    """Fetch repository information from GitHub API."""
    try:
        import requests
    except ImportError:
        return {"error": "requests library not installed"}
    
    url = f"https://api.github.com/repos/{owner}/{repo}"
    headers = {'Accept': 'application/vnd.github.v3+json'}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            return response.json()
        else:
            return {"error": f"GitHub API returned {response.status_code}"}
    except Exception as e:
        return {"error": str(e)}


def get_project_path(project_name: Optional[str] = None) -> Optional[str]:
    """
    Get the folder path for a project.
    
    Args:
        project_name: Project name to look up. If None or 'current', returns active project path.
    
    Returns:
        Project folder path, or None if not found.
    """
    try:
        from distr.core.db import get_session
        from distr.core.db.projects import Project
        from difflib import SequenceMatcher
        
        def similarity(a: str, b: str) -> float:
            return SequenceMatcher(None, a.lower(), b.lower()).ratio()
        
        session = get_session()
        try:
            project = None
            
            # If no name or 'current', get active project
            if not project_name or project_name.lower() in ('current', 'active', 'the project'):
                project = session.query(Project).filter(Project.in_use == True).first()
                if project:
                    logger.info(f"Using active project: {project.name}")
            else:
                # Fuzzy match project name
                all_projects = session.query(Project).all()
                best_match = None
                best_score = 0.0
                
                for p in all_projects:
                    score = similarity(project_name, p.name)
                    if score > best_score:
                        best_score = score
                        best_match = p
                
                if best_match and best_score >= 0.5:
                    project = best_match
                    logger.info(f"Matched '{project_name}' to project '{project.name}' (score: {best_score:.2f})")
            
            if project and project.folder_location:
                return project.folder_location
            
            return None
            
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"Error getting project path: {e}")
        return None


def fetch_github_tree(owner: str, repo: str, branch: str = 'main') -> list:
    """Fetch repository file tree from GitHub API."""
    try:
        import requests
    except ImportError:
        return []
    
    branches_to_try = [branch] if branch else ['main', 'master']
    
    for br in branches_to_try:
        url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{br}?recursive=1"
        headers = {'Accept': 'application/vnd.github.v3+json'}
        
        try:
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 200:
                data = response.json()
                return data.get('tree', [])
        except Exception:
            continue
    
    return []


class GitOperationsTool(BaseTool):
    """
    Comprehensive tool for git operations.
    
    Actions:
    - read_repo: Read a GitHub repo's README and structure without cloning
    - clone: Clone a repository to local path
    - pull: Pull changes from remote
    - push: Push changes to remote
    - commit: Stage files and create a commit
    - status: Check repository status
    - branch: List or create branches
    - checkout: Switch branches
    - diff: Show changes
    - log: Show recent commits
    """
    
    name: str = "git_operations"
    description: str = (
        "Perform git operations on repositories. Integrates with the project system - "
        "if user says 'pull the project' or 'push the project', automatically uses the active project's folder.\n\n"
        "Available actions:\n"
        "- read_repo: Read a GitHub repo's README and structure (provide repo_url)\n"
        "- clone: Clone a repository (provide repo_url and optionally path)\n"
        "- pull: Pull from remote. Use project_name='current' for active project, or specify project_name\n"
        "- push: Push to remote. Use project_name='current' for active project, or specify project_name\n"
        "- commit: Create a commit (provide message, optionally files='all', use project_name for project folder)\n"
        "- status: Check repo status (use project_name or path)\n"
        "- branch: List branches or create branch (provide branch name to create)\n"
        "- checkout: Switch branches (provide branch, optionally create_branch=true)\n"
        "- diff: Show uncommitted changes\n"
        "- log: Show recent commits\n\n"
        "IMPORTANT: When user says 'pull/push/commit the project' or mentions a project name, "
        "set project_name='current' (for active project) or project_name='<name>' to use that project's folder."
    )
    args_schema: type[BaseModel] = GitOperationsInput
    
    default_clone_path: str = Field(default="~/repos", description="Default path for cloning repos")
    
    def __init__(self, default_clone_path: str = "~/repos", **kwargs):
        super().__init__(**kwargs)
        self.default_clone_path = os.path.expanduser(default_clone_path)
    
    def _read_repo(self, repo_url: str) -> str:
        """Read a GitHub repository's README and structure."""
        owner, repo, branch = parse_github_url(repo_url)
        
        if not owner or not repo:
            return f"Could not parse GitHub URL: {repo_url}. Expected format: https://github.com/owner/repo"
        
        # Get repo info
        info = fetch_github_repo_info(owner, repo)
        if "error" in info:
            return f"Error fetching repo info: {info['error']}"
        
        # Get README
        readme = fetch_github_readme(owner, repo, branch or info.get('default_branch', 'main'))
        
        # Get file tree (limited)
        tree = fetch_github_tree(owner, repo, branch or info.get('default_branch', 'main'))
        
        # Build response
        result = []
        result.append(f"# {info.get('full_name', f'{owner}/{repo}')}")
        result.append(f"\n**Description:** {info.get('description', 'No description')}")
        result.append(f"**Stars:** {info.get('stargazers_count', 0)} | **Forks:** {info.get('forks_count', 0)}")
        result.append(f"**Language:** {info.get('language', 'Unknown')}")
        result.append(f"**Default branch:** {info.get('default_branch', 'main')}")
        result.append(f"**Clone URL:** {info.get('clone_url', repo_url)}")
        
        # Show file structure (limited to top-level and key files)
        if tree:
            result.append("\n## Repository Structure")
            dirs = set()
            files = []
            for item in tree[:100]:  # Limit items
                path = item.get('path', '')
                if item.get('type') == 'tree':
                    if '/' not in path:
                        dirs.add(path + '/')
                else:
                    if '/' not in path:
                        files.append(path)
            
            if dirs:
                result.append("**Directories:** " + ", ".join(sorted(dirs)[:15]))
            if files:
                result.append("**Files:** " + ", ".join(sorted(files)[:15]))
        
        # Add README
        result.append("\n## README\n")
        # Truncate README if too long
        if len(readme) > 8000:
            readme = readme[:8000] + "\n\n... [README truncated]"
        result.append(readme)
        
        return "\n".join(result)
    
    def _clone(self, repo_url: str, path: str = None) -> str:
        """Clone a repository."""
        owner, repo, _ = parse_github_url(repo_url)
        
        if not path:
            path = os.path.join(self.default_clone_path, repo or 'repo')
        
        path = os.path.expanduser(path)
        
        # Create parent directory if needed
        parent_dir = os.path.dirname(path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)
        
        if os.path.exists(path):
            return f"Directory already exists: {path}. Use pull to update or remove it first."
        
        logger.info(f"Cloning: {repo_url} -> {path}")
        success, output = run_git_command(['clone', repo_url, path], timeout=120)
        
        if success:
            return f"Successfully cloned {repo_url} to {path}\n{output}"
        else:
            return f"Failed to clone repository: {output}"
    
    def _pull(self, path: str, branch: str = None) -> str:
        """Pull changes from remote."""
        path = os.path.expanduser(path)
        
        if not os.path.exists(path):
            return f"Directory not found: {path}"
        
        if not os.path.exists(os.path.join(path, '.git')):
            return f"Not a git repository: {path}"
        
        args = ['pull', 'origin']
        if branch:
            args.append(branch)
        
        logger.info(f"Pulling: {path}")
        success, output = run_git_command(args, cwd=path)
        
        if success:
            return f"Pull successful:\n{output}"
        else:
            return f"Pull failed: {output}"
    
    def _push(self, path: str, branch: str = None) -> str:
        """Push changes to remote."""
        path = os.path.expanduser(path)
        
        if not os.path.exists(path):
            return f"Directory not found: {path}"
        
        if not os.path.exists(os.path.join(path, '.git')):
            return f"Not a git repository: {path}"
        
        # Get current branch if not specified
        if not branch:
            success, current = run_git_command(['rev-parse', '--abbrev-ref', 'HEAD'], cwd=path)
            if success:
                branch = current
            else:
                branch = 'main'
        
        args = ['push', 'origin', branch]
        
        logger.info(f"Pushing: {path} -> origin/{branch}")
        success, output = run_git_command(args, cwd=path)
        
        if success:
            return f"Push successful to origin/{branch}:\n{output or 'Everything up-to-date'}"
        else:
            # Check if we need to set upstream
            if 'no upstream branch' in output.lower() or 'set-upstream' in output.lower():
                success, output = run_git_command(['push', '-u', 'origin', branch], cwd=path)
                if success:
                    return f"Push successful (set upstream to origin/{branch}):\n{output}"
            return f"Push failed: {output}"
    
    def _commit(self, path: str, message: str, files: str = 'all') -> str:
        """Stage files and create a commit."""
        path = os.path.expanduser(path)
        
        if not os.path.exists(path):
            return f"Directory not found: {path}"
        
        if not os.path.exists(os.path.join(path, '.git')):
            return f"Not a git repository: {path}"
        
        if not message:
            return "Commit message is required"
        
        # Stage files
        if files == 'all':
            success, output = run_git_command(['add', '-A'], cwd=path)
        else:
            file_list = [f.strip() for f in files.split(',')]
            success, output = run_git_command(['add'] + file_list, cwd=path)
        
        if not success:
            return f"Failed to stage files: {output}"
        
        # Check if there are changes to commit
        success, status = run_git_command(['status', '--porcelain'], cwd=path)
        if success and not status:
            return "No changes to commit"
        
        # Create commit
        logger.info(f"Committing: {path}")
        success, output = run_git_command(['commit', '-m', message], cwd=path)
        
        if success:
            return f"Commit successful:\n{output}"
        else:
            return f"Commit failed: {output}"
    
    def _status(self, path: str) -> str:
        """Check repository status."""
        path = os.path.expanduser(path)
        
        if not os.path.exists(path):
            return f"Directory not found: {path}"
        
        if not os.path.exists(os.path.join(path, '.git')):
            return f"Not a git repository: {path}"
        
        success, output = run_git_command(['status'], cwd=path)
        
        if success:
            return output
        else:
            return f"Status failed: {output}"
    
    def _branch(self, path: str, branch: str = None) -> str:
        """List branches or create a new branch."""
        path = os.path.expanduser(path)
        
        if not os.path.exists(path):
            return f"Directory not found: {path}"
        
        if not os.path.exists(os.path.join(path, '.git')):
            return f"Not a git repository: {path}"
        
        if branch:
            # Create new branch
            success, output = run_git_command(['branch', branch], cwd=path)
            if success:
                return f"Created branch: {branch}"
            else:
                return f"Failed to create branch: {output}"
        else:
            # List branches
            success, output = run_git_command(['branch', '-a'], cwd=path)
            if success:
                return f"Branches:\n{output}"
            else:
                return f"Failed to list branches: {output}"
    
    def _checkout(self, path: str, branch: str, create_branch: bool = False) -> str:
        """Switch to a branch."""
        path = os.path.expanduser(path)
        
        if not os.path.exists(path):
            return f"Directory not found: {path}"
        
        if not os.path.exists(os.path.join(path, '.git')):
            return f"Not a git repository: {path}"
        
        if not branch:
            return "Branch name is required for checkout"
        
        args = ['checkout']
        if create_branch:
            args.append('-b')
        args.append(branch)
        
        success, output = run_git_command(args, cwd=path)
        
        if success:
            action = "Created and switched to" if create_branch else "Switched to"
            return f"{action} branch: {branch}\n{output}"
        else:
            return f"Checkout failed: {output}"
    
    def _diff(self, path: str) -> str:
        """Show uncommitted changes."""
        path = os.path.expanduser(path)
        
        if not os.path.exists(path):
            return f"Directory not found: {path}"
        
        if not os.path.exists(os.path.join(path, '.git')):
            return f"Not a git repository: {path}"
        
        success, output = run_git_command(['diff'], cwd=path)
        
        if success:
            if output:
                # Truncate if too long
                if len(output) > 10000:
                    output = output[:10000] + "\n\n... [diff truncated]"
                return output
            else:
                # Check staged changes
                success, staged = run_git_command(['diff', '--staged'], cwd=path)
                if staged:
                    if len(staged) > 10000:
                        staged = staged[:10000] + "\n\n... [diff truncated]"
                    return f"Staged changes:\n{staged}"
                return "No changes detected"
        else:
            return f"Diff failed: {output}"
    
    def _log(self, path: str) -> str:
        """Show recent commits."""
        path = os.path.expanduser(path)
        
        if not os.path.exists(path):
            return f"Directory not found: {path}"
        
        if not os.path.exists(os.path.join(path, '.git')):
            return f"Not a git repository: {path}"
        
        success, output = run_git_command(
            ['log', '--oneline', '--graph', '-n', '15'],
            cwd=path
        )
        
        if success:
            return f"Recent commits:\n{output}"
        else:
            return f"Log failed: {output}"
    
    def _resolve_path(self, path: Optional[str], project_name: Optional[str]) -> Optional[str]:
        """Resolve the working path from explicit path or project name."""
        # If explicit path provided, use it
        if path:
            return os.path.expanduser(path)
        
        # Try to get path from project
        if project_name:
            project_path = get_project_path(project_name)
            if project_path:
                logger.info(f"Using project folder: {project_path}")
                return project_path
        
        return None
    
    def _run(self, action: str, repo_url: str = None, path: str = None, project_name: str = None, branch: str = None, message: str = None, files: str = None, create_branch: bool = False, **kwargs) -> str:
        """
        Execute the requested git action.
        """
        action = action.lower()
        
        # Resolve path from explicit path or project name
        resolved_path = self._resolve_path(path, project_name)
        
        # Helper for path requirement error
        def path_required_error(action_name: str) -> str:
            return f"No path specified for {action_name}. Either provide 'path' directly, or set 'project_name' to use a project's folder (use 'current' for active project)."
        
        try:
            if action == "read_repo":
                if not repo_url:
                    return "repo_url is required for read_repo action"
                return self._read_repo(repo_url)
            
            elif action == "clone":
                if not repo_url:
                    return "repo_url is required for clone action"
                return self._clone(repo_url, resolved_path)
            
            elif action == "pull":
                if not resolved_path:
                    # Try to get active project as fallback
                    resolved_path = get_project_path('current')
                if not resolved_path:
                    return path_required_error("pull")
                return self._pull(resolved_path, branch)
            
            elif action == "push":
                if not resolved_path:
                    resolved_path = get_project_path('current')
                if not resolved_path:
                    return path_required_error("push")
                return self._push(resolved_path, branch)
            
            elif action == "commit":
                if not resolved_path:
                    resolved_path = get_project_path('current')
                if not resolved_path:
                    return path_required_error("commit")
                if not message:
                    return "message is required for commit action"
                return self._commit(resolved_path, message, files or 'all')
            
            elif action == "status":
                if not resolved_path:
                    resolved_path = get_project_path('current')
                if not resolved_path:
                    return path_required_error("status")
                return self._status(resolved_path)
            
            elif action == "branch":
                if not resolved_path:
                    resolved_path = get_project_path('current')
                if not resolved_path:
                    return path_required_error("branch")
                return self._branch(resolved_path, branch)
            
            elif action == "checkout":
                if not resolved_path:
                    resolved_path = get_project_path('current')
                if not resolved_path:
                    return path_required_error("checkout")
                if not branch:
                    return "branch is required for checkout action"
                return self._checkout(resolved_path, branch, create_branch)
            
            elif action == "diff":
                if not resolved_path:
                    resolved_path = get_project_path('current')
                if not resolved_path:
                    return path_required_error("diff")
                return self._diff(resolved_path)
            
            elif action == "log":
                if not resolved_path:
                    resolved_path = get_project_path('current')
                if not resolved_path:
                    return path_required_error("log")
                return self._log(resolved_path)
            
            else:
                return f"Unknown action: {action}. Valid actions: read_repo, clone, pull, push, commit, status, branch, checkout, diff, log"
                
        except Exception as e:
            logger.error(f"Git operation error: {e}", exc_info=True)
            return f"Error during {action}: {str(e)}"
    
    async def _arun(self, **kwargs) -> str:
        """Async version - just calls sync version."""
        return self._run(**kwargs)
