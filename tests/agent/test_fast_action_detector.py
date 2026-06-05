#!/usr/bin/env python3
"""
Fast Action Detector Test Suite
================================

Tests the FastActionDetector against real-world conversational inputs
to measure accuracy — specifically, whether it correctly routes to the
LLM (CONVERSATIONAL/UNKNOWN) vs. fast-pathing to a tool (action types).

The key metric: conversations that need the LLM should NOT be swallowed
by the fast action detector. Strong models like GLM-5.1 and Kimi-K2 are
wasted if their input gets intercepted by an overzealous regex.

Target: ~80% accuracy on conversational routing with GLM-5.1 / Kimi-K2

Usage:
    python3 tests/agent/test_fast_action_detector.py
    python3 tests/agent/test_fast_action_detector.py --verbose
    python3 tests/agent/test_fast_action_detector.py --category false_positives
"""

import re
import sys
import os
import types
import logging
from dataclasses import dataclass
from typing import List, Tuple, Optional

# ─── Bootstrap: load the detector module without full project imports ───

DETECTOR_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "distr", "core", "agent", "services", "llm", "fast_action_detector.py"
)


def _load_detector():
    """Load the FastActionDetector by exec'ing the source file directly."""
    with open(DETECTOR_PATH, "r") as f:
        source = f.read()

    module = types.ModuleType("fast_action_detector")
    module.__dict__["re"] = re
    module.__dict__["logging"] = logging
    exec(source, module.__dict__)
    return module.FastActionDetector, module.ActionType, module.DetectedAction


FastActionDetector, ActionType, DetectedAction = _load_detector()
detector = FastActionDetector()

# ─── Test infrastructure ───

@dataclass
class TestCase:
    """A single test case for the fast action detector."""
    input_text: str
    expected_route: str  # "llm" or "fast_action"
    expected_action_type: Optional[str] = None  # e.g. "CLIPBOARD_COPY", "MOUSE_MOVEMENT" — only for fast_action
    category: str = ""
    description: str = ""

    @property
    def should_go_to_llm(self) -> bool:
        return self.expected_route == "llm"


@dataclass
class TestResult:
    """Result of running a test case."""
    test: TestCase
    actual_action_type: str
    actual_tool_name: str
    actual_response_type: str
    actual_confidence: float
    went_to_llm: bool  # True if CONVERSATIONAL or UNKNOWN
    passed: bool
    reason: str = ""


def run_test(tc: TestCase) -> TestResult:
    """Run a single test case through the detector."""
    result = detector.detect(tc.input_text)

    went_to_llm = result.action_type in (
        ActionType.CONVERSATIONAL,
        ActionType.UNKNOWN,
    )

    if tc.should_go_to_llm:
        # We expect this to go to LLM
        passed = went_to_llm
        if not passed:
            reason = f"Expected LLM routing but got {result.action_type.value} (tool={result.tool_name}, response_type={result.response_type})"
        else:
            reason = "Correctly routed to LLM"
    else:
        # We expect this to go to a fast action
        if went_to_llm:
            passed = False
            reason = f"Expected fast action ({tc.expected_action_type or 'any'}) but got LLM routing ({result.action_type.value})"
        elif tc.expected_action_type:
            passed = result.action_type.value == tc.expected_action_type.lower()
            if not passed:
                reason = f"Expected {tc.expected_action_type} but got {result.action_type.value}"
            else:
                reason = f"Correctly routed to {result.action_type.value}"
        else:
            passed = True
            reason = f"Correctly routed to fast action: {result.action_type.value}"

    return TestResult(
        test=tc,
        actual_action_type=result.action_type.value,
        actual_tool_name=result.tool_name,
        actual_response_type=result.response_type,
        actual_confidence=result.confidence,
        went_to_llm=went_to_llm,
        passed=passed,
        reason=reason,
    )


# ═══════════════════════════════════════════════════════════════════════════
# TEST CASES
# ═══════════════════════════════════════════════════════════════════════════

TEST_CASES: List[TestCase] = []

def T(input_text, expected_route, expected_action_type=None, category="", description=""):
    """Shorthand to add a test case."""
    TEST_CASES.append(TestCase(
        input_text=input_text,
        expected_route=expected_route,
        expected_action_type=expected_action_type,
        category=category,
        description=description,
    ))

# ─── CATEGORY: Conversational Questions (MUST go to LLM) ───

T("What do you think about artificial intelligence?", "llm",
  category="conversational_questions",
  description="General opinion question about AI")

T("How does machine learning work?", "llm",
  category="conversational_questions",
  description="Educational question about ML")

T("Why is the sky blue?", "llm",
  category="conversational_questions",
  description="Classic factual question")

T("What's your favorite programming language?", "llm",
  category="conversational_questions",
  description="Opinion question")

T("Can you tell me about the history of computing?", "llm",
  category="conversational_questions",
  description="Tell me about (not 'tell cursor')")

T("Who invented the internet?", "llm",
  category="conversational_questions",
  description="Factual question")

T("Where can I learn more about Python?", "llm",
  category="conversational_questions",
  description="Question about resources")

T("When was the first computer built?", "llm",
  category="conversational_questions",
  description="Factual question about history")

T("How are you doing today?", "llm",
  category="conversational_questions",
  description="Social greeting/question")

T("What's the meaning of life?", "llm",
  category="conversational_questions",
  description="Philosophical question")

T("I was wondering if you could help me understand quantum computing", "llm",
  category="conversational_questions",
  description="Polite conversational request")

T("Tell me a story about a brave knight", "llm",
  category="conversational_questions",
  description="Story request, not 'tell cursor'")

