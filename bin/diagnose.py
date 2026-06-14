#!/usr/bin/env python3
"""
DecisionsAI Diagnostic Tool

Correlates log entries with chat messages to diagnose weird behavior.
Shows the full pipeline: STT transcription → fast action detection → tool call → result.

Usage:
    python bin/diagnose.py                  # Last 10 minutes
    python bin/diagnose.py --minutes 30     # Last 30 minutes
    python bin/diagnose.py --last 5         # Last 5 chat messages
    python bin/diagnose.py --search "mouse" # Search for keyword
    python bin/diagnose.py --all            # Everything from current session
"""

import argparse
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# --- Paths ---
# Try multiple locations for DB and logs (runtime creates these)
CRASH_LOG_DIR = Path.home() / ".decisions" / "logs"

def _find_db_and_logs():
    """Find the database and log files across possible locations."""
    candidates = [
        PROJECT_ROOT / "db",
        PROJECT_ROOT / "distr" / "db",
        Path.home() / ".decisions" / "db",
    ]
    db_path = None
    log_file = None
    for d in candidates:
        p = d / "settings.db"
        if p.exists():
            db_path = p
            break
    for d in candidates:
        p = d / "logs" / "decisions.log"
        if p.exists():
            log_file = p
            break
    # Fallback to default
    if not db_path:
        db_path = PROJECT_ROOT / "distr" / "db" / "settings.db"
    if not log_file:
        log_file = PROJECT_ROOT / "distr" / "db" / "logs" / "decisions.log"
    return db_path, log_file

DB_PATH, LOG_FILE = _find_db_and_logs()

# --- Colors ---
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
MAGENTA = "\033[95m"
CYAN = "\033[96m"
WHITE = "\033[97m"
GRAY = "\033[90m"


# --- Log parsing patterns ---
LOG_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+-\s+(\S+)\s+-\s+(\w+)\s+-\s+(.*)"
)
TOOL_CALL_RE = re.compile(r"(MouseMovementTool|MouseActionsTool|ScreenshotAnalyzerTool|screenshot_analyzer)\._run\s+called", re.IGNORECASE)
FAST_ACTION_RE = re.compile(r"FastActionDetector.*?(?:Detected|Analyzing|routing|Matched|FAST ACTION)", re.IGNORECASE)
STT_RE = re.compile(r"(STT|transcri|speech.to.text|whisper|vosk|recognized|heard)", re.IGNORECASE)
TOOL_EXEC_RE = re.compile(r"(Executing|_run called|tool.*result|action=|move_center|move_to|moveTo|pyautogui)", re.IGNORECASE)
SCREENSHOT_RE = re.compile(r"(screenshot|screen.capture|vision|capture_single|screencapture)", re.IGNORECASE)
MOUSE_RE = re.compile(r"(mouse|cursor|moveTo|pyautogui\.move|smooth_move|CURRENT SCREEN|move_center|move_top|move_bottom)", re.IGNORECASE)
ERROR_RE = re.compile(r"(error|exception|traceback|failed|crash)", re.IGNORECASE)
LLM_RE = re.compile(r"(LLM|ollama|openai|anthropic|openrouter|tool_call|function_call|agent.*tool)", re.IGNORECASE)


def parse_log_line(line):
    """Parse a single log line into components."""
    m = LOG_LINE_RE.match(line.strip())
    if not m:
        return None
    ts_str, logger_name, level, message = m.groups()
    try:
        ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S,%f")
    except ValueError:
        return None
    return {"timestamp": ts, "logger": logger_name, "level": level, "message": message}


def classify_log(entry):
    """Classify a log entry by category."""
    msg = entry["message"]
    tags = []
    if FAST_ACTION_RE.search(msg):
        tags.append("FAST_ACTION")
    if STT_RE.search(msg):
        tags.append("STT")
    if TOOL_CALL_RE.search(msg):
        tags.append("TOOL_CALL")
    if TOOL_EXEC_RE.search(msg):
        tags.append("TOOL_EXEC")
    if SCREENSHOT_RE.search(msg):
        tags.append("SCREENSHOT")
    if MOUSE_RE.search(msg):
        tags.append("MOUSE")
    if ERROR_RE.search(msg):
        tags.append("ERROR")
    if LLM_RE.search(msg):
        tags.append("LLM")
    return tags


def load_logs(since=None, search=None):
    """Load and parse log file entries."""
    if not LOG_FILE.exists():
        print(f"{RED}No log file found at {LOG_FILE}{RESET}")
        return []

    entries = []
    with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            entry = parse_log_line(line)
            if not entry:
                continue
            if since and entry["timestamp"] < since:
                continue
            if search and search.lower() not in entry["message"].lower():
                continue
            entry["tags"] = classify_log(entry)
            if entry["tags"]:  # Only keep classified entries
                entries.append(entry)
    return entries


