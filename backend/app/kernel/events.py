from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, DefaultDict, List, Type


@dataclass
class DomainEvent:
    """Base for operational events (GoodsReceiptPosted, ProductionConfirmed, ...).

    This is the seam that keeps Finance auto-posting (blueprint 8.3) decoupled
    from operations: modules ``emit`` events, subscribers (Finance, later
    analytics) react. Synchronous in-process for the MVP; swappable for a queue
    in V1 without touching the emitters.
    """


_subscribers: DefaultDict[Type["DomainEvent"], List[Callable]] = defaultdict(list)


def subscribe(event_type: Type[DomainEvent], handler: Callable) -> None:
    # Idempotent: registering the same handler twice must not double-fire it
    # (which would double-post finance journals).
    if handler not in _subscribers[event_type]:
        _subscribers[event_type].append(handler)


def emit(event: DomainEvent) -> None:
    for handler in _subscribers[type(event)]:
        handler(event)


def clear_subscribers() -> None:
    """Test helper — reset the registry between cases."""
    _subscribers.clear()
