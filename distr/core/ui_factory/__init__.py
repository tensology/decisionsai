"""Local-first, contract-driven UI generation for DecisionsAI."""

from .compiler import CompilationResult, compile_screen_contract
from .benchmark import representative_contracts, run_template_benchmark
from .routing import UIRouteDecision, choose_ui_execution_route
from .contracts import (
    AcceptanceCriterion,
    ContractValidationError,
    FieldContract,
    ScreenContract,
    ScreenType,
)

__all__ = [
    "AcceptanceCriterion",
    "CompilationResult",
    "ContractValidationError",
    "FieldContract",
    "ScreenContract",
    "ScreenType",
    "compile_screen_contract",
    "representative_contracts",
    "run_template_benchmark",
    "UIRouteDecision",
    "choose_ui_execution_route",
]
