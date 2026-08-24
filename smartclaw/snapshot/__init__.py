"""
Snapshot module

Provides Git-based snapshot functionality for tracking and reverting file changes.
Based on SmartClaw' ported src/snapshot/index.ts
"""

from smartclaw.snapshot.snapshot import (
    Snapshot,
    SnapshotPatch,
    FileDiff,
)

__all__ = [
    "Snapshot",
    "SnapshotPatch",
    "FileDiff",
]
