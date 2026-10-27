"""Queue ordering rules (SPEC section 6, Queue). Pure functions, no database access.

Entries carry a stored `position`. A new entry gets its position once, from the round-robin rule; a host move
only rewrites the moved entry's position. So manual order survives later inserts.
"""
from dataclasses import dataclass

UPCOMING = ("waiting", "preparing", "ready", "failed")
VOCAL_LEVELS = (0, 5, 10, 15)
KEY_RANGE = range(-3, 4)


@dataclass
class Entry:
    id: int
    guest: str          # added_by: the guest whose turn this entry uses
    position: float
    status: str = "waiting"


def rounds(entries: list[Entry]) -> list[int]:
    """Round of each entry = how many entries of the same guest come before it."""
    seen: dict[str, int] = {}
    out = []
    for e in entries:
        out.append(seen.get(e.guest, 0))
        seen[e.guest] = out[-1] + 1
    return out


def insert_index(upcoming: list[Entry], guest: str, guest_order: list[str]) -> int:
    """Index at which a new entry of `guest` goes into `upcoming` (sorted by position).

    Round-robin: each guest's next entry takes turns, guests in the order they first queued. The new entry's
    turn is (its round = the guest's number of upcoming entries, the guest's rank); it goes right after the
    last entry whose turn comes before it. Without manual moves that is the same as "before the first later
    turn"; after a host reorder it never lets a new entry overtake an earlier turn.
    """
    rank = {g: i for i, g in enumerate(guest_order)}
    me = (sum(1 for e in upcoming if e.guest == guest), rank.get(guest, len(rank)))
    index = 0
    for i, (e, r) in enumerate(zip(upcoming, rounds(upcoming))):
        if (r, rank.get(e.guest, len(rank))) <= me:
            index = i + 1
    return index


def position_at(upcoming: list[Entry], index: int, exclude_id: int | None = None) -> float:
    """A position value that sorts at `index` among `upcoming` (without the entry `exclude_id`)."""
    rest = [e for e in upcoming if e.id != exclude_id]
    if not rest:
        return 1.0
    if index <= 0:
        return rest[0].position - 1.0
    if index >= len(rest):
        return rest[-1].position + 1.0
    return (rest[index - 1].position + rest[index].position) / 2


def needs_renumber(upcoming: list[Entry]) -> bool:
    return any(b.position - a.position < 1e-6 for a, b in zip(upcoming, upcoming[1:]))


def next_to_play(upcoming: list[Entry]) -> Entry | None:
    """The head if it's ready; otherwise the first ready entry (the unready head stays on top)."""
    return next((e for e in upcoming if e.status == "ready"), None)


def can_remove(entry_guest: str, guest_id: str | None, is_host: bool) -> bool:
    return is_host or (guest_id is not None and guest_id == entry_guest)
