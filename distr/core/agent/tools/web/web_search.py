"""
Web Search Tool

A tool that searches the web for information including weather, news, 
people lookups, and general queries. Plays a search sound when searching.
"""

import logging
import os
import platform
import subprocess
from typing import Optional, Any

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class WebSearchInput(BaseModel):
    """Input schema for web_search tool."""
    query: str = Field(description="The search query or question to search for on the web")

# Path to search sound - navigate from tools/web/ up to project root (DecisionsAI)
# web_search.py -> web/ -> tools/ -> distr/ -> agent/ -> distr/ -> project root
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_TOOLS_DIR)))))
SEARCH_SOUND_PATH = os.path.join(_PROJECT_ROOT, "assets", "sounds", "search.mp3")


def play_search_sound():
    """Play the search sound effect."""
    try:
        if not os.path.exists(SEARCH_SOUND_PATH):
            logger.warning(f"Search sound not found at: {SEARCH_SOUND_PATH}")
            return
        
        system = platform.system()
        if system == "Darwin":  # macOS
            subprocess.Popen(
                ['afplay', SEARCH_SOUND_PATH],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        elif system == "Windows":
            subprocess.Popen(
                ['ffplay', '-nodisp', '-autoexit', '-loglevel', 'quiet', SEARCH_SOUND_PATH],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:  # Linux
            players = [['paplay', SEARCH_SOUND_PATH], ['aplay', SEARCH_SOUND_PATH],
                      ['mpg123', SEARCH_SOUND_PATH], ['ffplay', '-nodisp', '-autoexit', SEARCH_SOUND_PATH]]
            for player_cmd in players:
                try:
                    subprocess.Popen(
                        player_cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    break
                except FileNotFoundError:
                    continue
        logger.info("Played search sound")
    except Exception as e:
        logger.warning(f"Could not play search sound: {e}")


def search_web(query: str, max_results: int = 5) -> list:
    """Search the web via DDG. Returns list of dicts with title/body/href."""
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))
    except ImportError:
        logger.error("ddgs not installed. Install with: pip install ddgs")
        return []
    except Exception as e:
        logger.error("Web search error: %s", e)
        return []


def search_news(query: str, max_results: int = 5) -> list:
    """Search news via DDG."""
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            return list(ddgs.news(query, max_results=max_results))
    except ImportError:
        logger.error("ddgs not installed. Install with: pip install ddgs")
        return []
    except Exception as e:
        error_str = str(e)
        if "ratelimit" in error_str.lower():
            logger.warning("DDG rate limit hit, returning empty results")
        else:
            logger.error("News search error: %s", e)
        return []


def get_weather(location: str) -> dict:
    """Get weather for a location using wttr.in (free, no API key)."""
    try:
        import requests
        
        # wttr.in provides weather in various formats
        # format=j1 returns JSON
        url = f"https://wttr.in/{location}?format=j1"
        response = requests.get(url, timeout=10, headers={'User-Agent': 'curl'})
        
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"Weather API returned status {response.status_code}")
            return {}
    except ImportError:
        logger.error("requests not installed")
        return {}
    except Exception as e:
        logger.error(f"Weather API error: {e}")
        return {}


def format_weather_response(weather_data: dict, location: str) -> str:
    """Format weather data into a conversational response."""
    try:
        if not weather_data:
            return f"I couldn't get the weather for {location}. Try being more specific with the location."
        
        current = weather_data.get('current_condition', [{}])[0]
        area = weather_data.get('nearest_area', [{}])[0]
        
        # Extract info
        temp_c = current.get('temp_C', 'unknown')
        temp_f = current.get('temp_F', 'unknown')
        feels_like_c = current.get('FeelsLikeC', temp_c)
        feels_like_f = current.get('FeelsLikeF', temp_f)
        condition = current.get('weatherDesc', [{}])[0].get('value', 'unknown conditions')
        humidity = current.get('humidity', 'unknown')
        wind_speed = current.get('windspeedKmph', 'unknown')
        wind_dir = current.get('winddir16Point', '')
        
        # Get location name
        city = area.get('areaName', [{}])[0].get('value', location)
        country = area.get('country', [{}])[0].get('value', '')
        
        location_str = f"{city}, {country}" if country else city
        
        response = f"The weather in {location_str} is currently {condition.lower()}. "
        response += f"It's {temp_c}°C or {temp_f}°F, and feels like {feels_like_c}°C. "
        response += f"Humidity is at {humidity}% with winds from the {wind_dir} at {wind_speed} kilometers per hour."
        
        return response
    except Exception as e:
        logger.error(f"Error formatting weather: {e}")
        return f"I found weather data for {location} but had trouble formatting it."


def format_search_results(results: list, query: str) -> str:
    """Format search results into a conversational response."""
    if not results:
        return f"I couldn't find any information about '{query}'."
    
    # Build a conversational summary
    response_parts = []
    
    for i, result in enumerate(results[:3]):  # Top 3 results
        title = result.get('title', '')
        body = result.get('body', result.get('description', ''))
        
        if body:
            # Clean up and truncate
            body = body.strip()
            if len(body) > 200:
                body = body[:200] + "..."
            response_parts.append(body)
    
    if response_parts:
        # Combine into a flowing response
        combined = " ".join(response_parts)
        return f"Here's what I found about '{query}': {combined}"
    else:
        return f"I found some results for '{query}' but couldn't extract the details."


