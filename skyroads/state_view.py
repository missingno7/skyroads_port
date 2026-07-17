"""Re-export of dos_re.state_view at the skyroads top level.

Lives here (not under bridge/native) so those layers can build typed views
without importing dos_re directly -- skyroads/bridge and skyroads/recovered_native must
have zero VM dependency (tools/audit_layers.py enforces this; pitfall #17).
dos_re.state_view itself is pure (field descriptors + byte-buffer math, no
cpu/mem/VM imports) -- this shim exists only to keep the "zero direct dos_re
import" invariant uniform across every pure layer, mirroring skyroads/islands.py.
"""
from __future__ import annotations

from dos_re.state_view import (
    S8,
    S16,
    U8,
    U16,
    U16Array,
    ByteBackend,
    OverlayBackend,
    SegmentBackend,
    StructArray,
    StructView,
    WidthContractBackend,
    coerce_backend,
)

__all__ = [
    "S8", "S16", "U8", "U16", "U16Array",
    "ByteBackend", "OverlayBackend", "SegmentBackend",
    "StructArray", "StructView", "WidthContractBackend", "coerce_backend",
]