T("Tell me something interesting about space", "llm",
  category="conversational_questions",
  description="Tell me (general), not 'tell cursor'")

T("Can you help me brainstorm ideas for my project?", "llm",
  category="conversational_questions",
  description="Brainstorming request - not an action")

T("What are the pros and cons of microservices?", "llm",
  category="conversational_questions",
  description="Analytical question")

T("Explain quantum mechanics in simple terms", "llm",
  category="conversational_questions",
  description="Explain without 'this/that/it' - should go to LLM")

T("Elaborate on the concept of distributed systems", "llm",
  category="conversational_questions",
  description="Elaborate without 'this/that/it' context")

T("How do I improve my coding skills?", "llm",
  category="conversational_questions",
  description="Advice question")

T("What's the best approach to learn a new language?", "llm",
  category="conversational_questions",
  description="Methodology question")

T("Can you compare React and Vue for me?", "llm",
  category="conversational_questions",
  description="Comparison request")

T("Is it better to use TypeScript or JavaScript?", "llm",
  category="conversational_questions",
  description="Opinion/comparison question")

T("What's the difference between Docker and Kubernetes?", "llm",
  category="conversational_questions",
  description="Technical comparison question")

T("How do neurons in the brain communicate?", "llm",
  category="conversational_questions",
  description="Scientific explanation request")

T("Why do cats purr?", "llm",
  category="conversational_questions",
  description="Trivia question")

# ─── CATEGORY: Statements and Opinions (MUST go to LLM) ───

T("I think the best approach is to use microservices", "llm",
  category="statements_opinions",
  description="Personal opinion statement")

T("That's an interesting perspective on the problem", "llm",
  category="statements_opinions",
  description="Conversational response")

T("I disagree with that approach because it doesn't scale well", "llm",
  category="statements_opinions",
  description="Disagreement with reasoning")

T("The weather has been really strange lately", "llm",
  category="statements_opinions",
  description="Small talk")

T("I've been working on a machine learning project recently", "llm",
  category="statements_opinions",
  description="Personal statement")

T("This project is really challenging but fun", "llm",
  category="statements_opinions",
  description="Statement about project")

T("I'm not sure if that's the right way to do it", "llm",
  category="statements_opinions",
  description="Uncertainty expression")

T("That sounds like a good plan", "llm",
  category="statements_opinions",
  description="Agreement")

T("Let me think about that for a moment", "llm",
  category="statements_opinions",
  description="Thinking out loud")

T("Actually, I think we should reconsider", "llm",
  category="statements_opinions",
  description="Reconsideration")

# ─── CATEGORY: Multi-sentence Conversations (MUST go to LLM) ───

T("I've been reading about neural networks. Can you explain how backpropagation works?", "llm",
  category="multi_sentence",
  description="Statement then question")

T("I just finished a course on Python. What should I learn next?", "llm",
  category="multi_sentence",
  description="Statement then advice request")

T("That's really helpful, thanks. One more question - how does garbage collection work?", "llm",
  category="multi_sentence",
  description="Gratitude then follow-up question")

T("I understand the basics now. Can we go deeper into the topic of async programming?", "llm",
  category="multi_sentence",
  description="Progress update then deeper question")

T("The code you suggested worked great. Now I want to add error handling to it.", "llm",
  category="multi_sentence",
  description="Positive feedback then new request")

T("Wait, that doesn't seem right. Let me rephrase - what I actually need is a way to serialize this data structure.", "llm",
  category="multi_sentence",
  description="Correction then clarification")

T("First of all, thank you for your help. Second, I wanted to ask about the performance implications.", "llm",
  category="multi_sentence",
  description="Multi-part message")

T("I tried the approach you suggested, but it didn't quite work. The issue is that the connection keeps timing out.", "llm",
  category="multi_sentence",
  description="Problem description")

# ─── CATEGORY: False Positive Guard - Phrases that should NOT trigger actions ───

T("What's the best way to move past this challenge?", "llm",
  category="false_positives",
  description="'move past' is figurative, not mouse movement")

T("Can you create a plan for the project?", "llm",
  category="false_positives",
  description="'create a plan' is not 'create action/file/image'")

T("I want to find a good restaurant nearby", "llm",
  category="false_positives",
  description="'find' here is not 'find skill' or 'find UI element'")

T("Show me how to write better code", "llm",
  category="false_positives",
  description="'show me how' is a learning request, not screenshot")

T("Look at this from a different angle", "llm",
  category="false_positives",
  description="Figurative 'look at this', not visual analysis")

T("Can you explain what this error means?", "llm",
  category="false_positives",
  description="'explain what' is not 'explain this' (clipboard context)")

T("I need to clear my thoughts and start fresh", "llm",
  category="false_positives",
  description="'clear ... start' is figurative, not clear chat")

T("Let's start over with a new idea", "llm",
  category="false_positives",
  description="'start over' figurative, but might trigger NEW_CHAT pattern")

T("I read that article you mentioned", "llm",
  category="false_positives",
  description="'read that article' is statement, not clipboard read")

T("Can you show me an example of how to use decorators in Python?", "llm",
  category="false_positives",
  description="'show me an example' is educational, not screenshot")

T("Read the room and adjust your tone accordingly", "llm",
  category="false_positives",
  description="'read the room' is figurative")

T("Click through to the next page of results", "llm",
  category="false_positives",
  description="'click through' is figurative, not UI click")

T("I want to generate more leads for my business", "llm",
  category="false_positives",
  description="'generate' is not 'generate image'")

T("Move this conversation in a different direction", "llm",
  category="false_positives",
  description="'move this conversation' is figurative, not mouse")

