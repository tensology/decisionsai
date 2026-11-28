"""Deterministic tool-intent hints for obvious user requests.

Semantic retrieval is useful for fuzzy requests, but common operational
commands should not depend on embedding luck.  These hints force key tools into
the candidate set while still leaving the LLM room to choose and fill args.
"""

from __future__ import annotations

import re

try:
    from distr.core.agent.ticket_intent import classify_ticket_intent
except Exception:  # pragma: no cover - routing should never fail on import noise
    classify_ticket_intent = None


_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "clipboard_action",
        (
            r"\bclipboard\b",
            r"\bwhat\s+I\s+copied\b",
            r"\bread\s+(this|that|it|selected|the\s+selection|selected\s+text)\b",
        ),
    ),
    (
        "client_mail_proposal",
        (
            r"\b(e-?mail|gmail|mail|ticket)\b.{0,80}\b(proposal|thread)\b",
            r"\b(proposal|thread)\b.{0,80}\b(e-?mail|gmail|mail)\b",
            r"\b(check|see|make\s+sure|confirm)\b.{0,30}\bthat'?s?\b.{0,20}\bproposal\b",
            r"\b(create|make|turn)\b.{0,25}\b(it|that|this)\b.{0,25}\b(into\s+)?(a\s+)?proposal\b",
            r"\bcreate\s+(?:it\s+)?(?:as\s+)?a\s+proposal\b",
        ),
    ),
    (
        "initiative_attention",
        (
            r"\b(ignore|stop\s+worrying\s+about|don'?t\s+worry\s+about|set\s+aside|forget\s+about|park)\b.{0,40}\b(client|customer|project|sender|contact)\b",
            r"\b(client|customer|project)\b.{0,40}\b(ignore|set\s+aside|on\s+hold|not\s+a\s+priority)\b",
            r"\blink\b.{0,40}\b(e-?mail|address|inbox|account|mailbox)\b.{0,40}\bproject\b",
            r"\b(e-?mail|address|inbox|account|mailbox)\b.{0,40}\b(to|with)\b.{0,20}\bproject\b.{0,10}\blink",
            r"\b(who|which\s+clients?)\b.{0,30}\b(waiting|set\s+aside|ignored)\b",
        ),
    ),
    (
        "whatsapp_toolkit",
        (
            r"\b(whats\s*app|whatsapp)\b.*\b(messages?|chat|thread|contact|screenshots?|photos?|media|read|search|find|ingest|analy[sz]e|draft|reply|send|status|stories)\b",
            r"\b(search|find|read|show|ingest|analy[sz]e|draft|write|send|post|publish)\b.*\b(whats\s*app|whatsapp)\b",
        ),
    ),
    (
        "codex_thread_context",
        (
            r"\b(codex|codecs)\b.*\b(conversations?|threads?|chats?|sessions?|transcripts?|history)\b",
            r"\b(conversations?|threads?|chats?|sessions?|transcripts?|history)\b.*\b(codex|codecs)\b",
            r"\b(codex|codecs)\b.*\b(work\s+with|bring\s+in|pull\s+in|load|summari[sz]e|turn\s+.*\b(ticket|plan|skill)|what\s+happened)\b",
            r"\b(what\s+am\s+i\s+doing|where\s+am\s+i|workload|working\s+on)\b.*\b(codex|codecs)\b",
        ),
    ),
    (
        "ide_thread",
        (
            r"\b(cursor|codex|codecs)\b.*\b(thread|chat|session|response|reply|doing|status|latest)\b",
            r"\b(read|check|what\s+did|latest)\b.*\b(cursor|codex|codecs)\b",
            r"\b(send|prompt|continue|amend|resume)\b.*\b(cursor|codex|codecs)\b.*\b(thread|chat|session)\b",
            r"\bwhat\s+is\s+(cursor|codex|codecs)\b.*\b(doing|working)\b",
        ),
    ),
    (
        "work_ops",
        (
            r"\b(what'?s?\s+coming\s+in|check\s+(?:my\s+)?(?:work|inbox|intake)|work\s+intake)\b",
            r"\b(intake|stage)\b.+\b(jira|email|gmail|tickets?)\b",
            r"\b(run|start|execute)\b.+\b(ticket|issue)\b\s*#?\d+",
            r"\b(run|start)\b.+\bticket\b",
            r"\b(work\s+status|status\s+of\s+(?:the\s+)?(?:work|ticket)|where\s+is\s+ticket)\b",
            r"\b(show|read)\b.+\b(client\s+)?draft\b.+\bticket\b",
            r"\b(send|approve)\b.+\b(client|draft)\b",
            r"\bsend\s+(?:it\s+)?to\s+the\s+client\b",
        ),
    ),
    (
        "proactive_orchestrator",
        (
            r"\b(proactive|morning|lunch|evening|check\s+work|work\s+coming\s+in|prioriti[sz]e|what\s+is\s+important)\b.*\b(gmail|slack|whats\s*app|telegram|trello|jira|boards?|codex|codecs|cursor|project)\b",
            r"\b(gmail|slack|whats\s*app|telegram|trello|jira|boards?)\b.*\b(prioriti[sz]e|important|check|scan|work\s+coming\s+in|what\s+matters)\b",
            r"\b(daily\s+plan|day\s+plan|today'?s\s+plan|plan\s+my\s+day|morning\s+brief|what\s+should\s+i\s+do\s+today|what'?s\s+my\s+plan)\b",
            r"\b(plan|prioriti[sz]e)\b.*\b(today|my\s+day|daily|emails?|gmail|whats\s*app|tickets?|boards?|projects?|workflows?)\b",
            r"\b(where\s+am\s+i|what\s+am\s+i\s+doing|workload|working\s+on)\b.*\b(cursor|codex|codecs)\b",
            r"\b(cursor|codex|codecs)\b.*\b(workload|working\s+on|what\s+am\s+i\s+doing|where\s+am\s+i)\b",
            r"\b(turn\s+on|enable|start|set\s+up|setup|activate)\b.+\b(jira)\b.+\b(morning\s+)?(intake|automation|batch|notifications?|email)\b",
            r"\b(jira)\b.+\b(morning\s+)?(intake|automation)\b.+\b(turn\s+on|enable|start|set\s+up|setup|activate)\b",
            r"\b(turn\s+on|enable|start)\b.+\bjira\b.+\b(tickets?|issues?)\b.+\b(from\s+)?(email|gmail|telegram)\b",
            r"\b(run|do|start)\b.+\bjira\b.+\b(intake|morning\s+intake|email\s+batch)\b\s*(now)?\b",
        ),
    ),
    (
        "clipboard_action",
        (
            r"\b(what'?s?|what\s+is|show|get|read|see)\s+(?:in|on)?\s*(?:my\s+|the\s+)?clipboard\b",
            r"\b(?:read|ingest|consume|inspect|load|get)\b.*\b(?:my\s+|the\s+)?clipboard\b",
            r"\b(read|inspect|check|look\s+at|review|load)\b.*\bclipboard\b.*\b(talk|discuss|go\s+through|about\s+it|with\s+me)\b",
            r"\b(explain|elaborate|summari[sz]e|rework|rewrite)\s+(?:on\s+)?this\b",
            r"\b(?:set|write|put|copy)\s+(?:the\s+)?clipboard\s+(?:to|as)\b",
            r"\b(?:set|write|put|copy)\s+.+\s+(?:to|into|onto)\s+(?:my\s+|the\s+)?clipboard\b",
        ),
    ),
    (
        "window_management",
        (
            r"\b(minimi[sz]e|maximi[sz]e|restore|unminimi[sz]e|full\s*screen|hide|focus)\b.*\b(window|app|application|terminal|codex|codecs|spotify|finder|chrome|safari)\b",
            r"\b(window|app|application|terminal|codex|codecs|spotify|finder|chrome|safari)\b.*\b(minimi[sz]e|maximi[sz]e|restore|unminimi[sz]e|full\s*screen|hide|focus)\b",
            r"\b(move|send|put)\b.*\b(window|terminal|codex|codecs|app|application)\b.*\b(screen|monitor|display|desktop|space)\b",
            r"\b(move|send|put)\b.*\b(left|right|cent(?:er|re)|second|third)\b.*\b(screen|monitor|display|desktop|space)\b",
            r"\b(open|switch|go|take\s+me)\b.*\b(first|second|third|\d+)\s+(desktop|space)\b",
            r"\b(list|show)\b.*\b(desktops?|spaces?)\b",
            r"^(?:please\s+|can\s+you\s+)?(?:minimi[sz]e|maximi[sz]e|full\s*screen|restore)\s+(?:this|the)\s+window\b",
        ),
    ),
    (
        "launch_app",
        (
            r"\b(open|launch|start|bring\s+up|show|focus|activate|switch\s+to|go\s+back\s+to|return\s+to)\b.*\b(spotify|music|terminal|finder|safari|chrome|brave|codex|codecs|cursor|slack|notes|calculator|textedit)\b",
        ),
    ),
    (
        "media_control",
        (
            r"\b(reload|refresh)\b.{0,40}\b(brave(?:\s+browser)?|google\s+chrome|chrome|safari|firefox)\b",
            r"\b(brave(?:\s+browser)?|google\s+chrome|chrome|safari|firefox)\b.{0,40}\b(reload|refresh)\b",
        ),
    ),
    (
        "message_agent",
        (
            r"\b(ask|tell|message|ping|check\s+with|consult|delegate\s+(?:this\s+|it\s+)?to|hand\s+(?:this|it)\s+(?:over\s+)?to)\b.{0,60}\b(?:the\s+)?(?:other\s+)?agents?\b",
            r"(?:^|\s)@[a-z][\w-]+",
        ),
    ),
    (
        "web_browser",
        (
            r"\bhttps?://\S+",
            r"\b(?:in|with|using)\s+(?:the\s+|a\s+)?(?:web\s+)?browser\b",
            r"\b(browse|visit|go\s+to|open|navigate\s+to|check)\b.{0,40}\b(website|web\s*site|web\s*page|site|url|www\.\S+|\w+\.(?:com|org|net|io|co\.za|dev|ai))\b",
            r"\b(log\s*in|sign\s*in)\b.{0,30}\b(website|site|portal)\b",
        ),
    ),
    (
        "calendar_blocks",
        (
            # Decisions' own /calendar (schedule blocks). Google Calendar
            # requests stay with google_workspace.
            r"^(?!.*\b(?:google|gmail|g\s*cal)\b).*\b(?:my|the|decisions)\s+calendar\b",
            r"(?:^|\s)/calendar\b",
            r"\b(time|schedule)\s*blocks?\b",
            r"^(?!.*\b(?:google|gmail)\b).*\b(block\s+out|book\s+(?:me\s+)?(?:some\s+)?time|schedule\s+(?:me\s+)?(?:some\s+)?time)\b",
        ),
    ),
    (
        "snippets",
        (
            r"\bsnippets?\b",
        ),
    ),
    (
        "development_overview",
        (
            r"\b(development|dev)\b.{0,40}\b(status|overview|threads?|work\s+items?|progress|going\s+on)\b",
            r"\bwhat(?:'s|\s+is|\s+are)\b.{0,30}\b(?:the\s+)?(?:dev(?:elopment)?\s+)?threads?\b.{0,20}\b(doing|status|working\s+on)\b",
        ),
    ),
    (
        "select_thread",
        (
            r"\b(select|pick|highlight|choose)\b.{0,60}\b(threads?|conversations?|chats?)\b",
            r"\b(focus\s+on|jump\s+to|go\s+to|switch\s+to|show(?:\s+me)?)\b.{0,40}\b(latest|newest|most\s+recent|last)?\s*(?:development\s+)?threads?\b",
        ),
    ),
    (
        "open_thread",
        (
            r"\b(open(?:\s+up)?|pull\s+up|bring\s+up|launch)\b.{0,60}\b(threads?)\b",
            r"\bopen\b.{0,40}\b(latest|newest|most\s+recent|last)\b.{0,20}\b(chat|conversation)\b",
        ),
    ),
    (
        "open_page",
        (
            r"\b(open(?:\s+up)?|show(?:\s+me)?|pull\s+up|launch|go\s+to|bring\s+up)\b.{0,80}\b(chat|web\s*ui|web\s*interface|development(?:\s+section)?|preferences?|settings)\b",
        ),
    ),
    (
        "open_project",
        (
            r"\b(open|bring\s+up|show)\b.{0,20}\b(?:the\s+|this\s+|a\s+|my\s+)?project\b(?!s\b)(?!\s+ticket)",
        ),
    ),
    (
        "start_project",
        (
            r"\b(start(?:\s+and\s+launch)?|launch(?:\s+and\s+start)?|run|boot\s+up|initialize)\s+(?:the\s+|this\s+|my\s+)?project\b(?!\s+(?:ticket|board))",
            r"\bstart\s+project\b.{0,80}\band\s+(?:then\s+)?launch(?:\s+it)?\b",
        ),
    ),
    (
        "smart_open",
        (
            r"\b(open|bring\s+up|show)\b.*\b(downloads?|documents?|desktop|home)\s+folder\b",
            r"\b(open|bring\s+up|show)\b.*\bmy\s+(downloads?|documents?|desktop)\b",
        ),
    ),
    (
        "move_to_element",
        (
            r"\b(move|put|position)\b.*\b(mouse|cursor|pointer)\b.*\b(to|over|on)\b",
        ),
    ),
    (
        "find_element",
        (
            r"\b(move|put|position)\b.*\b(mouse|cursor|pointer)\b.*\b(to|over|on)\b",
        ),
    ),
    (
        "create_ticket",
        (
            r"\b(create|make|add|new|draft)\s+(?:a\s+|an\s+)?(?:ticket|card|issue)\b",
            r"\b(create|make|add|new|draft)\s+(?:a\s+|an\s+)?(?:jira|trello)\s+(?:ticket|card|issue)\b",
            r"\b(?:break|split|turn|convert|scope)\b.{0,80}\b(?:work|request|project|brief|instructions?|it|this)\b.{0,40}\b(?:into|as)\b.{0,20}\b(?:tickets?|work\s+items?|tasks?)\b",
            r"\b(?:break|split|turn|convert|scope)\s+(?:it|this|that|the\s+(?:work|request|project|brief))\s+(?:down\s+)?into\s+(?:separate\s+|individual\s+|a\s+group\s+of\s+)?(?:tickets?|work\s+items?|tasks?)\b",
            r"\b(?:break|split|turn|convert|scope)\b.{0,140}\b(?:tickets?|work\s+items?)\b",
            r"\b(?:tickets?|work\s+items?)\b.{0,80}\b(?:independently\s+executable|dependencies|dependency|workflow|backlog)\b",
            r"\b(move|transfer|relocate)\b.+\b(ticket|card|issue)\b.+\bboard\b",
            r"\b(ticket|card|issue)\b.+\b(move|transfer|relocate)\b.+\bboard\b",
            r"\b(move|transfer|relocate)\b.{0,80}\b(tickets?|cards?|issues?)\b",
            r"\b(tickets?|cards?|issues?)\b.{0,80}\b(move|transfer|relocate)\b",
            r"\bmerge\b.{0,60}\b(tickets?|cards?|issues?)\b",
            r"\b(tickets?|cards?|issues?)\b.{0,60}\bmerge\b",
            r"\bcreate_ticket\b",
            r"\b(list_tickets|move_ticket|delete_ticket|update_ticket|get_ticket)\b",
            r"\bwhats\s*app\b.+\b(sync|latest|activity|overview|contacts?|chats?|messages?|thread|context|snapshot|ticket|reply|send)\b",
            r"\b(sync|latest|activity|overview|list|show|read|open|snapshot|create|make|draft|reply|send)\b.+\bwhats\s*app\b",
            r"\b(groups?|chats?|threads?)\b.+\b(messages?|photos?|screenshots?|voice\s+notes?)\b",
            r"\b(messages?|photos?|screenshots?|voice\s+notes?)\b.+\b(groups?|chats?|threads?)\b",
            r"\b(open|show|go\s+to|switch\s+to|launch|bring\s+up)\b.+\b(ticket\s+boards?|kanban(?:\s+board)?)\b",
            r"\b(ticket\s+boards?|kanban(?:\s+board)?)\b.+\b(open|show|bring\s+up)\b",
            r"\b(open|show|go\s+to|launch)\b.+\b(?:jira|trello|local)\b.+\bboard\b",
            r"\b(open|show|go\s+to)\b.+\bboard\b.*\b(jira|trello|local)\b",
        ),
    ),
    (
        "file_operations",
        (
            r"\b(rename|move|delete|remove|list|show|create|copy)\s+.+\b(file|folder|directory|downloads|desktop|documents)\b",
            r"\bwhat\s+files\s+are\s+on\b",
        ),
    ),
    (
        "convert_document",
        (
            r"\b(convert|turn|export|make)\s+.+\b(pdf|word|docx|document)\b",
            r"\bexport\s+as\s+pdf\b",
        ),
    ),
    (
        "create_step_runner",
        (
            r"\b(create|build|make|generate)\s+(?:a\s+|an\s+)?(?:step\s+runner|automation|workflow)\b",
        ),
    ),
    (
        "build_tool",
        (
            r"\b(build|create|make|generate|save)\s+(?:a\s+|an\s+|this\s+)?(?:reusable\s+|custom\s+)?(?:tool|capability)\b",
            r"\b(turn|save|make)\s+(?:this|that|it)\s+into\s+(?:a\s+)?(?:tool|repeatable\s+action)\b",
        ),
    ),
    (
        "computer_use",
        (
            r"\b(on\s+(?:my|the)\s+(?:screen|desktop)|in\s+the\s+(?:app|window))\b.*\b(first|then|after|finally)\b",
            r"\b(first|then|after\s+that|finally)\b.*\b(click|type|select|open|scroll|drag|press)\b.*\b(click|type|select|open|scroll|drag|press)\b",
            r"\b(complete|handle|do)\s+(?:this\s+)?(?:multi[- ]step\s+)?(?:desktop|screen|gui)\s+(?:task|workflow)\b",
        ),
    ),
    (
        "scheduled_action",
        (
            r"\b(schedule|scheduled|recurring|every\s+(?:day|weekday|week|morning|evening))\b.*\b(action|desktop|keypress|key\s*press|type|open|recording|chrome|app|automation)\b",
            r"\b(list|show|what|cancel|delete|disable|enable|reschedule|move)\b.*\bscheduled\s+(?:desktop\s+)?actions?\b",
            r"\b(cancel|delete|disable|enable|reschedule|move)\b.*\baction\s+\d+\b",
            r"\bopen\s+\w+\b.*\b(every\s+(?:day|weekday|week)|daily|weekly|weekdays?)\b",
        ),
    ),
    (
        "google_workspace",
        (
            r"\b(?:connected|connect(?:ed|ion)?|authenticated|authentication|working)\b.{0,80}\b(?:google|gmail|google\s+calendar|google\s+workspace)\b",
            r"\b(?:google|gmail|google\s+calendar|google\s+workspace)\b.{0,80}\b(?:connected|connect(?:ed|ion)?|authenticated|authentication|working)\b",
            r"\b(?:check|read|show|list|open|access|use|try)\b.{0,80}\b(?:gmail|google\s+calendar|google\s+drive|google\s+workspace)\b",
            r"\b(?:google\s+)?calendar\b.*\b(?:create|add|make|schedule|event|appointment)\b",
            r"\b(?:create|add|make|schedule)\b.*\b(?:google\s+)?calendar\b",
            r"\b(?:create|add|make|schedule)\s+(?:the\s+|an?\s+)?(?:calendar\s+)?event\b",
            r"\b(gmail|email)\b.*\battachment",
            r"\battachment\b.*\b(gmail|email|inbox)\b",
            r"\bdownload\b.*\b(gmail|email)\b.*\battachment",
            r"\b(get|grab|pull|save)\b.*\b(gmail|email)\b.*\battachment",
        ),
    ),
    (
        "screenshot_analyzer",
        (
            r"\b(take|capture|grab|get)\s+(?:a\s+)?(?:screenshot|screen\s*shot|picture)\b",
            r"\b(give|send|show)\s+(?:me\s+)?(?:a\s+)?screenshots?\b",
            r"\bscreenshot\s+(?:of\s+)?screen\s+\d+\b",
            r"\b(what\s+do\s+you\s+see|what'?s?\s+on\s+(?:the\s+)?screen|describe\s+(?:the\s+)?screen|what'?s?\s+on\s+my\s+screen)\b",
            r"\b(see|look\s+at|analyze|check|examine)\b.*?\b(?:my\s+)?(?:screen|display|monitor)\b",
            r"\bsee\s+what\s+i(?:'m|\s+am)\s+looking\s+at\b",
            r"\bscreen\s+(?:capture|interaction|shot)\b",
            r"\b(move|put|position)\b.*\b(mouse|cursor|pointer)\b.*\b(to|over|on)\b",
        ),
    ),
    (
        "web_search",
        (
            # Plain web-search wording ("search the web for…", "google how to…").
            r"\b(search|look\s+up|check)\b.{0,20}\b(?:the\s+)?(?:web|internet|online)\b",
            r"\bgoogle\b\s+(?:it|this|that|for|how|what|why|where|who|when|the|a|an)\b",
            r"^\s*google\s+\S+",
            # Current, location-specific listings are research requests even when
            # the user does not say "search the web" explicitly. Keep this
            # deterministic so semantic retrieval cannot hide the web tool.
            r"\b(find|search|look\s+for|locate|list)\b.{0,140}\b(?:in|near|around|at|within)\b.{0,100}\b(?:shop|store|restaurant|cafe|coffee|pharmacy|doctor|dentist|hotel|salon|barber|print|copy|repair|service)\b",
            r"\b(find|search|look\s+for|locate|list)\b.{0,100}\b(?:shop|store|restaurant|cafe|coffee|pharmacy|doctor|dentist|hotel|salon|barber|print|copy|repair|service)\b.{0,100}\b(?:in|near|around|at|within)\b",
            r"\b(?:shop|store|restaurant|cafe|coffee|pharmacy|doctor|dentist|hotel|salon|barber|print|copy|repair|service)\b.{0,100}\b(?:in|near|around|at|within)\b.{0,100}\b(find|search|look|list|locate)\b",
        ),
    ),
    (
        "visual_baseline",
        (
            r"\b(visual\s+baselines?|baseline\s+sets?|reference\s+screens?|gold(?:en)?\s+standard)\b",
            r"\b(save|capture|create|add)\b.*\b(screenshot|screen)\b.*\b(baseline|reference|gold(?:en)?\s+standard)\b",
            r"\b(list|show|get|inspect|check|audit|ready|readiness)\b.*\b(visual\s+baselines?|baseline\s+sets?|reference\s+screens?)\b",
            r"\b(visual\s+baselines?|baseline\s+sets?|reference\s+screens?)\b.*\b(ready|readiness|missing|exist|usable)\b",
        ),
    ),
    (
        "special_key",
        (
            r"\b(?:press|hit|tap)\s+(?:the\s+)?(?:space\s*bar|spacebar|space|enter|return|tab|escape|esc|backspace|delete|up|down|left|right|page\s+up|page\s+down|home|end|[a-z0-9])\b",
            r"^(?:space\s*bar|spacebar|space|enter|return|tab|escape|esc|backspace|delete|up|down|left|right|home|end)\.?$",
        ),
    ),
    (
        "exit_app",
        (
            r"\b(exit|quit|close)\s+(?:the\s+)?(?:app|application|decisionsai)\b",
            r"^quit$",
        ),
    ),
    (
        "list_actions",
        (
            r"\b(?:list|show)\b.{0,80}\b(?:the\s+)?(?:list\s+of\s+)?(?:(?:my|the|your|available|saved|custom|recorded)\s+)*actions\b",
            r"\bwhat(?:'s|s|\s+is|\s+are)\b.{0,80}\b(?:the\s+)?(?:list\s+of\s+)?(?:(?:my|the|your|available|saved|custom|recorded)\s+)*actions\b",
            r"\b(?:saved|custom|recorded)\s+actions\b",
            r"\bactions\s+that\s+(?:i(?:'?ve|\s+have)?|you(?:'?ve|\s+have)?|we(?:'?ve|\s+have)?)\s+(?:created|recorded|saved)\b",
            r"\bactions\s+that\s+i\s+can(?:\s+actually)?\s+run\b",
        ),
    ),
    (
        "play_action",
        (
            r"\b(run|play|execute)\s+(?:the\s+|that\s+|my\s+|this\s+)?(?:saved\s+|custom\s+|recorded\s+)?action\b",
            r"\b(run|play|execute)\s+it\b",
        ),
    ),
)


