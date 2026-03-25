"""
Web Fetch Tool

A tool that fetches content from a URL and extracts readable text.
Useful for reading articles, documentation, or any web page content.
"""

import logging
import re
from typing import Optional, Any
from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class WebFetchInput(BaseModel):
    """Input schema for web_fetch tool."""
    url: str = Field(description="The URL to fetch content from")
    include_links: bool = Field(default=False, description="Whether to include hyperlinks in the output")


def clean_text(text: str) -> str:
    """Clean and normalize extracted text."""
    # Remove excessive whitespace
    text = re.sub(r'\s+', ' ', text)
    # Remove leading/trailing whitespace from lines
    lines = [line.strip() for line in text.split('\n')]
    # Remove empty lines
    lines = [line for line in lines if line]
    # Rejoin with single newlines
    return '\n'.join(lines)


def extract_text_from_html(html_content: str, include_links: bool = False) -> str:
    """
    Extract readable text from HTML content.
    
    Args:
        html_content: Raw HTML string
        include_links: Whether to include hyperlink URLs in output
    
    Returns:
        Cleaned text content
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.error("BeautifulSoup not installed. Install with: pip install beautifulsoup4")
        # Fallback: basic HTML tag stripping
        text = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'&nbsp;', ' ', text)
        text = re.sub(r'&amp;', '&', text)
        text = re.sub(r'&lt;', '<', text)
        text = re.sub(r'&gt;', '>', text)
        return clean_text(text)
    
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # Remove script and style elements
    for element in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'noscript', 'iframe', 'svg']):
        element.decompose()
    
    # Remove hidden elements
    for element in soup.find_all(style=re.compile(r'display:\s*none', re.IGNORECASE)):
        element.decompose()
    
    # Extract links if requested
    links = []
    if include_links:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '')
            text = a.get_text(strip=True)
            if href and text and href.startswith(('http://', 'https://')):
                links.append(f"[{text}]({href})")
    
    # Get text
    text = soup.get_text(separator='\n', strip=True)
    
    # Clean up the text
    text = clean_text(text)
    
    # Append links if requested
    if include_links and links:
        text += "\n\n--- Links ---\n"
        text += "\n".join(links[:20])  # Limit to 20 links
    
    return text


def fetch_url(url: str, timeout: int = 15) -> tuple[str, str]:
    """
    Fetch content from a URL.
    
    Args:
        url: The URL to fetch
        timeout: Request timeout in seconds
    
    Returns:
        Tuple of (content, content_type)
    """
    try:
        import requests
    except ImportError:
        raise ImportError("requests not installed. Install with: pip install requests")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
    }
    
    response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    response.raise_for_status()
    
    content_type = response.headers.get('Content-Type', 'text/html').lower()
    
    return response.text, content_type


class WebFetchTool(BaseTool):
    """
    Tool to fetch and extract content from a URL.
    
    Handles:
    - HTML pages (extracts readable text)
    - Plain text files
    - JSON content (returns formatted)
    
    Use for reading articles, documentation, or any web content.
    """
    
    name: str = "web_fetch"
    description: str = (
        "Fetch and extract content from a URL. Use this tool when you need to: "
        "1. Read the content of a specific webpage, article, or documentation page "
        "2. Get the text from a URL the user provides "
        "3. Access web content that isn't search results "
        "Input should be a valid URL starting with http:// or https://. "
        "Returns the text content of the page with HTML stripped."
    )
    args_schema: type[BaseModel] = WebFetchInput
    
    llm_service: Optional[Any] = Field(default=None, exclude=True)
    max_content_length: int = Field(default=50000, description="Maximum content length to return")
    
    def __init__(self, llm_service=None, max_content_length: int = 50000, **kwargs):
        super().__init__(**kwargs)
        self.llm_service = llm_service
        self.max_content_length = max_content_length
    
    def _normalize_url(self, url: str) -> str:
        """Ensure URL has a scheme."""
        url = url.strip()
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        return url
    
    def _run(self, url: str = "", include_links: bool = False, **kwargs) -> str:
        """
        Fetch URL content and return extracted text.
        
        Args:
            url: The URL to fetch
            include_links: Whether to include hyperlinks in output
        
        Returns:
            Extracted text content from the URL
        """
        if not url:
            return "Please provide a URL to fetch."
        
        try:
            # Normalize URL
            url = self._normalize_url(url)
            
            logger.info(f"WebFetch: Fetching content from '{url}'")
            
            # Fetch the content
            content, content_type = fetch_url(url)
            
            # Process based on content type
            if 'json' in content_type:
                # Return JSON as-is (formatted)
                import json
                try:
                    data = json.loads(content)
                    result = json.dumps(data, indent=2)
                except json.JSONDecodeError:
                    result = content
            elif 'text/plain' in content_type:
                # Plain text, return as-is
                result = content
            else:
                # Assume HTML, extract text
                result = extract_text_from_html(content, include_links=include_links)
            
            # Truncate if too long
            if len(result) > self.max_content_length:
                result = result[:self.max_content_length]
                result += f"\n\n... [Content truncated at {self.max_content_length} characters]"
            
            if not result.strip():
                return f"The page at {url} appears to be empty or contains no extractable text."
            
            logger.info(f"FETCHED: {len(result)} characters from {url}")
            return result
            
        except ImportError as e:
            logger.error(f"WebFetch import error: {e}")
            return f"Missing required library: {str(e)}"
        except Exception as e:
            error_msg = str(e)
            logger.error(f"WebFetch error for {url}: {error_msg}", exc_info=True)
            
            # Provide helpful error messages
            if "404" in error_msg:
                return f"Page not found (404): {url}"
            elif "403" in error_msg:
                return f"Access forbidden (403): {url} - The server rejected the request."
            elif "timeout" in error_msg.lower():
                return f"Request timed out while trying to fetch {url}"
            elif "ConnectionError" in error_msg or "connection" in error_msg.lower():
                return f"Could not connect to {url} - Please check if the URL is correct."
            else:
                return f"Error fetching {url}: {error_msg}"
    
    async def _arun(self, url: str = "", include_links: bool = False, **kwargs) -> str:
        """Async version - just calls sync version."""
        return self._run(url=url, include_links=include_links)