T("Scroll down to see more details about the topic", "llm",
  category="false_positives",
  description="'scroll down to see more' is figurative/educational")

T("That's a different type of problem altogether", "llm",
  category="false_positives",
  description="'type' is used as noun, not 'type text' command")

T("Delete that thought from your mind", "llm",
  category="false_positives",
  description="'delete' used figuratively")

T("Copy the approach I used for the last project", "llm",
  category="false_positives",
  description="'copy' used figuratively")

T("Paste the link into your response when you reply", "llm",
  category="false_positives",
  description="'paste' used figuratively")

T("Summarize what you know about climate change", "llm",
  category="false_positives",
  description="'summarize' without 'this/that/it/clipboard' context")

T("I want to read more about neural networks", "llm",
  category="false_positives",
  description="'read more about' is educational, not clipboard read")

T("Open your mind to new possibilities", "llm",
  category="false_positives",
  description="'open' used figuratively, not 'open app'")

T("What's on the agenda for today?", "llm",
  category="false_positives",
  description="'what's on' is not 'what's on screen'")

T("Press on with your goals even when it's hard", "llm",
  category="false_positives",
  description="'press' used figuratively, not key press")

T("Can you elaborate on your reasoning?", "llm",
  category="false_positives",
  description="'elaborate on your reasoning' has no 'this/that/it'")

T("Explain to me why recursion is useful", "llm",
  category="false_positives",
  description="'explain to me' is not 'explain this'")

T("Make a decision about which framework to use", "llm",
  category="false_positives",
  description="'make a decision' is not 'make action' or 'make image'")

T("Find the best approach to handle this error", "llm",
  category="false_positives",
  description="'find the best approach' is not 'find skill' or 'find UI element'")

T("New ways to think about the problem", "llm",
  category="false_positives",
  description="'new ways' is not 'new chat'")

T("How do you handle errors in React?", "llm",
  category="false_positives",
  description="'handle' is not a tool command")

T("Create a more efficient algorithm for sorting", "llm",
  category="false_positives",
  description="'create a more efficient algorithm' is not image/file creation")

T("The screen was showing an error message earlier. What could cause that?", "llm",
  category="false_positives",
  description="Past tense about screen, not live screenshot request")

T("I was just thinking about what we discussed yesterday regarding the API design", "llm",
  category="false_positives",
  description="Conversation reference, not a command")

T("Type your thoughts on this matter", "llm",
  category="false_positives",
  description="'Type your thoughts' should go to LLM, not type_text command")

T("Dictate the terms of the agreement", "llm",
  category="false_positives",
  description="'Dictate' used in general sense, not keyboard input")

T("Save your energy for the important tasks", "llm",
  category="false_positives",
  description="'save' used figuratively")

T("Send my regards to the team", "llm",
  category="false_positives",
  description="'send' used figuratively")

T("Close the gap between theory and practice", "llm",
  category="false_positives",
  description="'close' used figuratively, not 'close app'")

T("Run through the main points again", "llm",
  category="false_positives",
  description="'run through' is review, not 'run action'")

T("Play devil's advocate for a moment", "llm",
  category="false_positives",
  description="'Play devil's advocate' is figurative, not 'play action'")

T("Stop and think about the implications", "llm",
  category="false_positives",
  description="'stop' is not 'stop recording'")

T("Start by understanding the fundamentals", "llm",
  category="false_positives",
  description="'Start by' is not 'start recording' or 'new chat'")

T("What's on your mind?", "llm",
  category="false_positives",
  description="Conversational question, not 'what's on screen'")

T("Go into more detail about that topic", "llm",
  category="false_positives",
  description="'go into more detail' is not vision 'go into element'")

T("Find the key differences between these approaches", "llm",
  category="false_positives",
  description="'Find the key differences' is analytical, not UI find")

T("List the main reasons why this approach works", "llm",
  category="false_positives",
  description="'List the reasons' is not 'list files'")

T("What app would you recommend for note-taking?", "llm",
  category="false_positives",
  description="'What app' is not 'what app is open'")

T("Describe how you would approach this problem", "llm",
  category="false_positives",
  description="'Describe how' is conversational, not 'describe screen'")

T("How many programming languages do you know?", "llm",
  category="false_positives",
  description="'How many' is conversational, not counting UI elements")

T("What changed your mind about that?", "llm",
  category="false_positives",
  description="'What changed' is conversational, not visual diff")

T("Compare the tradeoffs of using REST vs GraphQL", "llm",
  category="false_positives",
  description="'Compare' is analytical, not visual screenshot compare")

T("Is it possible to create a custom solution?", "llm",
  category="false_positives",
  description="'Create a custom solution' is conceptual, not image/file creation")

T("Can you see why that approach might fail?", "llm",
  category="false_positives",
  description="'Can you see why' is figurative, not 'can you see my screen'")

T("I want to convert my understanding into practical skills", "llm",
  category="false_positives",
  description="'convert' is figurative, not file conversion")

T("Where is the best place to learn Python?", "llm",
  category="false_positives",
  description="'Where is' is not 'where is UI element'")

T("What went wrong with the previous approach?", "llm",
  category="false_positives",
  description="Asking about past approach, not 'what went wrong on screen'")

T("Read about the history of the internet", "llm",
  category="false_positives",
  description="'Read about' is conversational, not clipboard read")

T("I noticed the error might be in the configuration", "llm",
  category="false_positives",
  description="Mentioning an error conceptually, not a screen error")

T("The notification about the meeting was helpful", "llm",
  category="false_positives",
  description="Mentioning a notification conversationally, not 'what notification'")

T("How do tabs work in the browser?", "llm",
  category="false_positives",
  description="'tabs' in conceptual question, not 'next tab'")

