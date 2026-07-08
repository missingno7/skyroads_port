"""Re-export of dos_re.islands at the skyroads top level.

Lives here (not under codecs/recovered) so every pure layer can tag its
functions with @oracle_link without importing dos_re directly — recovered/
and codecs/ must have zero VM dependency (tools/audit_layers.py enforces
this; pitfall #17). Mirrors pre2_port's pre2/islands.py.
"""
from __future__ import annotations

from dos_re.islands import OracleLink, STATUSES, collect_islands, oracle_link, render_manifest

__all__ = ["OracleLink", "STATUSES", "collect_islands", "oracle_link", "render_manifest"]
