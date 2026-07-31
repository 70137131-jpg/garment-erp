"""14.1 — lay planning.

The problem
-----------
Given a required quantity per size ``d[s]``, a library of markers where marker
``m`` yields ``ratio[m][s]`` garments of size ``s`` per ply and costs
``length[m]`` centimetres of fabric per ply, and a spreading limit
``max_plies``, choose integer ply counts ``p[m]`` to

    minimise   Σ length[m] × p[m]
    subject to Σ ratio[m][s] × p[m] ≥ d[s]   for every size s
               0 ≤ p[m] ≤ max_plies,  p[m] integer

This is an integer covering problem — the same family as cutting stock — and it
is NP-hard. There is no exact solver in this project's dependencies and adding
one is not justified by the size of a typical order.

What this module actually does
------------------------------
A two-phase heuristic, named honestly in ``CutPlan.algorithm`` as
``greedy-coverage+ply-reduction``:

1. **Greedy coverage.** Repeatedly pick the marker giving the most *still-needed*
   garments per centimetre of fabric. Spread it only as deep as the first
   still-needed size it satisfies — not as deep as the deepest — so the lay
   stops before it starts manufacturing sizes nobody ordered.
2. **Ply reduction.** Walk the chosen lays and shave plies while every size
   remains covered. Greedy overshoots on its last lay almost every time; this
   pass reclaims that fabric.

It is **not** guaranteed optimal, and the result reports overcut per size and
weighted efficiency so a planner can see exactly what they are being handed
rather than being asked to trust a number. In practice, on the marker libraries
a garment factory actually maintains — a handful of markers per style — the gap
to optimal is small and the plan is one a cutting room can execute.

Termination is guaranteed: every greedy iteration strictly reduces the total
outstanding quantity, and a hard iteration cap bounds pathological inputs.
"""

from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple

_ZERO = Decimal("0")

# Bounds a pathological marker library (e.g. markers that cover no needed size)
# rather than letting the loop run away.
_MAX_ITERATIONS = 2000


class SolverError(Exception):
    """The plan cannot be solved as specified."""


class MarkerSpec:
    """The solver's view of a marker — geometry plus what it yields."""

    __slots__ = ("marker_id", "code", "length_cm", "ratio", "max_plies", "efficiency_pct")

    def __init__(
        self,
        marker_id: int,
        code: str,
        length_cm: Decimal,
        ratio: Dict[str, int],
        max_plies: int,
        efficiency_pct: Decimal = _ZERO,
    ):
        self.marker_id = marker_id
        self.code = code
        self.length_cm = Decimal(length_cm)
        self.ratio = {s: q for s, q in ratio.items() if q > 0}
        self.max_plies = max_plies
        self.efficiency_pct = efficiency_pct

    @property
    def pieces_per_ply(self) -> int:
        return sum(self.ratio.values())


class Lay:
    __slots__ = ("marker", "plies", "sequence")

    def __init__(self, marker: MarkerSpec, plies: int, sequence: int = 0):
        self.marker = marker
        self.plies = plies
        self.sequence = sequence

    @property
    def fabric_cm(self) -> Decimal:
        return self.marker.length_cm * Decimal(self.plies)

    @property
    def pieces(self) -> int:
        return self.marker.pieces_per_ply * self.plies