T("Volume up on the discussion about market trends", "llm",
  category="false_positives",
  description="'volume up' is figurative")

T("Undo the progress we've made on this", "llm",
  category="false_positives",
  description="'Undo' is figurative, not text editing undo")

T("Backspace through those ideas and try again", "llm",
  category="false_positives",
  description="'Backspace' used figuratively")

T("I need to redo my analysis of the data", "llm",
  category="false_positives",
  description="'Redo' used figuratively, not text editing redo")

# ─── CATEGORY: Tricky "this/that/it" references (MUST go to LLM) ───

T("This is really fascinating, can you tell me more?", "llm",
  category="this_that_it",
  description="'this' is conversational, not clipboard context")

T("That's not what I meant, let me clarify", "llm",
  category="this_that_it",
  description="'that's' is conversational")

T("It would be great if you could help me with this problem I've been working on", "llm",
  category="this_that_it",
  description="'it' and 'this' are conversational, not clipboard")

T("Can you rewrite the approach? I think there's a better way", "llm",
  category="this_that_it",
  description="'rewrite the approach' is conceptual, not clipboard rewrite")

T("Reword this to be more professional", "fast_action", "CLIPBOARD_REWORD",
  description="'reword this' IS a clipboard action - this SHOULD be fast-actioned")

T("Summarize this conversation for me", "llm",
  category="this_that_it",
  description="'summarize this conversation' should go to LLM, not clipboard")

T("Explain this concept in detail", "llm",
  category="this_that_it",
  description="'explain this concept' should go to LLM")

T("I think that is actually the right answer", "llm",
  category="this_that_it",
  description="'that' used as demonstrative pronoun, not clipboard context")

T("Read this passage from the textbook", "llm",
  category="this_that_it",
  description="'Read this passage' is conversational, not clipboard read")

T("Elaborate on this theory of relativity", "llm",
  category="this_that_it",
  description="'Elaborate on this theory' should go to LLM")

# ─── CATEGORY: Compound Sentences ───

T("What's your favorite color? And also take a screenshot", "llm",
  category="compound",
  description="Mixed conversation + action should go to LLM")

T("Can you help me with this code and also copy it?", "llm",
  category="compound",
  description="Mixed conversation + action")

T("I like that approach. Move mouse to center.", "fast_action", "MOUSE_MOVEMENT",
  description="Statement then clear command - command should win")

T("The algorithm is interesting. Can you explain how it works?", "llm",
  category="compound",
  description="Statement then question - should go to LLM")

T("That's helpful. Can you also show me how to implement it?", "llm",
  category="compound",
  description="Gratitude then question")

T("Great explanation! Now, what about edge cases?", "llm",
  category="compound",
  description="Positive feedback then question")

T("I understand that part. But what about error handling?", "llm",
  category="compound",
  description="Understanding then follow-up")

T("Interesting. Tell me more about distributed systems.", "llm",
  category="compound",
  description="Short reaction then question")

# ─── CATEGORY: Ambiguous Commands ───

T("Read about the history of the internet", "llm",
  category="ambiguous",
  description="'Read about' is educational, not clipboard read")

T("Read this book", "llm",
  category="ambiguous",
  description="'Read this book' could be clipboard or conversation")

T("Read this", "fast_action", "CLIPBOARD_READ",
  description="Short 'read this' IS clipboard action")

T("Read that article you mentioned earlier", "llm",
  category="ambiguous",
  description="'Read that article' is conversational reference")

T("Open my mind to new possibilities", "llm",
  category="ambiguous",
  description="'Open my mind' is figurative, not 'open app'")

T("Open Gmail", "fast_action", "OPEN_WINDOW",
  description="Clear app open command")

T("Can you focus on brave?", "fast_action", "OPEN_WINDOW",
  description="Polite focus/focus-on-app must not be swallowed as conversational")

T("Please bring up Safari", "fast_action", "OPEN_WINDOW",
  description="Bring up application routes to smart_open")

T("Show me how to create a React component", "llm",
  category="ambiguous",
  description="'Show me how' is educational")

T("Show me a screenshot", "fast_action", "SCREENSHOT_ANALYZE",
  description="Direct screenshot command")

T("Find a solution to this problem", "llm",
  category="ambiguous",
  description="'Find a solution' is conceptual")

T("Find the submit button", "fast_action", "SCREENSHOT_ANALYZE",
  description="Direct UI find command")

T("List the reasons why Python is popular", "llm",
  category="ambiguous",
  description="'List the reasons' is conversational")

T("List files in my downloads", "fast_action", "FILE_OPERATIONS",
  description="Direct file listing command")

T("Type your response in natural language", "llm",
  category="ambiguous",
  description="'Type your response' is conversational, not type_text")

T("Type hello world", "fast_action", "TYPE_TEXT",
  description="Direct type command")

# ─── CATEGORY: Long Multi-Paragraph Inputs (typical of strong models) ───

T("""I've been thinking about the problem we discussed earlier. The main issue
seems to be that the API responses are inconsistent. Sometimes we get a 200 status
code with the expected data, but other times we get timeout errors. What do you
think might be causing this inconsistency?""", "llm",
  category="long_paragraphs",
  description="Multi-paragraph problem description + question")

T("""There are several approaches we could take here. The first option is to use
a caching layer to reduce the number of API calls. The second option is to implement
retry logic with exponential backoff. And the third option is to switch to a different
API provider altogether. Which approach would you recommend?""", "llm",
  category="long_paragraphs",
  description="Multi-paragraph analysis + recommendation request")

