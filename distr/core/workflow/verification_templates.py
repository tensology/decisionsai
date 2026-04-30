"""
Verification templates for structured workflow validation.

Each template defines a set of categories with checklist items.
Instead of free-text verification strings, workflows can reference
a named template that the agent systematically checks against.
"""

from typing import Dict, List


class VerificationCategory:
    """A single category of verification checks."""
    def __init__(self, name: str, items: List[str]):
        self.name = name
        self.items = items


VERIFICATION_TEMPLATES: Dict[str, List[VerificationCategory]] = {
    "web_app": [
        VerificationCategory("Auth & Security", [
            "Login with valid credentials succeeds",
            "Login with invalid credentials shows error",
            "Expired session redirects to login",
            "Protected routes require authentication",
            "No secrets exposed in client-side code or network responses",
            "CSRF protection on state-changing requests",
        ]),
        VerificationCategory("Core Flows", [
            "Primary user action completes end-to-end",
            "Create → Read → Update → Delete cycle works",
            "Form validation catches invalid input",
            "Required fields show validation errors",
            "Navigation (links, back button, deep links) works correctly",
        ]),
        VerificationCategory("Error Handling", [
            "API errors show user-friendly messages",
            "Network failures show retry option",
            "404 pages show helpful navigation",
            "500 errors show fallback UI (not white screen)",
            "Rate limiting shows appropriate messaging",
        ]),
        VerificationCategory("UI States", [
            "Loading states (spinner/skeleton) appear during async operations",
            "Empty states show helpful messaging",
            "Error states show recovery path",
            "Hover/focus states visible on all interactive elements",
            "Disabled states visibly different from enabled",
        ]),
        VerificationCategory("Responsive", [
            "Layout works at mobile (375px)",
            "Layout works at tablet (768px)",
            "Layout works at desktop (1280px+)",
            "Touch targets are 44px minimum on mobile",
            "No horizontal scroll on mobile",
        ]),
    ],
    "api": [
        VerificationCategory("Endpoints", [
            "All endpoints return correct status codes",
            "Request validation rejects invalid input",
            "Response format matches API spec",
            "Pagination works correctly",
            "Rate limiting headers present",
        ]),
        VerificationCategory("Error Handling", [
            "400 errors include field-level details",
            "401/403 errors don't leak resource info",
            "500 errors return generic message (no stack traces)",
            "Timeout handling with appropriate status",
            "Idempotency on POST/PUT where applicable",
        ]),
        VerificationCategory("Security", [
            "Auth required on all non-public endpoints",
            "CORS configured correctly",
            "Input sanitization on all endpoints",
            "No sensitive data in error responses",
            "Request size limits enforced",
        ]),
    ],
    "cli": [
        VerificationCategory("Execution", [
            "Command runs without errors on valid input",
            "Invalid input produces clear error message",
            "--help flag prints usage",
            "Exit codes correct (0=success, 1=error)",
            "Progress indication for long-running operations",
        ]),
        VerificationCategory("Edge Cases", [
            "Handles empty input gracefully",
            "Handles large input without crashing",
            "Permissions errors have clear messaging",
            "Network-dependent commands fail gracefully",
            "Concurrent execution doesn't corrupt state",
        ]),
    ],
    "security": [
        VerificationCategory("OWASP Top 10", [
            "No SQL/LDAP/OS command injection points",
            "Authentication bypass not possible",
            "Sensitive data not exposed in URLs, logs, or client code",
            "Access control checks on all protected resources",
            "Security misconfiguration checked (defaults, debug mode)",
        ]),
        VerificationCategory("Infrastructure", [
            "No hardcoded secrets (API keys, passwords, tokens)",
            "HTTPS enforced in production config",
            "Dependencies scanned for known CVEs",
            "Content Security Policy configured",
            "Rate limiting on authentication endpoints",
        ]),
        VerificationCategory("LLM-specific", [
            "Prompt injection vectors identified and mitigated",
            "User input never directly in system prompts",
            "Agent tool access scoped to minimum required",
            "No sensitive data in LLM context that could leak",
            "Output validation before execution of generated code",
        ]),
    ],
}


def get_template(template_name: str) -> List[VerificationCategory]:
    """Return a verification template by name. Returns empty list if not found."""
    return VERIFICATION_TEMPLATES.get(template_name, [])


def list_templates() -> List[str]:
    """Return available template names."""
    return list(VERIFICATION_TEMPLATES.keys())