SAVED_ACTION_TOOLS: tuple[str, str] = ("list_actions", "play_action")

_EXPLICIT_CREATE_ACTION_PATTERNS: tuple[str, ...] = (
    r"\b(?:create|make|add|record)\b.{0,60}\b(?:a\s+|an\s+|new\s+)?(?:saved\s+|custom\s+|recorded\s+)?actions?\b",
    r"\bnew\s+action\b",
    r"\bstart\s+recording\b",
)


def pair_saved_action_tools(names: list[str]) -> list[str]:
    """If either saved-action tool is present, include both."""
    present = set(names)
    if present & set(SAVED_ACTION_TOOLS):
        for name in SAVED_ACTION_TOOLS:
            if name not in present:
                names.append(name)
                present.add(name)
    return names


def is_explicit_create_action_request(text: str) -> bool:
    """Return whether the user explicitly asked to create or record a new action."""
    raw = (text or "").strip()
    return bool(raw) and any(
        re.search(pattern, raw, re.IGNORECASE)
        for pattern in _EXPLICIT_CREATE_ACTION_PATTERNS
    )


def is_saved_action_run_or_list_request(text: str) -> bool:
    """Return whether the user asked to list or run an existing saved action."""
    raw = (text or "").strip()
    if not raw:
        return False
    forced = forced_tool_names_for_text(raw)
    if any(name in forced for name in SAVED_ACTION_TOOLS):
        return True
    return is_workflow_follow_up(raw) and bool(
        re.search(r"\b(run|play|execute|do)\s+(it|that)\b", raw, re.IGNORECASE)
    )