def load_chats(since=None, last_n=None, search=None):
    """Load recent chat messages from the database."""
    if not DB_PATH.exists():
        print(f"{RED}No database found at {DB_PATH}{RESET}")
        return []

    engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        query = "SELECT id, parent_id, title, input, response, created_date, model_name, provider FROM chats WHERE input IS NOT NULL AND input != '' ORDER BY created_date DESC"
        if last_n:
            query += f" LIMIT {last_n}"
        elif since:
            query = f"SELECT id, parent_id, title, input, response, created_date, model_name, provider FROM chats WHERE input IS NOT NULL AND input != '' AND created_date >= '{since.strftime('%Y-%m-%d %H:%M:%S')}' ORDER BY created_date DESC"

        rows = session.execute(text(query)).fetchall()
        chats = []
        for row in rows:
            chat = {
                "id": row[0],
                "parent_id": row[1],
                "title": row[2],
                "input": row[3],
                "response": row[4],
                "created_date": row[5],
                "model_name": row[6],
                "provider": row[7],
            }
            if search and search.lower() not in (chat["input"] or "").lower() and search.lower() not in (chat["response"] or "").lower():
                continue
            chats.append(chat)
        chats.reverse()
        return chats
    finally:
        session.close()


def format_timestamp(ts):
    """Format timestamp for display."""
    if isinstance(ts, str):
        return ts[:19]
    return ts.strftime("%H:%M:%S")


def tag_color(tag):
    """Get color for a tag."""
    colors = {
        "STT": CYAN,
        "FAST_ACTION": YELLOW,
        "TOOL_CALL": MAGENTA,
        "TOOL_EXEC": GREEN,
        "SCREENSHOT": BLUE,
        "MOUSE": GREEN,
        "ERROR": RED,
        "LLM": WHITE,
    }
    return colors.get(tag, GRAY)


def print_separator(char="─", width=100):
    print(f"{GRAY}{char * width}{RESET}")


def print_header(title):
    print()
    print_separator("═")
    print(f"{BOLD}{WHITE}  {title}{RESET}")
    print_separator("═")


def print_chat(chat):
    """Print a single chat message with formatting."""
    ts = format_timestamp(chat["created_date"])
    provider = chat["provider"] or "?"
    model = chat["model_name"] or "?"

    print(f"\n  {GRAY}{ts}{RESET}  {DIM}[{provider}/{model}]{RESET}")
    print(f"  {CYAN}YOU:{RESET} {chat['input'][:200]}")
    if chat["response"]:
        resp = chat["response"][:300]
        if len(chat["response"]) > 300:
            resp += "..."
        print(f"  {GREEN}AI:{RESET}  {resp}")


def print_log_entry(entry):
    """Print a single log entry with formatting."""
    ts = format_timestamp(entry["timestamp"])
    tags_str = " ".join(f"{tag_color(t)}[{t}]{RESET}" for t in entry["tags"])
    level_color = RED if entry["level"] == "ERROR" else YELLOW if entry["level"] == "WARNING" else GRAY
    msg = entry["message"][:200]
    if len(entry["message"]) > 200:
        msg += "..."
    print(f"  {GRAY}{ts}{RESET} {level_color}{entry['level']:7s}{RESET} {tags_str}")
    print(f"           {DIM}{msg}{RESET}")


