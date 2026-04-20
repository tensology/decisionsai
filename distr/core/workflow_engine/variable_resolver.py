"""HTTP request variable resolution for the workflow engine.

Resolves {{step_N}}, {{step_N.field}}, and {{variable_name}} placeholders
in HTTP request configuration from previous step outputs and explicit variables.
"""

import json
import logging
import re
from copy import deepcopy
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Pattern to find all {{...}} placeholders
_PLACEHOLDER_RE = re.compile(r"\{\{([^}]+)\}\}")


def _build_variable_context(
    previous_step_results: List[dict],
    explicit_variables: Dict[str, str],
) -> Dict[str, str]:
    """Build a flat variable context from previous step results and explicit variables.

    For each previous step result at index *i* (0-based), the following keys are
    created:
      - ``step_{i+1}`` → full result text
      - ``step_{i+1}.{field}`` → individual fields if the result is valid JSON dict

    Explicit variables from the config's ``variables`` map are merged in afterwards,
    so they can override step-derived keys if names collide.
    """
    var_context: Dict[str, str] = {}

    for i, step_result in enumerate(previous_step_results):
        result_text = step_result.get("result", "")
        var_context[f"step_{i + 1}"] = str(result_text)

        # Try to parse JSON results for field-level access
        try:
            parsed = json.loads(result_text)
            if isinstance(parsed, dict):
                for key, val in parsed.items():
                    var_context[f"step_{i + 1}.{key}"] = str(val)
        except (json.JSONDecodeError, TypeError):
            pass

    # Explicit variables take precedence
    var_context.update(explicit_variables)

    return var_context


def _substitute(text: str, var_context: Dict[str, str]) -> str:
    """Replace all resolvable ``{{key}}`` placeholders in *text*.

    Unresolvable placeholders are left as-is and a warning is logged for each.
    """
    if not text:
        return text

    def _replacer(match: re.Match) -> str:
        key = match.group(1)
        if key in var_context:
            return var_context[key]
        logger.warning("Unresolvable variable placeholder: {{%s}}", key)
        return match.group(0)  # leave as-is

    return _PLACEHOLDER_RE.sub(_replacer, text)


def resolve_variables(
    text: str,
    previous_step_results: List[dict],
    explicit_variables: Optional[Dict[str, str]] = None,
) -> str:
    """Resolve ``{{variable}}`` placeholders in a plain text string.

    Parameters
    ----------
    text:
        The text containing ``{{...}}`` placeholders to resolve.
    previous_step_results:
        Ordered list of completed step results.  Each entry is a dict that
        should contain a ``"result"`` key with the step's output text.
    explicit_variables:
        Optional mapping of variable names to values.  These take precedence
        over step-derived variables.

    Returns
    -------
    str
        The text with all resolvable placeholders replaced.  Unresolvable
        placeholders are left as-is.
    """
    if not text:
        return text
    var_context = _build_variable_context(
        previous_step_results, explicit_variables or {},
    )
    return _substitute(text, var_context)


def resolve_http_variables(
    config: dict,
    previous_step_results: List[dict],
) -> dict:
    """Resolve variable placeholders in HTTP request config from previous step outputs.

    Parameters
    ----------
    config:
        A dict with keys such as ``url``, ``method``, ``headers``, ``body``,
        and ``variables``.  The ``variables`` key is an explicit mapping of
        variable names to values.
    previous_step_results:
        Ordered list of completed step results.  Each entry is a dict that
        should contain a ``"result"`` key with the step's output text.

    Returns
    -------
    dict
        A *new* config dict with all resolvable ``{{...}}`` placeholders
        replaced.  The original *config* is not mutated.

    Notes
    -----
    - ``{{step_N}}`` resolves to the full result text of step N (1-indexed).
    - ``{{step_N.field}}`` resolves to a specific field from step N's JSON result.
    - ``{{variable_name}}`` resolves from the explicit ``variables`` map.
    - Unresolvable placeholders are left as-is and a warning is logged.
    """
    resolved = deepcopy(config)

    explicit_variables: Dict[str, str] = config.get("variables", {}) or {}
    var_context = _build_variable_context(previous_step_results, explicit_variables)

    # Resolve URL
    if "url" in resolved:
        resolved["url"] = _substitute(resolved["url"], var_context)

    # Resolve header values
    if "headers" in resolved and isinstance(resolved["headers"], dict):
        resolved["headers"] = {
            k: _substitute(v, var_context) for k, v in resolved["headers"].items()
        }

    # Resolve body
    if "body" in resolved and resolved["body"]:
        resolved["body"] = _substitute(resolved["body"], var_context)

    return resolved
