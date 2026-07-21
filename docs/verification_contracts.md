# SkyRoads verification contracts

SkyRoads' faithful gameplay island has two different comparison surfaces.

While `skyroads.gameplay` owns the loop, it uses
`skyroads:gameplay:interior/v1`. The canonical projection includes named
gameplay state, consumed input, deterministic tick state, the OPL command
state, video mode/palette, and the rendered VGA aperture. It deliberately
excludes x86 registers and flags, instruction count, call depth, gameplay stack
scratch, Sound Blaster bookkeeping, and VGA programming order.

That means the gameplay body may become a fully native implementation without
pretending to maintain an ASM stack or sound-card object. It remains faithful
only while it supplies every required semantic field, aperture, and ordered
observable effect declared by the contract.

When gameplay exits to generated code, it uses one named continuation contract:

- `gameplay-result` and `gameplay-aborted` return at `1010:2C61`;
- `road-departure-transition` calls the generated `1010:0F05` continuation.

The exit projection compares the named exit and continuation, complete live
register file, stack coordinates and call depth, shared DOS memory image,
timer/instruction/PIC state, and Sound Blaster state where the receiving
generated code can still observe it. This is intentionally stricter than the
interior projection.

A future native menu/level-selection shell can move this seam outward. At that
point it may replace the guest-device portion only with a new declared semantic
or continuation contract; it cannot silently weaken the existing faithful
claim.

The full generic model and audio/render claim levels are documented in the
dos_re submodule at `docs/verification_contracts.md`.