def forced_tool_names_for_text(text: str) -> list[str]:
    """Return tool names that should be force-included for this request."""
    raw = (text or "").strip()
    if not raw:
        return []

    forced: list[str] = []
    ticket_intent_kind = ""
    if classify_ticket_intent:
        try:
            ticket_intent_kind = classify_ticket_intent(raw).kind
        except Exception:
            ticket_intent_kind = ""

    if ticket_intent_kind == "debug_decisions_ticket":
        forced.append("create_cursor_ticket")
    elif ticket_intent_kind == "ticket_file":
        forced.append("file_operations")
    elif ticket_intent_kind == "type_text":
        forced.append("type_text")

    for tool_name, patterns in _RULES:
        if ticket_intent_kind in {"debug_decisions_ticket", "ticket_file", "type_text"} and tool_name == "create_ticket":
            continue
        if tool_name == "start_project" and re.match(r"^\s*(?:did|have|has)\b", raw, re.IGNORECASE):
            continue
        if tool_name in SAVED_ACTION_TOOLS and re.search(
            r"\bscheduled\s+(?:desktop\s+)?actions?\b", raw, re.IGNORECASE
        ):
            continue
        if any(re.search(pattern, raw, re.IGNORECASE) for pattern in patterns):
            forced.append(tool_name)
    if "message_agent" not in forced and _addresses_known_agent(raw):
        forced.append("message_agent")
    return pair_saved_action_tools(forced)


