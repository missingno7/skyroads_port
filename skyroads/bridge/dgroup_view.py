"""Byte-backed typed view over SkyRoads' DGROUP -- the layout bridge.

See docs/state_mirrors.md and dos_re/docs/state_mirrors.md (the shared
machinery this instantiates: ``dos_re.state_view``, promoted from
pre2_port's ``pre2/bridge/dgroup_view.py``). Recovered logic
(skyroads/handrecovered/*) and the native frame stepper (skyroads/native/*)
operate on ``GameView`` fields (``view.ship_pos``, ``view.game_state``) and
never see a DGROUP offset; THIS module is the only place those offsets are
written down for skyroads. The same view can run over EITHER a
``NativeGameState`` (skyroads.native.state, whose ``.data`` is already just
the 64 KB DGROUP -- pass ``base=0``, the default) or a live VM ``mem``
(whose ``.data`` is the full 1 MB real-mode image -- pass
``base=ds_segment << 4``); ``coerce_backend`` wraps either in a
``ByteBackend`` at the given base. Unlike pre2's ``dgroup_view``, the base
is a parameter here, not a module constant -- skyroads' native image has no
other segments (yet) to offset against.

Field offsets are taken directly from the ``@oracle_link`` docstrings in
skyroads/handrecovered/player.py, movement.py, controls.py and menu.py -- see
those modules for the verification status of each field's *consumer*, not
just its address.
"""
from __future__ import annotations

from skyroads.state_view import U16, StructView, coerce_backend


class _KeyRow:
    """Adapts a backend to ``Sequence[int]`` indexed by ABSOLUTE DGROUP offset
    (``key_row[0x0BD2]``), matching skyroads.handrecovered.controls.decode_keyboard's
    signature -- it indexes by the raw offsets it documents, not a 0-based array."""

    __slots__ = ("_backend",)

    def __init__(self, backend):
        self._backend = backend

    def __getitem__(self, off: int) -> int:
        return self._backend.rb(off)


class GameView(StructView):
    """SkyRoads' DGROUP (``ds == 0x1686`` in every captured runtime), named."""

    def __init__(self, source, base: int = 0):
        super().__init__(coerce_backend(source, base), 0)

    # -- forward motion / bounce (player.py) ------------------------------------------------
    # RAW (unsigned) words, matching every recovered function's contract: each
    # one sign-extends its own inputs internally (e.g. decay_bounce's
    # `bounce & 0x8000` test), so handing it an already-Python-signed value
    # would double-convert. Read these as-is; a caller wanting a signed
    # reading applies the same `v - 0x10000 if v & 0x8000 else v` the
    # recovered functions do.
    speed = U16(0x9330)            # forward(+)/back(-) axis; advance_ship's `speed`
    bounce = U16(0x9336)           # vertical velocity (decay_bounce / update_vertical_velocity)
    game_state = U16(0x456E)       # 3 == gameplay; 2 == level-select (menu.py)
    #: ds:[456A] -- "entered" (level-select latch, menu.py) and "grounded"
    #: (player.py's update_vertical_velocity) are the SAME field read two
    #: different ways depending on game_state; both names alias it (see
    #: docs/state_mirrors.md's width-alias convention, extended to modes).
    entered = U16(0x456A)
    grounded = U16(0x456A)
    gravity = U16(0x54AA)          # ds:[54AA], per-level signed gravity accel (raw word)
    jump = U16(0x547A)             # jump request (0/1), also decode_keyboard's `jump` output
    jump_level_gate = U16(0x4562)  # per-level constant the (unrecovered) jump latch compares against
    steer = U16(0x95F4)            # left(-)/right(+) axis, decode_keyboard's `steer` output (raw word)
    lateral_accel = U16(0x4568)    # steer*29 accumulator feeding the (unrecovered) vertical target term

    # -- movement.py's three swept-collision axes --------------------------------------------
    af1c = U16(0xAF1C)             # depth/vertical accumulator A
    af2c = U16(0xAF2C)             # depth/vertical accumulator B == view Y base

    # -- level-select / respawn timers (menu.py, RespawnState) --------------------------------
    timer_a = U16(0x5494)          # ds:[5494] distance/"fuel" timer (progression.py)
    timer_b = U16(0xB13C)          # ds:[B13C] time/"oxygen" timer (progression.py)
    timer_a_param = U16(0x54A2)    # ds:[54A2] per-level fuel-rate divisor (progression.py)
    timer_b_param = U16(0x4566)    # ds:[4566] per-level oxygen-rate divisor (progression.py)
    effect_gate = U16(0x4570)      # ds:[4570] gates the 25AC-25D6 one-shot effect (dynamics.py)
    f41c0 = U16(0x41C0)            # ds:[41C0] the fall-death lateral-threshold base (loop.py, 23CA)

    # -- respawn/reset fields not yet named elsewhere (player.py's RespawnState) --------------
    unknown_5496 = U16(0x5496)
    frame_ctr = U16(0x4558)
    unknown_455a = U16(0x455A)
    unknown_af2e = U16(0xAF2E)
    unknown_af30 = U16(0xAF30)
    unknown_af38 = U16(0xAF38)
    elapsed_ticks = U16(0x1600)    # the frame-pacing tick counter (skyroads/pacing.py)

    # -- keyboard input row (controls.py) ------------------------------------------------------
    @property
    def key_row(self) -> _KeyRow:
        return _KeyRow(self._backend)

    @property
    def rw(self):
        """The backend's DGROUP word-reader, for the collision/classify predicates
        (``skyroads.native.collision.make_visible`` / ``classify.classify_ship``)."""
        return self._backend.rw

    # -- 32-bit fields: state_view has no U32 descriptor (dos_re/state_view.py), so these are
    # plain lo/hi word compositions over the same backend -- not a new descriptor class, since
    # only these two fields need it (see dos_re/docs/state_mirrors.md's "leave genuinely
    # union/raw offsets as backend access with a comment" guidance, extended to widths).
    @property
    def ship_pos(self) -> int:
        """ds:[54AC:54AE] -- forward road position (advance_ship); reused as the
        level-select scroll position while game_state != 3 (menu.py)."""
        return self._backend.rw(0x54AC) | (self._backend.rw(0x54AE) << 16)

    @ship_pos.setter
    def ship_pos(self, v: int) -> None:
        v &= 0xFFFFFFFF
        self._backend.ww(0x54AC, v & 0xFFFF)
        self._backend.ww(0x54AE, (v >> 16) & 0xFFFF)

    @property
    def lateral(self) -> int:
        """ds:[9618:961A] -- 32-bit lateral (lane) position (movement.py)."""
        return self._backend.rw(0x9618) | (self._backend.rw(0x961A) << 16)

    @lateral.setter
    def lateral(self, v: int) -> None:
        v &= 0xFFFFFFFF
        self._backend.ww(0x9618, v & 0xFFFF)
        self._backend.ww(0x961A, (v >> 16) & 0xFFFF)
