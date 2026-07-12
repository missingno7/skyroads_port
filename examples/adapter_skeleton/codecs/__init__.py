"""TEMPLATE — native decoders for the game's packed asset formats.

Same purity rule as ``recovered/``: no ``dos_re`` imports (the audit covers
both). Codecs are usually the FIRST islands — clean boundaries, big VM
speed-up, and every decoded asset makes the system more observable.

The proof for a codec is round-trip: decode → re-encode → byte-identical to
the original file/image, over the whole asset set — plus the hook oracle at
the original decode routine while the hybrid runtime still runs it.
"""
