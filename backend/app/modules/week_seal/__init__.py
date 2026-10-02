"""Week seal: immutable cell-diff package written at the seal moment.

Submodules:
- snapshot:   write the canonical, immutable package (INSERT only, never UPDATE)
- guard:      read-only sealed-state checks that reject board writes
- projection: diff the live board against the frozen package
"""
from app.modules.week_seal.snapshot import (
    SNAPSHOT_FORMAT,
    SealedPackageError,
    build_package_bytes,
    create_seal,
    get_seal,
    read_package,
)
from app.modules.week_seal.guard import (
    SealedWeekError,
    assert_writable,
)
from app.modules.week_seal.projection import diff_cells

__all__ = [
    "SNAPSHOT_FORMAT",
    "SealedPackageError",
    "SealedWeekError",
    "build_package_bytes",
    "create_seal",
    "get_seal",
    "read_package",
    "assert_writable",
    "diff_cells",
]