def format_news_results(results: list, topic: str) -> str:
    """Format news results into a conversational response."""
    if not results:
        return f"I couldn't find any recent news about '{topic}'."
    
    response_parts = [f"Here's the latest news about '{topic}':"]
    
    for i, result in enumerate(results[:4]):  # Top 4 news items
        title = result.get('title', '')
        source = result.get('source', '')
        date = result.get('date', '')
        
        if title:
            news_item = title
            if source:
                news_item += f" from {source}"
            response_parts.append(f"{i+1}. {news_item}")
    
    return " ".join(response_parts)


class WebSearchTool(BaseTool):
    """
    Tool to search the web for information.
    
    Handles:
    - Weather queries ("what's the weather in London")
    - News queries ("what's the news about AI")
    - People/things lookup ("who is Elon Musk", "what is quantum computing")
    - General questions ("how does X work", "why is Y")
    """
    
    name: str = "web_search"
    description: str = (
        "Search the web for current information. Use this tool when the user asks about: "
        "1. Weather - 'what's the weather in [location]', 'is it raining in [city]' "
        "2. News - 'what's the news', 'latest news about [topic]' "
        "3. People - 'who is [person]', 'tell me about [person]' "
        "4. Current events - anything that requires up-to-date information from the internet. "
        "IMPORTANT: Do NOT use this tool for general knowledge, definitions, or lists of common things (e.g. 'list 3 foods'). "
        "Only search if you cannot answer from your own knowledge."
    )
    args_schema: type[BaseModel] = WebSearchInput
    
    llm_service: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, llm_service=None, **kwargs):
        super().__init__(**kwargs)
        self.llm_service = llm_service
    
    def _detect_query_type(self, query: str) -> str:
        """Detect the type of query to route to appropriate search method."""
        query_lower = query.lower()
        
        # Weather patterns
        weather_keywords = ['weather', 'temperature', 'forecast', 'raining', 'sunny', 'cloudy', 
                          'snowing', 'hot', 'cold', 'humid', 'wind', 'storm']
        if any(kw in query_lower for kw in weather_keywords):
            return 'weather'
        
        # News patterns
        news_keywords = ['news', 'latest', 'headlines', 'breaking', 'update on', 'happening']
        if any(kw in query_lower for kw in news_keywords):
            return 'news'
        
        # Default to general search
        return 'general'
    
    def _extract_location(self, query: str) -> str:
        """Extract location from a weather query."""
        query_lower = query.lower()
        
        # Common patterns
        patterns = [
            'weather in ', 'weather for ', 'weather at ',
            'temperature in ', 'temperature at ',
            'forecast for ', 'forecast in ',
            'raining in ', 'snowing in ', 'sunny in ',
            'like in ', 'like at '
        ]
        
        for pattern in patterns:
            if pattern in query_lower:
                idx = query_lower.find(pattern) + len(pattern)
                location = query[idx:].strip()
                # Clean up trailing punctuation
                location = location.rstrip('?.,!')
                return location
        
        # Fallback - try to find a capitalized word that might be a location
        words = query.split()
        for word in words:
            if word[0].isupper() and word.lower() not in ['what', 'how', 'is', 'the', 'weather', 'in', 'at', 'for']:
                return word
        
        return ''
    
    def _extract_news_topic(self, query: str) -> str:
        """Extract topic from a news query."""
        query_lower = query.lower()
        
        # Remove common news-related words to get the topic
        remove_words = ['news', 'latest', 'headlines', 'breaking', 'about', 'on', 'the', 
                       'what', 'is', 'are', 'tell', 'me', 'any', "what's", 'whats']
        
        words = query.split()
        topic_words = [w for w in words if w.lower() not in remove_words]
        
        return ' '.join(topic_words).strip('?.,!') or 'today'
    
    def _run(self, query: str = "", **kwargs) -> str:
        """
        Search the web and return results.
        
        Args:
            query: The search query or question
        
        Returns:
            Conversational summary of search results
        """
        if not query:
            return "What would you like me to search for?"
        
        try:
            # Play search sound
            logger.info(f"WEB SEARCH: '{query}'")
            play_search_sound()
            
            # Detect query type
            query_type = self._detect_query_type(query)
            logger.info(f"WebSearch: Query type detected as '{query_type}' for: {query}")
            
            if query_type == 'weather':
                # Extract location and get weather
                location = self._extract_location(query)
                if not location:
                    location = "current location"
                
                logger.info(f"WebSearch: Getting weather for '{location}'")
                weather_data = get_weather(location)
                response = format_weather_response(weather_data, location)
                
            elif query_type == 'news':
                # Extract topic and search news
                topic = self._extract_news_topic(query)
                logger.info(f"WebSearch: Searching news for '{topic}'")
                results = search_news(topic)
                response = format_news_results(results, topic)
                
            else:
                # General web search
                logger.info(f"WebSearch: General search for '{query}'")
                results = search_web(query)
                response = format_search_results(results, query)
            
            logger.info(f"SEARCH COMPLETE: {len(response)} chars")
            return response
            
        except Exception as e:
            logger.error(f"WebSearch error: {e}", exc_info=True)
            return f"I had trouble searching for that. Error: {str(e)}"
    
    async def _arun(self, query: str = "", **kwargs) -> str:
        """Async version - just calls sync version."""
        return self._run(query=query)