T("""I'm building a real-time chat application and I need to decide on the tech stack.
On the backend, I'm considering Node.js with Socket.IO, Python with FastAPI and
WebSocket support, or Go with gorilla/websocket. For the frontend, I'm leaning toward
React with TypeScript. Can you help me weigh the pros and cons of each backend option?""", "llm",
  category="long_paragraphs",
  description="Detailed technical question with context")

T("""Let me give you some context first. We have a microservices architecture with
about 12 services communicating through a message queue. Recently, we've noticed that
service-to-service calls are getting slower. We've already checked the obvious things
like network latency and CPU usage, but everything looks normal. The database queries
are also fast. What else could be causing the slowdown?""", "llm",
  category="long_paragraphs",
  description="Detailed diagnostic question with context")

T("""I've read through the documentation for both frameworks and I'm still confused
about one thing. The official docs say that state management should be handled at the
component level, but all the tutorials I've found use a global store. Which approach
is actually recommended for production applications? And can you explain why?""", "llm",
  category="long_paragraphs",
  description="Documentation confusion + 'explain why' request")

# ─── CATEGORY: Context-Dependent References ───

T("What do you think about that?", "llm",
  category="context_references",
  description="Vague reference needs LLM understanding")

T("Can you go into more detail?", "llm",
  category="context_references",
  description="Vague request needs context")

T("And what about the other option?", "llm",
  category="context_references",
  description="Reference to previous conversation")

T("Now try again with a different approach", "llm",
  category="context_references",
  description="'try again' references previous attempt")

T("Also, the second part of your answer was unclear", "llm",
  category="context_references",
  description="References previous response")

T("I thought you said it would work differently", "llm",
  category="context_references",
  description="References previous conversation")

T("That's not quite right, try again", "llm",
  category="context_references",
  description="Correction with 'try again'")

T("No, that's not what I was asking about. I meant the part about error handling.", "llm",
  category="context_references",
  description="Clarification referencing previous exchange")

# ─── CATEGORY: Genuine Fast Action Commands (SHOULD be fast-actioned) ───

T("copy this", "fast_action", "CLIPBOARD_COPY",
  description="Direct copy command")

T("cut this", "fast_action", "CLIPBOARD_CUT",
  description="Direct cut command")

T("paste", "fast_action", "CLIPBOARD_PASTE",
  description="Direct paste command")

T("read this", "fast_action", "CLIPBOARD_READ",
  description="Direct read this command")

T("read that", "fast_action", "CLIPBOARD_READ",
  description="Direct read that command")

T("explain this", "fast_action", "CLIPBOARD_EXPLAIN",
  description="Direct explain this command")

T("summarize this", "fast_action", "CLIPBOARD_SUMMARIZE",
  description="Direct summarize this command")

T("rewrite this", "fast_action", "CLIPBOARD_REWRITE",
  description="Direct rewrite this command")

T("reword that", "fast_action", "CLIPBOARD_REWORD",
  description="Direct reword that command")

T("elaborate on this", "fast_action", "CLIPBOARD_ELABORATE",
  description="Direct elaborate this command")

T("take a screenshot", "fast_action", "SCREENSHOT_ANALYZE",
  description="Direct screenshot command")

T("move mouse to center", "fast_action", "MOUSE_MOVEMENT",
  description="Direct mouse movement")

T("clear chat", "fast_action", "CLEAR_CHAT",
  description="Direct clear chat")

T("new chat", "fast_action", "NEW_CHAT",
  description="Direct new chat")

T("type hello world", "fast_action", "TYPE_TEXT",
  description="Direct type command")

T("scroll down", "fast_action", "MOUSE_ACTION",
  description="Direct scroll command")

T("volume up", "fast_action", "MEDIA_CONTROL",
  description="Direct volume command")

T("next tab", "fast_action", "KEYBOARD_SHORTCUT",
  description="Direct tab navigation")

T("what's on my screen", "fast_action", "SCREENSHOT_ANALYZE",
  description="Direct screen query")

T("click the Submit button", "fast_action", "SCREENSHOT_ANALYZE",
  description="Direct UI click command")

T("open Gmail", "fast_action", "OPEN_WINDOW",
  description="Direct app open command")

T("summarize the clipboard", "fast_action", "CLIPBOARD_SUMMARIZE",
  description="Direct clipboard summarize command")

T("read from clipboard", "fast_action", "CLIPBOARD_READ",
  description="Direct clipboard read command")

T("move mouse top right", "fast_action", "MOUSE_MOVEMENT",
  description="Direct mouse movement")

T("exit app", "fast_action", "EXIT_APP",
  description="Direct exit command")

T("change mode", "fast_action", "CHANGE_MODE",
  description="Direct mode change command")

T("can you summarize this", "fast_action", "CLIPBOARD_SUMMARIZE",
  description="Polite clipboard summarize command")

T("can you explain that", "fast_action", "CLIPBOARD_EXPLAIN",
  description="Polite clipboard explain command")

T("convert to mp3", "fast_action", "FILE_CONVERT",
  description="Direct file conversion command")

T("create an image of a sunset", "fast_action", "IMAGE_GENERATE",
  description="Direct image generation command")

T("make a logo for my company", "fast_action", "IMAGE_GENERATE",
  description="Direct logo generation command")

T("delete the file", "fast_action", "FILE_DELETE",
  description="Direct file delete command")

T("tell cursor the build keeps failing", "fast_action", "CURSOR_TICKET",
  description="Direct cursor ticket command")

T("Can we talk through what work should be done in Cursor before sending anything?", "llm",
  category="polite_conversational",
  description="Conversation about Cursor work, not a handoff")

T("Can we talk through what work should be done in Codex before sending anything?", "llm",
  category="polite_conversational",
  description="Conversation about Codex work, not a handoff")

