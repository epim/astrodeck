"""Frame sync: get a night's data off the rig and onto something that can
process it, incrementally, while the run is still going.

The public surface is deliberately small and PURE — see :mod:`.manifest`. The
transports (HTTP pull today; SMB/SFTP/object-store push later) hold no logic of
their own beyond "list what you have" and "give/take these bytes", because the
moment a direction owns a copy of the reconciliation rules the two directions
start to drift and only one of them gets the next bug fix.
"""
from .manifest import (Diff, Entry, FileFacts, Manifest, build, diff,
                       hash_file)

__all__ = ["Diff", "Entry", "FileFacts", "Manifest", "build", "diff",
           "hash_file"]
