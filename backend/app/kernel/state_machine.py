from typing import Dict, Set


class InvalidTransition(Exception):
    """Raised when a document is moved between states that are not connected."""


class StateMachine:
    """Declarative status-transition guard (blueprint A3: status-driven workflow).

    Each document type declares its allowed transitions once; services call
    ``assert_transition`` before changing status so illegal moves (e.g. shipping
    an unconfirmed order) are impossible.
    """

    def __init__(self, transitions: Dict[str, Set[str]], initial: str):
        self.transitions = transitions
        self.initial = initial

    def can(self, current: str, target: str) -> bool:
        return target in self.transitions.get(current, set())

    def assert_transition(self, current: str, target: str) -> None:
        if not self.can(current, target):
            raise InvalidTransition(
                f"Cannot move from '{current}' to '{target}'"
            )