T("find skill for database", "fast_action", "SKILL_FIND",
  description="Direct skill find command")

# ─── CATEGORY: Polite Conversational (must go to LLM) ───

T("Can you help me understand recursion?", "llm",
  category="polite_conversational",
  description="Polite question about concept")

T("Could you please explain the difference between TCP and UDP?", "llm",
  category="polite_conversational",
  description="Polite explanation request")

T("Would you mind elaborating on that point?", "llm",
  category="polite_conversational",
  description="Polite elaboration request")

T("Can you tell me more about distributed systems?", "llm",
  category="polite_conversational",
  description="Polite 'tell me more' request")

T("Could you show me how to solve this algorithm?", "llm",
  category="polite_conversational",
  description="Polite 'show me how' - educational, not screenshot")

T("Would you be able to create a tutorial on Python?", "llm",
  category="polite_conversational",
  description="Polite creation request - conceptual, not image")

T("Can you find information about quantum computing?", "llm",
  category="polite_conversational",
  description="'Can you find information' - research, not skill/UI find")

T("Please explain why this approach doesn't work", "llm",
  category="polite_conversational",
  description="'Explain why this approach' - conceptual, not clipboard")

# ─── CATEGORY: Code-related Conversations (must go to LLM) ───

T("How do I fix this bug in my Python code?", "llm",
  category="code_conversation",
  description="Code question, not a tool command")

T("What's the best way to handle async errors in Rust?", "llm",
  category="code_conversation",
  description="Technical question")

T("Can you review this code and suggest improvements?", "llm",
  category="code_conversation",
  description="Code review request - 'this code' may clash but should go to LLM")

T("I keep getting a null pointer exception. Any ideas?", "llm",
  category="code_conversation",
  description="Error description question")

T("Why does my React component keep re-rendering?", "llm",
  category="code_conversation",
  description="Debugging question")

T("Write a function that checks if a number is prime", "llm",
  category="code_conversation",
  description="Code generation request")

T("Refactor this code to be more readable", "llm",
  category="code_conversation",
  description="Refactoring request - conceptual 'this'")

T("What's the difference between let, const, and var in JavaScript?", "llm",
  category="code_conversation",
  description="Technical explanation question")

# ─── CATEGORY: Edge Cases ───

T("", "llm",
  category="edge_cases",
  description="Empty input should go to LLM")

T("a", "llm",
  category="edge_cases",
  description="Single character should go to LLM")

T("hi", "llm",
  category="edge_cases",
  description="Greeting should go to LLM")

T("thanks", "llm",
  category="edge_cases",
  description="Thanks should go to LLM")

T("ok", "llm",
  category="edge_cases",
  description="OK should go to LLM")

T("yes", "llm",
  category="edge_cases",
  description="Yes should go to LLM")

T("no", "llm",
  category="edge_cases",
  description="No should go to LLM")

T("hello", "llm",
  category="edge_cases",
  description="Greeting")

T("good morning", "llm",
  category="edge_cases",
  description="Greeting")

T("goodbye", "fast_action", "EXIT_APP",
  description="Direct exit command")

T("bye", "fast_action", "EXIT_APP",
  description="Direct exit command")

T("thanks, that was really helpful!", "llm",
  category="edge_cases",
  description="Gratitude with detail")

T("I agree with that approach", "llm",
  category="edge_cases",
  description="Simple agreement")

T("interesting", "llm",
  category="edge_cases",
  description="Single word reaction")

T("hmm, let me think about that", "llm",
  category="edge_cases",
  description="Thinking response")

T("the", "llm",
  category="edge_cases",
  description="Single common word - should not match any pattern")

T("what", "llm",
  category="edge_cases",
  description="Single question word")

T("why is this not working", "fast_action", "SCREENSHOT_ANALYZE",
  description="'why is this not working' IS a screenshot analysis request")

T("what's wrong with this", "fast_action", "SCREENSHOT_ANALYZE",
  description="'what's wrong with this' IS a screenshot analysis request")

T("what do you see", "fast_action", "SCREENSHOT_ANALYZE",
  description="'what do you see' IS a screenshot analysis request")

T("look at my screen", "fast_action", "SCREENSHOT_ANALYZE",
  description="Direct screenshot command")

# ─── CATEGORY: STT Artifacts / Natural Speech ───

T("Uh, can you help me understand polymorphism?", "llm",
  category="stt_artifacts",
  description="Speech with filler, but clearly conversational")

T("Hmm, what's the best way to handle state management?", "llm",
  category="stt_artifacts",
  description="Speech with filler + question")

T("So like, I'm trying to figure out how callbacks work", "llm",
  category="stt_artifacts",
  description="Natural speech pattern")

T("You know, I think the issue might be with the database connection", "llm",
  category="stt_artifacts",
  description="Natural speech pattern")

T("Mass can you explain this formula to me", "llm",
  category="stt_artifacts",
  description="STT artifact: 'Mass' for 'mouse' — but 'explain this formula' is conversational, not clipboard")

T("Can you move the mass to the center", "fast_action", "MOUSE_MOVEMENT",
  description="STT artifact: 'mass' for 'mouse'")

# ─── CATEGORY: Domain-Specific Conversations ───

T("What are the implications of GDPR for small businesses?", "llm",
  category="domain_specific",
  description="Legal domain question")

T("How does photosynthesis convert light energy to chemical energy?", "llm",
  category="domain_specific",
  description="Biology domain question")

T("Can you explain the concept of monetary policy?", "llm",
  category="domain_specific",
  description="Economics domain question")

T("What causes earthquakes?", "llm",
  category="domain_specific",
  description="Geology domain question")

