"""Write guard: a sealed week rejects board-mutating operations.

Guarded writes: generation (normal and force), swap confirmation and swap
revocation. Unsealing flips weeks.status back to 'ready' and restores
writes; it never touches the package bytes.

Force regeneration is allowed only on a writable (unsealed) week. After it
runs, projection.diff_cells can list where the live board now diverges from
the still-immutable package.
"""
from app.modules.week_seal.snapshot import get_seal

# Operations the guard understands; surfaced in the error payload.
OP_GENERATE = "generate"
OP_FORCE_REGENERATE = "force_regenerate"
OP_SWAP_CONFIRM = "swap_confirm"
OP_SWAP_REVOKE = "swap_revoke"

SEALED_STATUS = "sealed"


class SealedWeekError(Exception):
    """A board write was attempted against a sealed week."""

    def __init__(self, reason: str, week_id: int, op: str):
        super().__init__(reason)
        self.reason = reason
        self.week_id = week_id
        self.op = op


def is_sealed(c, week_id: int) -> bool:
    """Sealed iff weeks.status='sealed' AND a package row exists.

    The two must agree: a stray status without a package is treated as not
    sealed (nothing was ever frozen), and a package with a non-sealed status
    means the week was explicitly unsealed and writes are allowed again.
    """
    week = c.execute("SELECT status FROM weeks WHERE id=?", (week_id,)).fetchone()
    if week is None or week["status"] != SEALED_STATUS:
        return False
    return get_seal(c, week_id) is not None


def assert_writable(c, week_id: int, op: str) -> None:
    """Raise SealedWeekError(week_sealed) if the week is currently sealed."""
    if is_sealed(c, week_id):
        raise SealedWeekError("week_sealed", week_id, op)