def _produced(lays: Sequence[Lay]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for lay in lays:
        for size, per_ply in lay.marker.ratio.items():
            out[size] = out.get(size, 0) + per_ply * lay.plies
    return out


def _covers(lays: Sequence[Lay], demand: Dict[str, int]) -> bool:
    produced = _produced(lays)
    return all(produced.get(size, 0) >= qty for size, qty in demand.items())


def _greedy(
    demand: Dict[str, int], markers: Sequence[MarkerSpec], max_plies: int
) -> List[Lay]:
    remaining = {s: q for s, q in demand.items() if q > 0}
    lays: List[Lay] = []
    iterations = 0

    while remaining and iterations < _MAX_ITERATIONS:
        iterations += 1

        best: Optional[MarkerSpec] = None
        best_score = _ZERO
        for marker in markers:
            if marker.length_cm <= 0:
                continue
            # Only count garments that are still wanted. A marker packed with
            # sizes we have already finished is not efficient, it is waste.
            useful = sum(
                min(per_ply, remaining.get(size, 0))
                for size, per_ply in marker.ratio.items()
            )
            if useful <= 0:
                continue
            score = Decimal(useful) / marker.length_cm
            if score > best_score:
                best_score = score
                best = marker

        if best is None:
            # No marker can serve any outstanding size.
            break

        # Spread only until the *first* outstanding size is satisfied. Going
        # deeper would over-produce every other size in the marker.
        plies_options = [
            -(-remaining[size] // per_ply)  # ceil division
            for size, per_ply in best.ratio.items()
            if remaining.get(size, 0) > 0
        ]
        plies = min(plies_options) if plies_options else 1
        plies = max(1, min(plies, max_plies, best.max_plies))

        lays.append(Lay(best, plies))
        for size, per_ply in best.ratio.items():
            if size in remaining:
                remaining[size] -= per_ply * plies
                if remaining[size] <= 0:
                    del remaining[size]

    if remaining:
        missing = ", ".join(sorted(remaining))
        raise SolverError(
            f"No available marker can produce these sizes: {missing}"
        )
    return lays


def _merge(lays: Sequence[Lay]) -> List[Lay]:
    """Combine repeated markers into one lay, respecting the ply ceiling."""
    merged: List[Lay] = []
    for lay in lays:
        for existing in merged:
            if existing.marker.marker_id == lay.marker.marker_id:
                room = min(lay.marker.max_plies, existing.marker.max_plies) - existing.plies
                take = min(lay.plies, max(0, room))
                if take > 0:
                    existing.plies += take
                    lay.plies -= take
                if lay.plies == 0:
                    break
        if lay.plies > 0:
            merged.append(Lay(lay.marker, lay.plies))
    return [l for l in merged if l.plies > 0]


def _reduce_plies(lays: List[Lay], demand: Dict[str, int]) -> List[Lay]:
    """Shave plies while demand stays covered.

    Greedy's final lay routinely overshoots. Removing plies from the *most
    expensive* fabric first reclaims the most metres for the same lost pieces.
    """
    ordered = sorted(lays, key=lambda l: l.marker.length_cm, reverse=True)
    for lay in ordered:
        while lay.plies > 0:
            lay.plies -= 1
            if not _covers(ordered, demand):
                lay.plies += 1
                break
    return [l for l in ordered if l.plies > 0]


def solve_lay_plan(
    demand: Dict[str, int],
    markers: Sequence[MarkerSpec],
    max_plies: int = 100,
) -> Tuple[List[Lay], Dict[str, int]]:
    """Return the chosen lays and the produced quantity per size.

    Raises :class:`SolverError` when the demand cannot be met by the supplied
    markers, rather than silently returning a plan that under-delivers.
    """
    positive = {s: q for s, q in demand.items() if q > 0}
    if not positive:
        raise SolverError("There is nothing to cut")
    if not markers:
        raise SolverError("No markers are available for this style and width")
    if max_plies < 1:
        raise SolverError("Maximum plies must be at least one")
    for marker in markers:
        if marker.length_cm <= 0:
            raise SolverError(f"Marker {marker.code} has no length")

    lays = _greedy(positive, markers, max_plies)
    lays = _merge(lays)
    lays = _reduce_plies(lays, positive)

    # Re-order for the cutting room: longest spread first is the conventional
    # sequence, and it keeps the biggest fabric commitment at the front where
    # a supervisor will see it.
    lays.sort(key=lambda l: (l.plies * l.marker.length_cm), reverse=True)
    for index, lay in enumerate(lays, start=1):
        lay.sequence = index

    return lays, _produced(lays)


def weighted_efficiency(lays: Sequence[Lay]) -> Decimal:
    """Fabric-weighted marker efficiency across the plan."""
    total_fabric = sum((l.fabric_cm for l in lays), _ZERO)
    if total_fabric <= 0:
        return _ZERO
    weighted = sum((l.marker.efficiency_pct * l.fabric_cm for l in lays), _ZERO)
    return (weighted / total_fabric).quantize(Decimal("0.000001"))