_AGENT_NAMES_CACHE: dict = {"at": 0.0, "names": ()}


def _known_agent_names() -> tuple:
    import time

    if time.monotonic() - _AGENT_NAMES_CACHE["at"] < 60:
        return _AGENT_NAMES_CACHE["names"]
    names: tuple = ()
    try:
        from distr.core.db import ChatAgent, get_session

        with get_session() as session:
            names = tuple(name for (name,) in session.query(ChatAgent.name).all() if name)
    except Exception:
        names = ()
    _AGENT_NAMES_CACHE.update(at=time.monotonic(), names=names)
    return names


def _addresses_known_agent(raw: str) -> bool:
    """'ask Uncle Roger ...', 'tell Hayley ...': a talk verb followed by a known agent's name."""
    verbs = r"\b(?:ask|tell|message|ping|check\s+with|consult|delegate\s+(?:this\s+|it\s+)?to|let)\b"
    for name in _known_agent_names():
        if re.search(verbs + r"\s+" + re.escape(name) + r"\b", raw, re.IGNORECASE):
            return True
    return False


_FOLLOW_UP_LEAD = r"^(?:(?:so|then|okay|ok|yes|yeah|yep|sure)[,.]?\s+)*"

_WORKFLOW_FOLLOW_UP_PATTERNS: tuple[str, ...] = (
    r"^(?:yes|yeah|yep|sure|okay|ok)[.!?]?$",
    r"^(?:yes|yeah|yep|sure|okay|ok)[,.]?\s+(?:please\s+)?(?:can|could|will)\s+you(?:\s+please)?[.!?]?$",
    r"^(?:please\s+)?(?:can|could|will)\s+you(?:\s+please)?[.!?]?$",
    _FOLLOW_UP_LEAD + r"(?:please\s+)?(?:try|retry)(?:\s+it|\s+that)?\s+again[.!?]?$",
    _FOLLOW_UP_LEAD + r"(?:please\s+)?do\s+(?:it|that)(?:\s+again)?[.!?]?$",
    _FOLLOW_UP_LEAD + r"(?:please\s+)?(?:go\s+ahead|continue|finish\s+it)[.!?]?$",
    _FOLLOW_UP_LEAD + r"(?:please\s+)?(?:can\s+you\s+|could\s+you\s+)?(?:run|play|execute)(?:\s+it|\s+that)?[.!?]?$",
    r"^(?:please\s+)?create\s+the\b.*\bevent\b[.!]?$",
    r"\bclipboard\b.*\bdo\s+it\b",
    r"^(?:i\s+want\s+you\s+to\s+)?(?:perform|do|choose|use|take)?\s*(?:the\s+)?(?:first|second|third|fourth|fifth|last)\s+option[.!]?$",
)


def is_workflow_follow_up(text: str) -> bool:
    """Return whether a short instruction should retain an active tool chain."""
    raw = (text or "").strip()
    return bool(raw) and any(
        re.search(pattern, raw, re.IGNORECASE)
        for pattern in _WORKFLOW_FOLLOW_UP_PATTERNS
    )