T("I'm trying to understand the differences between capitalism and socialism", "llm",
  category="domain_specific",
  description="Political science question")

T("How does machine learning apply to healthcare?", "llm",
  category="domain_specific",
  description="Interdisciplinary question")

T("What's the historical significance of the Renaissance?", "llm",
  category="domain_specific",
  description="History question")

# ─── CATEGORY: Instructions with Multiple Parts ───

T("First, explain the concept of closures. Then give me a practical example.", "llm",
  category="multi_part",
  description="Multi-part instruction should go to LLM")

T("Compare Docker and Kubernetes, and tell me when to use each", "llm",
  category="multi_part",
  description="Comparison + recommendation request")

T("Write a poem about programming, and make it rhyme in ABAB format", "llm",
  category="multi_part",
  description="Creative request with constraints")

T("Explain the theory, provide an example, and then tell me about common pitfalls", "llm",
  category="multi_part",
  description="Triple instruction should go to LLM")

T("Summarize the key points and elaborate on the most important one", "llm",
  category="multi_part",
  description="Compound summarize/elaborate without clipboard context")

# ─── CATEGORY: Questions that sound like commands ───

T("How do I create a new file in Linux?", "llm",
  category="command_like_questions",
  description="Question about creating files, not a create command")

T("What's the best way to copy files between servers?", "llm",
  category="command_like_questions",
  description="Question about copying, not a copy command")

T("Can you explain how to move files using the command line?", "llm",
  category="command_like_questions",
  description="Question about moving, not a move command")

T("How do you delete a branch in git?", "llm",
  category="command_like_questions",
  description="Question about deleting, not a delete command")

T("What's the shortcut for selecting all text?", "llm",
  category="command_like_questions",
  description="Question about shortcuts, not a shortcut command")

T("Where can I find documentation for this API?", "llm",
  category="command_like_questions",
  description="Question about finding, not a find command")

T("How would you open a file in Python?", "llm",
  category="command_like_questions",
  description="Question about opening, not an open command")

T("When should I use the paste command in vim?", "llm",
  category="command_like_questions",
  description="Question about pasting, not a paste command")

T("How do I read a CSV file in pandas?", "llm",
  category="command_like_questions",
  description="Question about reading, not a read command")

T("What's the scroll behavior in CSS?", "llm",
  category="command_like_questions",
  description="Question about scrolling, not a scroll command")

# ─── CATEGORY: Real-world GLM/Kimi style conversations ───

T("I'm working on a React application and I need to implement authentication. What's the best approach for handling JWT tokens on the client side?", "llm",
  category="strong_model_input",
  description="Typical strong model input - detailed context + question")

T("The microservice architecture we discussed earlier has a problem with service discovery. I think we should look into using Consul or etcd. What are your thoughts on that?", "llm",
  category="strong_model_input",
  description="Detailed context + opinion + question")

T("I've analyzed the performance metrics from our monitoring system and noticed that the p99 latency for the /api/users endpoint has increased by 300% over the last week. The database queries seem fine, so I suspect it's either the cache layer or the connection pool. Can you help me narrow down the possible causes?", "llm",
  category="strong_model_input",
  description="Long technical analysis + question")

T("Based on what you said earlier about the tradeoffs between horizontal and vertical scaling, I think vertical scaling might work for our current workload. But I'm concerned about the single point of failure. Is there a way to mitigate that risk while still keeping costs manageable?", "llm",
  category="strong_model_input",
  description="Referencing previous conversation + new question")

T("Let me share some context: we're building an e-commerce platform with about 50,000 daily active users. The current stack is Node.js with Express on the backend, React on the frontend, and PostgreSQL for the database. We're seeing some performance bottlenecks during peak hours, particularly around the checkout flow. I'd love to hear your suggestions for optimizing this.", "llm",
  category="strong_model_input",
  description="Context sharing + advice request")

T("Actually, I just realized something. The issue we've been debugging might not be a race condition at all. What if the problem is that our CDN cache invalidation isn't working properly? That would explain why users are seeing stale content. Can you walk me through how to verify this hypothesis?", "llm",
  category="strong_model_input",
  description="Hypothesis + verification request")

T("That's a great point about using WebSocket for real-time updates. But I'm worried about scalability. In our previous project, we had issues with WebSocket connections dropping when the server load went above 80%. Do you think using Socket.IO with its fallback to polling would help, or should we consider a completely different approach like server-sent events?", "llm",
  category="strong_model_input",
  description="Feedback + concern + comparison request")

T("I've been reading through the RFC for HTTP/3 and I find the move from TCP to QUIC really interesting. The reduced connection establishment time alone could be huge for our mobile users. But I'm not sure about the maturity of server implementations. Do you have any experience deploying HTTP/3 in production?", "llm",
  category="strong_model_input",
  description="Detailed analysis + question")

T("Wait, I think we're overcomplicating this. Let me step back and think about what we really need. The core requirement is: users should be able to see real-time notifications. We don't necessarily need full bidirectional communication. Would a simple long polling approach work for this use case, or am I missing something?", "llm",
  category="strong_model_input",
  description="Simplification + question")

T("One more thing I forgot to mention: our deployment pipeline uses GitHub Actions with a blue-green deployment strategy. When we roll out new changes, about 5% of requests fail during the switch. Do you think we need to implement proper connection draining, or is there a simpler fix?", "llm",
  category="strong_model_input",
  description="Additional context + question")


# ═══════════════════════════════════════════════════════════════════════════
# TEST RUNNER
# ═══════════════════════════════════════════════════════════════════════════