def correlate(chats, logs):
    """Correlate chat messages with surrounding log entries."""
    if not chats:
        print(f"\n  {YELLOW}No chat messages found in this time range.{RESET}")
        return
    if not logs:
        print(f"\n  {YELLOW}No relevant log entries found. Logs may have been cleared on last startup.{RESET}")
        print(f"  {DIM}(DecisionsAI clears logs each time it launches){RESET}")

    for chat in chats:
        print_separator()
        print_chat(chat)

        # Find log entries within ±5 seconds of this chat message
        chat_ts = chat["created_date"]
        if isinstance(chat_ts, str):
            try:
                chat_ts = datetime.strptime(chat_ts[:19], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue

        window_before = chat_ts - timedelta(seconds=3)
        window_after = chat_ts + timedelta(seconds=10)

        related = [e for e in logs if window_before <= e["timestamp"] <= window_after]

        if related:
            print(f"\n  {DIM}── Pipeline trace ({len(related)} log entries) ──{RESET}")
            # Sort by category priority for readability
            priority = {"STT": 0, "FAST_ACTION": 1, "LLM": 2, "TOOL_CALL": 3, "TOOL_EXEC": 4, "MOUSE": 5, "SCREENSHOT": 6, "ERROR": 7}
            related.sort(key=lambda e: (e["timestamp"], min(priority.get(t, 99) for t in e["tags"]) if e["tags"] else 99))
            for entry in related:
                print_log_entry(entry)
        else:
            print(f"\n  {DIM}  (no matching log entries in ±5s window){RESET}")

    print()
    print_separator()


def show_recent_errors(logs):
    """Show any recent errors from logs."""
    errors = [e for e in logs if "ERROR" in e["tags"]]
    if errors:
        print_header("Recent Errors")
        for entry in errors[-10:]:
            print_log_entry(entry)
        print()


def show_crash_logs():
    """Check for recent crash logs."""
    if not CRASH_LOG_DIR.exists():
        return
    crash_files = sorted(CRASH_LOG_DIR.glob("crash_*.log"), key=os.path.getmtime, reverse=True)
    if crash_files:
        latest = crash_files[0]
        age = datetime.now() - datetime.fromtimestamp(os.path.getmtime(latest))
        if age < timedelta(hours=1):
            print_header("Recent Crash Log")
            print(f"  {RED}Crash log found: {latest} ({age.seconds // 60}m ago){RESET}")
            with open(latest, "r") as f:
                lines = f.readlines()[-20:]
                for line in lines:
                    print(f"  {DIM}{line.rstrip()}{RESET}")
            print()


def show_summary(chats, logs):
    """Show a quick summary of what happened."""
    print_header("Summary")

    tool_calls = [e for e in logs if "TOOL_CALL" in e["tags"] or "TOOL_EXEC" in e["tags"]]
    screenshots = [e for e in logs if "SCREENSHOT" in e["tags"]]
    mouse_ops = [e for e in logs if "MOUSE" in e["tags"]]
    fast_actions = [e for e in logs if "FAST_ACTION" in e["tags"]]
    errors = [e for e in logs if "ERROR" in e["tags"]]

    print(f"  Chat messages:    {BOLD}{len(chats)}{RESET}")
    print(f"  Fast detections:  {BOLD}{len(fast_actions)}{RESET}")
    print(f"  Tool calls:       {BOLD}{len(tool_calls)}{RESET}")
    print(f"  Screenshots:      {BOLD}{len(screenshots)}{RESET}")
    print(f"  Mouse operations: {BOLD}{len(mouse_ops)}{RESET}")
    print(f"  Errors:           {RED if errors else BOLD}{len(errors)}{RESET}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="DecisionsAI Diagnostic Tool — correlate logs with chat messages",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python bin/diagnose.py                  # Last 10 minutes
  python bin/diagnose.py --minutes 30     # Last 30 minutes  
  python bin/diagnose.py --last 5         # Last 5 chat messages
  python bin/diagnose.py --search mouse   # Search for 'mouse' in logs and chats
  python bin/diagnose.py --all            # Full session (since last log clear)
  python bin/diagnose.py --errors         # Show only errors
        """,
    )
    parser.add_argument("--minutes", type=int, default=10, help="Look back N minutes (default: 10)")
    parser.add_argument("--last", type=int, help="Show last N chat messages")
    parser.add_argument("--search", type=str, help="Filter by keyword")
    parser.add_argument("--all", action="store_true", help="Show everything from current session")
    parser.add_argument("--errors", action="store_true", help="Show only errors")
    parser.add_argument("--logs-only", action="store_true", help="Show only log entries (no chat)")
    args = parser.parse_args()

    print(f"\n{BOLD}{CYAN}DecisionsAI Diagnostic Tool{RESET}")
    print(f"{DIM}DB: {DB_PATH}{RESET}")
    print(f"{DIM}Log: {LOG_FILE}{RESET}")

    # Determine time window
    since = None
    if not args.all and not args.last:
        since = datetime.now() - timedelta(minutes=args.minutes)
        print(f"{DIM}Window: last {args.minutes} minutes{RESET}")

    # Load data
    logs = load_logs(since=since, search=args.search if not args.logs_only else args.search)
    chats = [] if args.logs_only else load_chats(since=since, last_n=args.last, search=args.search)

    if args.errors:
        show_recent_errors(logs)
        return

    # Show crash logs if any
    show_crash_logs()

    # Main correlation view
    if not args.logs_only:
        print_header("Chat → Log Correlation")
        correlate(chats, logs)

    if args.logs_only:
        print_header("Log Entries")
        for entry in logs:
            print_log_entry(entry)
        print()

    # Summary
    show_summary(chats, logs)

    # Show errors at the end
    show_recent_errors(logs)


if __name__ == "__main__":
    main()
