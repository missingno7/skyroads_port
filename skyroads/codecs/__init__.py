"""Native decoders for SKYROADS's packed asset formats (the .LZS/.DAT files in assets/).

Pure — no dos_re import — round-trip/decode tests carry the proof, cross-checked
against the VM oracle's own decompression output (never guessed from the raw
bytes alone; see docs/history/pitfalls.md #21).
"""