def run_all_tests(verbose=False, category_filter=None):
    """Run all tests and return results."""
    results = []
    test_cases = TEST_CASES

    if category_filter:
        test_cases = [tc for tc in test_cases if tc.category == category_filter]
        if not test_cases:
            print(f"No tests found for category: {category_filter}")
            print(f"Available categories: {sorted(set(tc.category for tc in TEST_CASES))}")
            return []

    for tc in test_cases:
        result = run_test(tc)
        results.append(result)

    return results


def print_report(results, verbose=False):
    """Print a detailed test report."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed
    accuracy = (passed / total * 100) if total > 0 else 0

    # Group by category
    categories = {}
    for r in results:
        cat = r.test.category or "uncategorized"
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(r)

    # ─── Summary ───
    print("\n" + "=" * 80)
    print("FAST ACTION DETECTOR — ACCURACY REPORT")
    print("=" * 80)
    print(f"\n  Total tests:    {total}")
    print(f"  Passed:         {passed}")
    print(f"  Failed:         {failed}")
    print(f"  Accuracy:       {accuracy:.1f}%")
    print(f"  Target:         80%")

    # ─── By category ───
    print(f"\n{'─' * 80}")
    print(f"  {'Category':<35} {'Pass':>5} {'Fail':>5} {'Total':>5} {'Acc%':>7}")
    print(f"{'─' * 80}")

    for cat in sorted(categories.keys()):
        cat_results = categories[cat]
        cat_passed = sum(1 for r in cat_results if r.passed)
        cat_total = len(cat_results)
        cat_acc = (cat_passed / cat_total * 100) if cat_total > 0 else 0
        cat_failed = cat_total - cat_passed
        marker = "✅" if cat_acc >= 80 else "❌"
        print(f"  {marker} {cat:<33} {cat_passed:>5} {cat_failed:>5} {cat_total:>5} {cat_acc:>6.1f}%")

    # ─── LLM routing accuracy ───
    llm_tests = [r for r in results if r.test.should_go_to_llm]
    llm_passed = sum(1 for r in llm_tests if r.passed)
    llm_total = len(llm_tests)
    llm_acc = (llm_passed / llm_total * 100) if llm_total > 0 else 0

    action_tests = [r for r in results if not r.test.should_go_to_llm]
    action_passed = sum(1 for r in action_tests if r.passed)
    action_total = len(action_tests)
    action_acc = (action_passed / action_total * 100) if action_total > 0 else 0

    print(f"\n{'─' * 80}")
    print(f"  ROUTING BREAKDOWN:")
    print(f"    LLM routing accuracy:      {llm_passed}/{llm_total} = {llm_acc:.1f}%")
    print(f"    Fast action accuracy:       {action_passed}/{action_total} = {action_acc:.1f}%")
    print(f"{'─' * 80}")

    # ─── Failures ───
    failures = [r for r in results if not r.passed]
    if failures:
        print(f"\n{'═' * 80}")
        print(f"  FAILURES ({len(failures)} total)")
        print(f"{'═' * 80}")

        # Group failures by root cause
        false_positives = [r for r in failures if r.test.should_go_to_llm]
        false_negatives = [r for r in failures if not r.test.should_go_to_llm]

        if false_positives:
            print(f"\n  ❌ FALSE POSITIVES (conversational input routed to fast action) — {len(false_positives)}:")
            print(f"  {'─' * 76}")
            for r in false_positives:
                print(f"    Input:    \"{r.test.input_text[:80]}\"")
                print(f"    Expected:  LLM routing")
                print(f"    Got:       {r.actual_action_type} → {r.actual_tool_name} ({r.actual_response_type})")
                print(f"    Category:  {r.test.category}")
                print()

        if false_negatives:
            print(f"\n  ❌ FALSE NEGATIVES (command input routed to LLM) — {len(false_negatives)}:")
            print(f"  {'─' * 76}")
            for r in false_negatives:
                print(f"    Input:    \"{r.test.input_text[:80]}\"")
                print(f"    Expected:  {r.test.expected_action_type}")
                print(f"    Got:       {r.actual_action_type} ({r.actual_response_type})")
                print(f"    Category:  {r.test.category}")
                print()
    else:
        print(f"\n  🎉 All tests passed!")

    if verbose:
        print(f"\n{'═' * 80}")
        print(f"  DETAILED RESULTS")
        print(f"{'═' * 80}")
        for r in results:
            status = "✅" if r.passed else "❌"
            route = "LLM" if r.went_to_llm else "FAST"
            print(f"  {status} [{route:>4}] {r.test.category:<25} | {r.actual_action_type:<25} | \"{r.test.input_text[:60]}\"")

    print()
    return accuracy


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Test the Fast Action Detector")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed results")
    parser.add_argument("--category", "-c", type=str, help="Filter by category", default=None)
    parser.add_argument("--list-categories", action="store_true", help="List available categories")
    parser.add_argument("--failing-only", action="store_true", help="Only show failing tests")
    args = parser.parse_args()

    if args.list_categories:
        categories = sorted(set(tc.category for tc in TEST_CASES))
        print("Available categories:")
        for cat in categories:
            count = sum(1 for tc in TEST_CASES if tc.category == cat)
            print(f"  {cat}: {count} tests")
        return

    results = run_all_tests(verbose=args.verbose, category_filter=args.category)
    accuracy = print_report(results, verbose=args.verbose)

    # Return exit code based on accuracy
    if accuracy >= 80:
        print(f"✅ Accuracy {accuracy:.1f}% meets the 80% target!")
        sys.exit(0)
    else:
        print(f"❌ Accuracy {accuracy:.1f}% is BELOW the 80% target!")
        sys.exit(1)


if __name__ == "__main__":
    main()
