"""SkyRoads adapter package — game knowledge built on the game-agnostic dos_re framework.

WHO WROTE IT is the distinction that matters here, so the names carry it:

    handrecovered/         HAND-WRITTEN. Pure logic over VALUES; imports nothing
                           (see its own docstring). dos_re Stage 4 shape.
    handrecovered_native/  HAND-WRITTEN. Equally CPU-free, but addresses the DOS
                           memory image by raw offset. dos_re Stage 2 (CPULESS
                           LIFTED) shape, exactly.

    lifted/                GENERATED (dos_re.lift). Literal per-instruction
                           lifts. "DO NOT hand-edit in place."
    recovered/             RESERVED FOR GENERATED CPUless output -- absent today.

WHY recovered/ IS RESERVED
    dos_re's CPUless promotion (tools/cpuless_promote.py) emits, per function, a
    recovered module plus a CPU-ABI adapter, and its own documented convention
    points ``--import-base`` at ``mygame.recovered`` and the adapters at the
    lifted slot. Our HAND-WRITTEN pure tier used to live at exactly that path.
    Left alone, the first promotion run would have dropped machine-generated
    modules on top of hand-recovered, oracle-proven work -- silently, since both
    would have been "recovered".

    So the hand-written tiers moved out (2026-07-17) rather than the generator
    being aimed elsewhere: the conventional name should belong to the
    conventional output, and hand-written code should say so in its own name.

    ``--recovered-dir`` / ``--adapter-dir`` / ``--import-base`` are parameters,
    so this is a choice, not a constraint. It is recorded here because the cost
    of getting it wrong is invisible: generated code overwriting the only proven
    reference that generated code has.

THE TWO ARE NOT RIVALS
    The hand-written tiers are the SEMANTIC TARGET for the generated CPUless
    output, and an independent cross-check against it: two implementations of one
    routine, one hand-recovered with an oracle proof, one machine-generated from
    the recovery IR. Keep both; compare them.
"""
