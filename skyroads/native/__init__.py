"""Native (VM-less) SkyRoads — recovered islands composed into a real frame stepper.

Pure orchestration only: this package imports skyroads.recovered + skyroads.bridge
and composes them against a NativeGameState, never dos_re/cpu/mem. Audited by
``python tools/audit_layers.py skyroads/recovered skyroads/native skyroads/bridge``
(pitfall #17; see tests/test_layer_audit.py). Where a per-frame step needs a
routine that is not yet recovered, it raises a typed gap (skyroads.native.gaps)
instead of guessing — see docs/skyroads/vmless_roadmap.md.
"""
