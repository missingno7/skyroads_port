"""Play SKYROADS gameplay VM-FREE — the standalone native-port entry point.

Every tick is pure recovered Python -- no VM, no interpreter, no original
binary running -- for the ENTIRE flow: intro splash, level-select menu, and
gameplay at any level, all built from the shipped game files alone
(``skyroads.native.boot.native_boot_image`` unpacks the EXE, primes DGROUP
and the display-list buffers, and loads every asset bank natively; see
``docs/skyroads/run_status.md``'s 2026-07-13 entries for the full recovery
trail). Two things are an honest, documented stand-in rather than a VM
recovery: the LOGO.PCX splash (SKYROADS.EXE itself never opens that file --
it must come from an external loader in the original distribution) and the
level-select highlight cursor (the ROM's own scroll-to-level-index mapping
wasn't pinned down, so a plain drawn box stands in for it). Everything else
-- gameplay sim, rendering, SFX, music, menu background/palette, level
loading -- is VM-verified byte-exact where the docs say so.

Usage:
    # THE FULL COLD START (milestone 2): splash -> native level-select menu
    # -> gameplay, zero VM at any point. Arrows navigate the menu grid,
    # enter/space confirms; in gameplay, arrows steer/accelerate, space jumps:
    python scripts/play_native.py --boot

    # Skip straight to one level's native gameplay window (no menu/intro),
    # fully VM-free:
    python scripts/play_native.py --level 2 --cold-native

    # Same, but seeded from a captured baseline snapshot instead of building
    # the boot image from files (legacy path, still useful for isolating a
    # render/sim bug from a boot-image bug):
    python scripts/play_native.py --level 2

    # Agent/CI: the headless sim run of a level (no window; plays holding
    # accelerate and prints the outcome):
    python scripts/play_native.py --level 2 --headless

    # Verification workflows (headless by nature, driven from a demo):
    # cold-start a level (ship_pos=0, zero player input) and play it natively
    # to a transition:
    python scripts/play_native.py --demo artifacts/demos/demo_e2e_20260710_132930 --cold

    # Same, but ALSO reset the real VM to the identical cold state and confirm
    # it independently reaches the same level-complete conclusion -- the
    # strongest form of the proof:
    python scripts/play_native.py --demo artifacts/demos/demo_e2e_20260710_132930 --cold-verify

    # Play a recorded demo's OWN input, purely natively, once seeded:
    python scripts/play_native.py --demo artifacts/demos/demo_e2e_20260710_132930

    # Same, but ALSO run the VM alongside and report any divergence (the
    # convergence proof, promoted from tests/test_native_loop_lockstep.py):
    python scripts/play_native.py --demo artifacts/demos/demo_e2e_20260710_132930 --verify

    # Keep running past the demo's recorded input (idle input) to see how far
    # the native driver gets on its own, transitions and all:
    python scripts/play_native.py --demo artifacts/demos/demo_e2e_20260710_132930 --extra-ticks 2000
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dos_re"))
# ROOT inserted LAST (so it wins position 0): this repo's own top-level
# submodules (pynuked_opl3/) must resolve ahead of dos_re's nested copy of
# the same package -- see skyroads/audio/opl3_synth.py.
sys.path.insert(0, str(ROOT))

import scripts.play as sp  # noqa: E402
from dos_re import player  # noqa: E402
from dos_re.cpu import CPU8086, HaltExecution  # noqa: E402
from dos_re.dos import ConsoleInputWouldBlock  # noqa: E402
from dos_re.input_demo import InputDemoPlayback  # noqa: E402
from dos_re.player import _use_real_console_input  # noqa: E402

from skyroads.bridge.dgroup_view import GameView  # noqa: E402
from skyroads.native.gaps import SkyroadsGap  # noqa: E402
from skyroads.native.loop import GameplayScratch, NativeGameplayDriver, apply_level_init  # noqa: E402
from skyroads.native.state import NativeGameState  # noqa: E402
from skyroads.recovered.dynamics import JumpScratch  # noqa: E402
from skyroads.recovered.player import RespawnState, level_gravity  # noqa: E402

LOOP_TOP_IP = 0x2324  # the gameplay sub-step's classification entry (1010:2324)
INPUT_OFFS = [0x95F4, 0x547A, 0x9330, 0x1600, 0x95F6] + list(range(0x0BD0, 0x0BE0))

#: The game's real frame + music timing, MEASURED from the VM (2026-07-13,
#: demo_cold_20260711_201855, steady gameplay): the PIT channel-0 reload is
#: 6628, so the timer runs at 1193182/6628 = 180 Hz, and the music ISR
#: `1010:5A55` fires ONCE per timer IRQ -- 6 IRQs per displayed frame (the
#: demo manifest's `timer_irqs_per_frame`), i.e. the game runs at 180/6 = 30
#: fps and the OPL sequencer ticks at 180 Hz. So the viewer must render at 30
#: fps and call `Engine.run_tick()` 6x per frame (was 35 fps x 2 = 70 Hz --
#: 2.6x too slow, the "music plays slower than it should" the user heard).
GAME_FPS = 30
MUSIC_TICKS_PER_FRAME = 6

#: Palette fade-to-black transitions (level start / death / finish). RECOVERED
#: from the real routine, not guessed: `1010:4331` cross-fades two 256-entry
#: RGB palette buffers over a caller-given duration; its hot inner loop
#: (`1010:43A9`) is `skyroads.recovered.palette_fade.blend_byte` -- ALREADY
#: VERIFIED (34,439 real hook calls, zero divergence, see symbol_ledger.md) --
#: `out = b + trunc((a-b)*percent/100)`, i.e. `out == b` at `percent=0` and
#: `out == a` at `percent=100`. A first pass here used an UN-VERIFIED guessed
#: "subtract ~2 units/frame toward black" scheme instead of this already-
#: recovered primitive -- wrong to ship without checking it against the
#: oracle, caught and replaced 2026-07-13.
#:
#: Traced 14 real `4331` calls (demo_cold_20260711_201855, corrected for the
#: `enter` prologue -- bp is the CALLER's frame until `4331+4`, a mistake this
#: same trace caught on the first pass). Every full-palette (`count==256`)
#: transition uses `duration=36` and, confirmed by dereferencing both struct
#: pointers, one side is ALWAYS a genuine all-zero (black) 256x3 buffer and
#: the other the real on-screen scene palette -- i.e. a real fade-to/from-
#: black, not an assumption. Level start / death / finish is a PAIR: fade the
#: old scene OUT to black, then fade the new scene IN from black. Timing
#: measured directly (entry-CS:IP to return-CS:IP frame count, 7 independent
#: instances, zero variance): a `duration=36` call always takes exactly
#: **30 real displayed frames = 1.0s @ 30fps**, so `duration` ticks aren't
#: needed -- the wall-clock length IS the verified fact.
FADE_FRAMES = 30
BLACK_PALETTE = [(0, 0, 0)] * 256

#: A crash/finish/timeout transition holds for one fade-out + fade-in pair
#: (2 x FADE_FRAMES) before the new state is playable -- see TransitionFader.
#: VM-measured on demo_skyroads_crash_20260713_085908: native_gameplay_substep
#: already plays the settle-window RAMP natively (grounded 0->0x2A over ~34
#: frames, af2c animating -- should_run_gameplay's own SETTLE_WINDOW_MAX
#: logic), so no extra work is needed there; TransitionFader covers the gap
#: AFTER that: the real VM doesn't reset until ~60 more frames later (grounded
#: plateaus at frame 45, the VM's own game_state 3->0 reset lands at frame
#: 105) -- which is exactly one fade-out + fade-in pair.


def fade_palette(start, end, percent: int):
    """The VM's real cross-fade (`1010:4331`/`43A9`): blend ``start`` (256
    RGB tuples, the palette at ``percent=0``) toward ``end`` (at
    ``percent=100``), via the verified ``blend_byte`` per channel."""
    from skyroads.recovered.palette_fade import blend_byte
    percent = max(0, min(100, percent))
    return [(blend_byte(e[0], s[0], percent), blend_byte(e[1], s[1], percent),
             blend_byte(e[2], s[2], percent)) for s, e in zip(start, end)]


class TransitionFader:
    """Drives the VM's real fade-out-to-black / fade-in-from-black pair
    around a scene switch (level start, crash/death/finish respawn).

    VM-verified (demo_cold_20260711_201855, frames 278-316): gameplay stays
    FROZEN through the ENTIRE fade-in, not just the fade-out -- af2c/ship_pos/
    gravity are all still 0 at frame 314 and only go live at 316, exactly
    FADE_FRAMES after the fade-in's own 4331 call starts. So a caller must
    hold ticking (and any state switch) frozen for the whole ``active``
    window, not just resume the instant the screen goes black.

    Call :meth:`tick` once per rendered frame; :meth:`switch` fires exactly
    once, at the black-screen midpoint, to perform the actual state change.
    """

    def __init__(self, switch):
        self._switch = switch
        self.remaining = 0

    @property
    def active(self) -> bool:
        return self.remaining > 0

    def start_out(self) -> None:
        """Fade the CURRENT scene out to black, switch, fade the NEW scene
        in -- the crash/death/finish respawn sequence."""
        self.remaining = 2 * FADE_FRAMES

    def start_in(self) -> None:
        """Fade IN only -- initial level entry, where there is no prior
        on-screen scene to fade out of (the caller applies the new state
        BEFORE calling this; no mid-fade ``switch()`` fires)."""
        self.remaining = FADE_FRAMES

    def tick(self) -> None:
        if self.remaining <= 0:
            return
        self.remaining -= 1
        if self.remaining == FADE_FRAMES:          # the exact black-screen instant
            self._switch()

    def palette(self, current):
        """The palette to render THIS frame, or ``None`` if no fade is
        active (render ``current`` unmodified)."""
        if self.remaining > FADE_FRAMES:
            percent = round(100 * (2 * FADE_FRAMES - self.remaining) / FADE_FRAMES)
            return fade_palette(current, BLACK_PALETTE, percent)
        if 0 < self.remaining <= FADE_FRAMES:
            percent = round(100 * (FADE_FRAMES - self.remaining) / FADE_FRAMES)
            return fade_palette(BLACK_PALETTE, current, percent)
        return None


def choose_song(prev_di: "int | None" = None) -> "tuple[int, int]":
    """Pick a gameplay MUZAX song the way the real game does (a random track
    in 1..9, never the intro track 0, no immediate repeat -- see
    ``skyroads.native.world_load.pick_gameplay_song``). Returns
    ``(song_index, di)``; thread ``di`` back in as ``prev_di`` on the next
    level load."""
    from skyroads.native.world_load import pick_gameplay_song
    return pick_gameplay_song(random.randrange(0x10000), prev_di)


def _bpw(m, ss, bp, o):
    return m.rw(ss, (bp - o) & 0xFFFF)


def boot_and_seed(root: Path, demo_path: Path):
    """Drive the ORIGINAL game (via the VM) to the first real gameplay
    sub-step, then return (NativeGameState, GameplayScratch, jump_level_gate,
    a `next_input()` generator yielding the demo's remaining recorded input
    frame by frame, and the live `rt`/`args`/`frontend`/`pb` for --verify)."""
    frontend = sp.SkyroadsFrontend(root)
    args = player.build_arg_parser(frontend).parse_args(
        ["--play-demo", str(demo_path), "--headless"])
    pb = InputDemoPlayback.load(str(demo_path))
    frontend.apply_demo_metadata(args, pb.manifest.get("metadata", {}))
    rt = (frontend.create_runtime(args) if pb.is_cold_start
          else frontend.load_snapshot_runtime(args, pb.snapshot_path()))
    args.install_replacements = False  # pure ASM oracle while seeding/boot-driving
    frontend.apply_hook_mode(rt, args)
    _use_real_console_input(rt)

    seed = {}

    def _try_seed(cpu):
        if seed:
            return
        m = cpu.mem
        ds = cpu.s.ds
        if m.rw(ds, 0x456E) == 0:
            seed["state"] = NativeGameState(bytearray(m.data[(ds << 4):(ds << 4) + 0x10000]))
            s = cpu.s
            seed["scratch"] = GameplayScratch(
                jump=JumpScratch(_bpw(m, s.ss, s.bp, 8), _bpw(m, s.ss, s.bp, 10),
                                 _bpw(m, s.ss, s.bp, 6)),
                bp12=_bpw(m, s.ss, s.bp, 12), bp14=_bpw(m, s.ss, s.bp, 14),
                bp24=_bpw(m, s.ss, s.bp, 24), tgt_af2c=_bpw(m, s.ss, s.bp, 28))
            seed["jump_level_gate"] = m.rw(ds, 0x4562)
            seed["frame"] = frame_box[0]

    inputs = []
    frame_box = [0]

    def _record_input(cpu):
        if not seed:
            return
        m = cpu.mem
        ds = cpu.s.ds
        inputs.append((
            m.rw(ds, 0x95F4), m.rw(ds, 0x547A), m.rw(ds, 0x9330),
            bytes(m.rb(ds, o) for o in range(0x0BD0, 0x0BE0)),
            m.rw(ds, 0x1600),
        ))

    orig = CPU8086.step

    def patched(self):
        if self.s.cs == 0x1010 and self.s.ip == LOOP_TOP_IP:
            _try_seed(self)
            _record_input(self)
        return orig(self)

    CPU8086.step = patched
    try:
        frame = 0
        while not pb.finished(frame):
            frame_box[0] = frame
            pb.apply_to_runtime(frame, rt, deliver=lambda r, sc: frontend.deliver_input(r, sc))
            try:
                frontend.advance_frame(rt, args, frame)
            except ConsoleInputWouldBlock:
                pass
            except HaltExecution:
                break
            frame += 1
    finally:
        CPU8086.step = orig

    if not seed:
        raise RuntimeError("never reached a game_state==0 gameplay sub-step in this demo")
    return seed, inputs, (rt, args, frontend, pb)


def run_offline(state, scratch, jump_level_gate, inputs, extra_ticks: int) -> None:
    """Pure native replay -- no VM from here on. Prints a summary."""
    view = GameView(state)
    driver = NativeGameplayDriver(view, jump_level_gate, scratch)
    for steer, jump, speed, keys, tick in inputs:
        view.steer = steer
        view.jump = jump
        view.speed = speed
        for i, kb in enumerate(keys):
            view._backend.wb(0x0BD0 + i, kb)
        view.elapsed_ticks = tick
        driver.tick()
    for _ in range(extra_ticks):
        driver.tick()  # idle input: whatever the view already holds
    print(f"[native] ticks={driver.ticks} transitions={driver.transitions} "
          f"final game_state={view.game_state} ship_pos={view.ship_pos:#x}")


def run_cold(state, jump_level_gate, max_ticks: int = 2000) -> None:
    """THE MILESTONE: reset to a genuine COLD level start
    (:func:`~skyroads.native.loop.apply_level_init` -- ``ship_pos = 0``, the
    fixed :class:`~skyroads.recovered.player.RespawnState` fields, the
    derived per-level gravity) over real level geometry, then run the native
    driver with ZERO player input -- no steer, no jump, no recorded demo --
    until it reaches a genuine level-complete transition
    (``ship_pos >= LEVEL_END``, ``game_state -> 2``) on its own.

    This works because forward motion is AUTOMATIC in SkyRoads (driven by the
    classification's ``dispatch_menu_action`` call each sub-step, not by
    player input -- see ``skyroads.recovered.classify``'s docstring); a
    completely idle input still drives the ship the length of the level.
    100% native from the first tick: no VM, no recorded input, no
    original binary -- only the level's static geometry tables were ever
    read from a VM capture.
    """
    view = GameView(state)
    scratch = apply_level_init(view, jump_level_gate)
    print(f"[cold] reset to a genuine level start: ship_pos={view.ship_pos:#x} "
          f"af2c={view.af2c:#06x} game_state={view.game_state} gravity={view.gravity:#06x}")
    driver = NativeGameplayDriver(view, jump_level_gate, scratch)
    for i in range(max_ticks):
        outcome = driver.tick()
        if outcome.transitioned:
            completed = "game_state=2" in outcome.reason
            print(f"[cold] tick {i}: transition -> {outcome.reason}")
            if completed:
                print(f"\n*** COLD RUN COMPLETE: level finished in {i + 1} ticks, "
                      f"100% native, zero player input, zero VM after the geometry seed ***")
            else:
                print(f"\n[cold] stopped on a non-level-complete transition after {i + 1} ticks "
                      f"(death/crash/timeout, not a level finish)")
            return
    print(f"\n[cold] did not reach a transition within {max_ticks} ticks "
          f"(ship_pos={view.ship_pos:#x})")


def run_level(root: Path, level: int, baseline_dir: Path, max_ticks: int = 4000) -> None:
    """Play LEVEL by INDEX, VM-FREE -- no demo, no per-run snapshot. Loads the
    level's geometry straight from ``ROADS.LZS`` with
    :func:`skyroads.native.level_load.native_level_load` (verified byte-exact vs
    the VM), over a level-INDEPENDENT constants baseline (the sim's clip/shape
    tables are computed at startup, so a fresh state lacks them -- see
    run_status.md; computing them from scratch is the cold-boot milestone). Then
    :func:`apply_level_init` for the player state, and runs the native driver with
    the accelerate key held (forward motion is input-driven: `[0x9330]` speed
    comes from the up key). The ship advances +75/tick and crashes at the first
    obstacle absent steer/jump -- to COMPLETE a level, feed its recorded input.
    """
    from skyroads.native.level_load import native_level_load
    from skyroads.native.state import NativeGameState, DATA_SEG

    mem_bin = baseline_dir / "memory_1mb.bin"
    if not mem_bin.exists():
        raise SystemExit(
            f"constants baseline not found: {mem_bin}\n"
            "Pass --baseline <snapshot_dir> (a captured DGROUP providing the "
            "level-independent startup constants). Any gameplay snapshot works; "
            "the level geometry in it is overwritten by native_level_load.")
    base = DATA_SEG << 4
    dg = mem_bin.read_bytes()[base:base + 0x10000]
    state = NativeGameState(bytearray(dg))

    decoded = native_level_load(state, level, game_root=str(root / "assets"))
    gate = state.rw(0x4562)
    print("[level] headless native SIM run (--headless): plays the level's physics/collision "
          "holding accelerate and prints the outcome. To PLAY the game, drop --headless "
          "(the window is the default).")
    print(f"[level] loaded level {level} from ROADS.LZS VM-FREE: gravity/gate={gate:#06x} "
          f"fuel={decoded.fuel} oxygen={decoded.oxygen} road={len(decoded.road)}B")

    view = GameView(state)
    scratch = apply_level_init(view, gate)
    print(f"[level] cold start: ship_pos={view.ship_pos:#x} game_state={view.game_state} "
          f"gravity={view.gravity:#06x} -- holding ACCELERATE (no steer/jump)")
    driver = NativeGameplayDriver(view, gate, scratch)
    for i in range(max_ticks):
        view.speed = 1  # hold the accelerate key
        outcome = driver.tick()
        if outcome.transitioned:
            if "game_state=2" in outcome.reason:
                print(f"\n*** LEVEL {level} COMPLETE in {i + 1} ticks -- 100% native, "
                      f"loaded by index, zero VM ***")
            else:
                print(f"[level] tick {i}: {outcome.reason} (ship_pos={view.ship_pos:#x}) "
                      f"-- expected without steer/jump; feed recorded input to finish")
            return
    print(f"[level] no transition in {max_ticks} ticks (ship_pos={view.ship_pos:#x})")


def run_window(root: Path, level: int, baseline_dir: Path, max_frames: int = 0,
              cold_native: bool = False) -> None:
    """THE WINDOW: play LEVEL interactively in a real window, 100% native.

    Per frame: pygame keys -> the sim's speed/steer/jump axes; one native sim
    tick (`NativeGameplayDriver`, which auto-respawns at crash/complete
    boundaries); one native render (`render_native_frame` -- the pipeline
    verified byte-exact against the VM on both captured frames); present the
    composed viewport through the level's DAC.

    ``cold_native=True`` (milestone 2): the ENTIRE 1 MB image -- program,
    DGROUP, display-list buffers, palette -- is built from the game files
    alone (`skyroads.native.boot.native_boot_image`), no VM, no snapshot.
    Otherwise a captured baseline snapshot supplies those (the level
    geometry itself is always loaded VM-free from ROADS.LZS by index).
    """
    import json as _json
    import pygame

    from skyroads.native.boot import (DASHBOARD_BEZEL_OVERLAP, SEG_DASHBRD,
                                      paint_dashboard)
    from skyroads.native.frame import render_native_frame
    from skyroads.native.hud import update_hud, update_progress_bar, draw_grav_meter
    from skyroads.native.image import NativeGameImage
    from skyroads.native.level_load import native_level_load
    from skyroads.native.state import DATA_SEG, NativeGameState
    from dos_re.display import Display

    if cold_native:
        from skyroads.native.boot import (apply_gameplay_segment_init,
                                          native_boot_dac, native_boot_image)
        img = NativeGameImage(native_boot_image(root / "assets"))
        palette = native_boot_dac(root / "assets")
        dg_base = DATA_SEG << 4
        st = NativeGameState(bytearray(img.data[dg_base:dg_base + 0x10000]))
        apply_gameplay_segment_init(st.data)
        print("[window] cold-native boot: program + DGROUP + display lists "
              "built entirely from game files (no VM, no snapshot)")
    else:
        mem_bin = baseline_dir / "memory_1mb.bin"
        state_json = baseline_dir / "state.json"
        if not mem_bin.exists() or not state_json.exists():
            raise SystemExit(f"--window needs a full baseline snapshot (memory_1mb.bin + "
                             f"state.json) at {baseline_dir}")
        img = NativeGameImage(bytearray(mem_bin.read_bytes()))
        palette = [tuple(e) for e in _json.loads(state_json.read_text())["dos"]["vga_palette"]]
        dg_base = DATA_SEG << 4
        st = NativeGameState(bytearray(img.data[dg_base:dg_base + 0x10000]))
    # The frame is presented straight from the image's VGA plane (0xA0000):
    # `34AE(ax=1)` (the in-frame present pass, now native) keeps rows 0..137
    # (the road band + ship) live, and rows 138..199 keep the cockpit art.
    # The GAUGES stay at their captured values until the HUD gauge renderer
    # (4526/44BE glyphs + dial/bar draws + the 4563 rect flush) is ported.

    # VM-free level geometry over the DGROUP.
    decoded = native_level_load(st, level, game_root=str(root / "assets"))

    # VM-free per-level WORLD assets: background bank + palette + song.
    from skyroads.native.world_load import (
        CMAP_DAC_BASE, expand6, load_world_assets, native_song_load)
    world = load_world_assets(level, game_root=str(root / "assets"))
    song_index, _ = choose_song()                # random gameplay track (1..9)
    song = native_song_load(st, song_index, game_root=str(root / "assets"))
    img.data[dg_base:dg_base + 0x10000] = st.data
    bg_seg = img.rw(DATA_SEG, 0x5170)            # the background bank segment
    img.data[(bg_seg << 4):(bg_seg << 4) + len(world.background)] = world.background
    # Compose the gameplay DAC: ROADS' 72 level colours -> 0..71, the world
    # CMAP's 114 -> 142..255; everything else (cockpit/ship) is level-fixed.
    for i in range(72):
        palette[i] = tuple(expand6(decoded.palette[3 * i + k]) for k in range(3))
    for i in range(len(world.cmap) // 3):
        palette[CMAP_DAC_BASE + i] = tuple(
            expand6(world.cmap[3 * i + k]) for k in range(3))
    gate = st.rw(0x4562)
    print(f"[window] level {level} loaded VM-free (road={len(decoded.road)}B, "
          f"gate={gate:#06x}); world {level // 3} background+palette + song "
          f"{song.index} (random 1..9) ({len(song.data)}B, {song.n_instruments} "
          f"patches) -- all native")

    view = GameView(img, base=dg_base)
    scratch = apply_level_init(view, gate)
    driver = NativeGameplayDriver(view, gate, scratch, auto_respawn=False)
    fader = TransitionFader(switch=driver.respawn)

    pygame.init()
    # native music: the recovered OPL sequencer -> a REAL Nuked-OPL3 chip
    # (pynuked_opl3, this repo's own top-level submodule) -- raw register
    # writes straight through, no semantic-event re-synthesis in between.
    music_engine = music_synth = None
    try:
        from skyroads.audio.opl3_synth import NativeOplSynth
        from skyroads.recovered.music import Engine as _MusicEngine
        music_synth = NativeOplSynth(pygame, present_hz=GAME_FPS)
        if not music_synth.available:
            music_synth = None
        else:
            music_engine = _MusicEngine(lambda o: img.rb(DATA_SEG, o),
                                        lambda o: img.rw(DATA_SEG, o))
            for reg, val in music_engine.reset_opl():     # one-time OPL/percussion init
                music_synth.write(reg, val)
            for off, b in music_engine.ovl.items():
                img.wb(DATA_SEG, off, b)
            print(f"[window] music: recovered sequencer -> {music_synth.backend_label}")
    except Exception as e:                       # noqa: BLE001 -- no audio device etc.
        print(f"[window] music disabled ({e})")
    # native SFX: the SFX.SND PCM bank, played on the sim's 03C2 trigger points
    # (the synth inits the mixer with its preferred rate; we resample to match)
    try:
        import numpy as _np
        from skyroads.native.sfx import load_sfx_bank
        if pygame.mixer.get_init() is None:
            pygame.mixer.init()
        mix_rate, _sz, mix_ch = pygame.mixer.get_init()
        sounds = []
        for eff in load_sfx_bank(root / "assets" / "SFX.SND"):
            u8 = _np.frombuffer(eff.pcm, dtype=_np.uint8).astype(_np.float32)
            mono = (u8 - 128.0) / 128.0
            n_out = max(1, int(len(mono) * mix_rate / eff.rate))
            res = _np.interp(_np.linspace(0, len(mono) - 1, n_out),
                             _np.arange(len(mono)), mono)
            s16 = (res * 32000).astype(_np.int16)
            if mix_ch > 1:
                s16 = _np.repeat(s16[:, None], mix_ch, axis=1)
            sounds.append(pygame.sndarray.make_sound(_np.ascontiguousarray(s16)))
        driver.on_sfx = lambda i: sounds[i].play() if i < len(sounds) else None
        print(f"[window] SFX: {len(sounds)} PCM effects from SFX.SND (native)")
    except Exception as e:                       # noqa: BLE001 -- no audio device etc.
        print(f"[window] SFX disabled ({e})")

    disp = Display((960, 720), title=f"SkyRoads native -- level {level}")
    disp.par = 1.2                               # 320x200 shown at 4:3 (non-square DOS pixels)
    clock = pygame.time.Clock()
    frames = 0
    running = True
    # Paint the FULL dashboard once. Rows 138..199 (which hold the HUD gauges)
    # are never touched by the road render, so from here only the bezel strip
    # (rows 129..137, which the road render rewrites) is re-overlaid per frame
    # -- the gauges are left to the byte-exact delta updater to fill AND unfill.
    paint_dashboard(img.data, SEG_DASHBRD)
    fader.start_in()                              # frozen fade IN from black on level start
    while running and (max_frames <= 0 or frames < max_frames):
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT or (
                    ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE):
                running = False
        keys = pygame.key.get_pressed()
        view.speed = (1 if keys[pygame.K_UP] else 0) - (1 if keys[pygame.K_DOWN] else 0)
        view.steer = ((1 if keys[pygame.K_RIGHT] else 0)
                      - (1 if keys[pygame.K_LEFT] else 0)) & 0xFFFF
        view.jump = 1 if keys[pygame.K_SPACE] else 0
        view.elapsed_ticks = (view.elapsed_ticks + 2) & 0xFFFF   # [1600] frame-tick counter (wobble/SFX debounce)
        if music_engine is not None:
            try:
                for _ in range(MUSIC_TICKS_PER_FRAME):     # 180 Hz = 6 ISR ticks / 30fps frame (VM-measured)
                    writes = music_engine.run_tick()
                    for off, b in music_engine.ovl.items():
                        img.wb(DATA_SEG, off, b)         # commit the tick's state
                    for reg, val in writes:
                        music_synth.write(reg, val)
                music_synth.pump()
            except Exception as e:                       # noqa: BLE001
                print(f"[window] music stopped ({e})")
                music_engine = None

        # Gameplay is FROZEN for the whole fade window (VM-verified -- see
        # TransitionFader), not just while the screen is black.
        if fader.active:
            fader.tick()
        else:
            outcome = driver.tick()
            if outcome.transitioned:
                print(f"[window] {outcome.reason} (kind={outcome.kind!r}) -- fading out")
                fader.start_out()
        # Full road re-render EVERY frame (rebuild=True), not just the first.
        # The VM's delta/skip render paths (34AE: nothing on delta==0, the
        # column loop on 1<=delta<8) assume a persistent, always-correct road
        # band maintained incrementally; the native port's own accumulated
        # offscreen/display-list state drifts under those paths and leaves
        # STALE TERRAIN at the left/right screen edges (the "edge ghosting").
        # A full render each frame (bg->offscreen + full tile raster) sidesteps
        # that entirely and is cheap here (~6 ms/frame). See run_status.md's
        # 2026-07-13 edge-ghosting entry; the delta-path bug itself is a known,
        # documented open item, not silently hidden.
        render_native_frame(img, DATA_SEG, offscreen=1, rebuild=True)
        paint_dashboard(img.data, SEG_DASHBRD, byte_count=DASHBOARD_BEZEL_OVERLAP)
        update_hud(img, DATA_SEG, view.ship_pos)
        update_progress_bar(img, DATA_SEG)
        draw_grav_meter(img, DATA_SEG)

        frame = img.data[0xA0000:0xA0000 + 64000]   # the live VGA plane
        fpal = fader.palette(palette) or palette            # fade-to-black transitions
        try:
            import numpy as _np
        except ImportError:
            raise SystemExit("--window needs numpy (pip install numpy pygame)")
        # numpy palette LUT: the 320x200 indexed plane -> RGB in one gather
        # (pal[idx]), ~20x the per-pixel Python loop and byte-identical -- the
        # dos_re render_frame numpy fast-path policy applied to the native player.
        _pal = _np.asarray(fpal, dtype=_np.uint8)                 # (256, 3)
        _idx = _np.frombuffer(bytes(frame), dtype=_np.uint8)      # (64000,)
        arr = _pal[_idx].reshape(200, 320, 3)
        disp.draw_game(arr)
        disp.flip()
        clock.tick(GAME_FPS)
        frames += 1
    pygame.quit()
    print(f"[window] closed after {frames} frames")


def run_cold_boot(root: Path, window_frames: int = 0) -> None:
    """MILESTONE 2, the full cold start: open on the native LEVEL-SELECT
    screen (GOMENU.LZS, VM-free), let the player pick a level, then hand off
    into real gameplay -- zero VM, zero snapshot, at any point.

    GOMENU's background + its own 212-colour CMAP are VM-verified byte-exact
    (212/212 palette entries, 63,970/64,000 background pixels -- the residual
    is the small 5x6 selection-icon PICT record this native version doesn't
    draw yet). The background ALREADY contains the full menu -- 10 world
    names in a 2x5 grid, each with its 3 "Road N" lines pre-rendered -- so
    the level to play is picked by highlighting one of those 30 lines
    directly (row/column geometry measured off the decoded image's own green
    text pixels, not a ROM-recovered layout table).

    What is NOT recovered: the ROM's own selection-cursor draw and the exact
    `scroll_pos`-to-level-index mapping (dispatch_menu_action's scroll
    mechanics are recovered and ASM-matched, but two different real captures
    disagreed on what level index a given scroll_pos selects -- see
    run_status.md -- so this menu does NOT reuse that indirection). Left/
    right/up/down here directly step a level index 0..29, highlighted with a
    plain drawn rectangle -- a UI affordance standing in for the ROM's own
    cursor sprite, not a recovered asset.
    """
    import pygame
    import numpy as _np

    from skyroads.native.boot import (DASHBOARD_BEZEL_OVERLAP, SEG_DASHBRD,
                                      apply_gameplay_segment_init,
                                      load_pict, native_boot_dac,
                                      native_boot_image, paint_dashboard,
                                      parse_lzs_container)
    from skyroads.native.frame import render_native_frame
    from skyroads.native.hud import update_hud, update_progress_bar, draw_grav_meter
    from skyroads.native.image import NativeGameImage
    from skyroads.native.level_load import native_level_load, read_game_file
    from skyroads.native.level_select import move_selection
    from skyroads.native.loop import NativeGameplayDriver, apply_level_init
    from skyroads.native.state import DATA_SEG, NativeGameState
    from skyroads.native.world_load import (
        CMAP_DAC_BASE, expand6, load_world_assets, native_song_load)
    from dos_re.display import Display

    img = NativeGameImage(native_boot_image(root / "assets"))
    dg_base = DATA_SEG << 4

    # the menu screen: GOMENU's background at 7176:0000 (VM-verified) + its
    # own 212-colour palette occupying DAC 0..211 directly (VM-verified).
    gomenu = read_game_file(root / "assets", "GOMENU.LZS")
    cmap, _aux, pict_at, _dest, mh, mw = parse_lzs_container(gomenu)
    _, menu_pixels = load_pict(gomenu, pict_at)
    menu_seg = 0x7176
    img.data[(menu_seg << 4):(menu_seg << 4) + len(menu_pixels)] = menu_pixels
    HIGHLIGHT_IDX = 255                     # unused by GOMENU's 212 colours
    menu_palette = [(0, 0, 0)] * 256
    for i in range(len(cmap) // 3):
        menu_palette[i] = tuple(expand6(cmap[3 * i + k]) for k in range(3))
    menu_palette[HIGHLIGHT_IDX] = (255, 255, 0)

    # 30 levels in a 2-column x 5-row x 3-road grid (measured off the
    # decoded background's own green "Road N" text pixels -- see the
    # function docstring's caveat: not a recovered ROM layout table).
    WORLD_ROW_Y0 = (12, 51, 90, 129, 168)
    ROAD_SUB_Y = ((0, 8), (10, 17), (19, 27))
    COL_X = ((6, 112), (166, 272))

    def level_box(level: int) -> "tuple[int, int, int, int]":
        world, road = level // 3, level % 3
        col, row = (0, world) if world < 5 else (1, world - 5)
        y0, y1 = WORLD_ROW_Y0[row] + ROAD_SUB_Y[road][0], WORLD_ROW_Y0[row] + ROAD_SUB_Y[road][1]
        x0, x1 = COL_X[col]
        return x0, y0, x1, y1

    pygame.init()
    disp = Display((960, 720), title="SkyRoads native -- cold boot")
    disp.par = 1.2                               # 320x200 shown at 4:3 (non-square DOS pixels)
    clock = pygame.time.Clock()

    def to_rgb(frame: bytes, palette) -> bytes:
        # numpy palette LUT (idx -> RGB in one gather), byte-identical to the
        # per-pixel loop and ~20x faster -- see the gameplay render path.
        pal = _np.asarray(palette, dtype=_np.uint8)
        idx = _np.frombuffer(bytes(frame), dtype=_np.uint8)
        return pal[idx].reshape(200, 320, 3).tobytes()

    def present(rgb_bytes: bytes) -> None:
        arr = _np.frombuffer(rgb_bytes, dtype=_np.uint8).reshape(200, 320, 3)
        disp.draw_game(arr)
        disp.flip()

    # ---- INTRO -- the VM-verified cold-start opening (demo_cold_20260713_
    # 213510, run_status.md round 20). The real game NEVER shows LOGO.PCX (a
    # publisher splash the EXE never opens; a stand-in previously shown here
    # and removed as unfaithful). The verified sequence is:
    #
    #   1. black screen;
    #   2. the 1010:4331 palette fade-in (the SAME verified 30-frame
    #      blend_byte fade as the gameplay transitions -- observed firing at
    #      the demo's f11) revealing the static INTRO.LZS checkerboard, whose
    #      palette is expand6(INTRO CMAP) (== the demo's stable DAC, 0/38
    #      mismatch);
    #   3. the ANIM.LZS ship animation painted ON TOP of that INTRO canvas.
    #      ANIM's 102-colour CMAP is a strict extension of INTRO's 38
    #      (identical prefix, ship colours at 38..101; == the demo's f110 DAC,
    #      0/102 mismatch), so the palette swap is invisible. The 221 tiles
    #      reveal at the VM-traced per-tick pace (skyroads/native/anim.py);
    #      the table-walk driver itself is a documented gap (output replayed).
    #
    # INTRO.SND (6024 Hz PCM, SB-DMA-rate-verified real game audio) plays
    # underneath.
    try:
        intro_lzs = read_game_file(root / "assets", "INTRO.LZS")
        icmap, _iaux, ipict_at, _idest, _imh, _imw = parse_lzs_container(intro_lzs)
        _ihdr, intro_pixels = load_pict(intro_lzs, ipict_at)
        intro_palette = [(0, 0, 0)] * 256
        for i in range(len(icmap) // 3):
            intro_palette[i] = tuple(expand6(icmap[3 * i + k]) for k in range(3))

        intro_sound = None
        try:
            snd = (root / "assets" / "INTRO.SND").read_bytes()
            mono = (_np.frombuffer(snd, dtype=_np.uint8).astype(_np.float32) - 128.0) / 128.0
            if pygame.mixer.get_init() is None:
                pygame.mixer.init()
            mix_rate, _sz, mix_ch = pygame.mixer.get_init()
            n_out = max(1, int(len(mono) * mix_rate / 6024))
            res = _np.interp(_np.linspace(0, len(mono) - 1, n_out), _np.arange(len(mono)), mono)
            s16 = (res * 32000).astype(_np.int16)
            if mix_ch > 1:
                s16 = _np.repeat(s16[:, None], mix_ch, axis=1)
            intro_sound = pygame.sndarray.make_sound(_np.ascontiguousarray(s16))
        except Exception as e:                       # noqa: BLE001
            print(f"[window] intro sound disabled ({e})")

        def wants_skip() -> bool:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    pygame.quit()
                    raise SystemExit
            keys = pygame.key.get_pressed()
            return bool(keys[pygame.K_ESCAPE] or keys[pygame.K_RETURN] or keys[pygame.K_SPACE])

        played = False
        intro_frames = 0
        # 1+2. black -> the verified 4331 fade-in of the INTRO.LZS checkerboard.
        intro_rgb_frame = bytes(intro_pixels[:64000])
        for f in range(FADE_FRAMES):
            if wants_skip():
                break
            if intro_sound is not None and not played:
                intro_sound.play()
                played = True
            percent = round(100 * (f + 1) / FADE_FRAMES)
            present(to_rgb(intro_rgb_frame,
                           fade_palette(BLACK_PALETTE, intro_palette, percent)))
            clock.tick(GAME_FPS)
            intro_frames += 1
            if window_frames and intro_frames >= window_frames:
                print(f"[window] --window-frames reached during the intro fade ({intro_frames})")
                break

        # 3. the ANIM ship animation over the INTRO canvas (palette is a strict
        # extension of the intro's -- no visible pop at the swap).
        from skyroads.native.anim import iter_reveal_counts, load_anim, paint_tile
        anim_cmap, anim_tiles = load_anim(root / "assets" / "ANIM.LZS")
        anim_palette = [(0, 0, 0)] * 256
        for i in range(len(anim_cmap) // 3):
            anim_palette[i] = tuple(expand6(anim_cmap[3 * i + k]) for k in range(3))
        canvas = bytearray(intro_rgb_frame)          # the INTRO base, not black
        idx = 0
        for count in iter_reveal_counts(len(anim_tiles)):
            if wants_skip():
                break
            for _ in range(count):
                if idx >= len(anim_tiles):
                    break
                paint_tile(canvas, anim_tiles[idx])
                idx += 1
            present(to_rgb(bytes(canvas), anim_palette))
            clock.tick(35)               # the anim's VM-traced reveal pace
            intro_frames += 1
            if window_frames and intro_frames >= window_frames:
                print(f"[window] --window-frames reached during the anim ({intro_frames}); advancing to the menu")
                break
    except SystemExit:
        return
    except Exception as e:                            # noqa: BLE001
        print(f"[window] intro disabled ({e})")

    # ---- MENU: pick a level (native GOMENU background + a drawn highlight
    # box around the selected "Road N" line) ----
    # NOTE: the full faithful cold-boot menu flow (main menu / Controls / Help
    # before the level-select, with the VM's real per-screen palettes, screen
    # composition and cursor) is a pending byte-exact recovery -- see
    # run_status.md 2026-07-13 round 19. This section is the level-select only.
    def run_level_select(selected: int, *, fade_in: bool) -> "int | None":
        """The level-select loop. Returns the confirmed level, or ``None`` if
        the window was closed. ``fade_in``: re-entering from a finished level
        (see the caller) fades the grid IN from black over ``FADE_FRAMES`` --
        the VM-measured mirror of the fade-out below (demo_skyroads_
        20260713_234905: the level-select pixels appear at the black
        mid-point then the palette ramps up over ~30 frames, run_status.md
        round 23). The very first entry (from the intro) needs no fade -- the
        anim already leaves the screen visible."""
        menu_frames = 0
        prev_left = prev_right = prev_up = prev_down = prev_confirm = False

        def grid_frame(sel: int) -> bytearray:
            frame = bytearray(img.data[(menu_seg << 4):(menu_seg << 4) + 64000])
            x0, y0, x1, y1 = level_box(sel)
            for x in range(x0, x1):
                frame[y0 * 320 + x] = HIGHLIGHT_IDX
                frame[(y1 - 1) * 320 + x] = HIGHLIGHT_IDX
            for y in range(y0, y1):
                frame[y * 320 + x0] = HIGHLIGHT_IDX
                frame[y * 320 + (x1 - 1)] = HIGHLIGHT_IDX
            return frame

        if fade_in:
            still = bytes(grid_frame(selected))
            for f in range(FADE_FRAMES):
                percent = round(100 * (f + 1) / FADE_FRAMES)
                present(to_rgb(still, fade_palette(BLACK_PALETTE, menu_palette, percent)))
                clock.tick(GAME_FPS)

        running = True
        while running:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    pygame.quit()
                    print("[window] closed at menu")
                    return None
            keys = pygame.key.get_pressed()
            if keys[pygame.K_ESCAPE]:
                pygame.quit()
                print("[window] closed at menu")
                return None
            left, right = keys[pygame.K_LEFT], keys[pygame.K_RIGHT]
            up, down = keys[pygame.K_UP], keys[pygame.K_DOWN]
            confirm = keys[pygame.K_RETURN] or keys[pygame.K_SPACE]
            selected = move_selection(
                selected,
                up=up and not prev_up, down=down and not prev_down,
                left=left and not prev_left, right=right and not prev_right)
            prev_left, prev_right, prev_up, prev_down = left, right, up, down
            if confirm and not prev_confirm:
                running = False
            prev_confirm = confirm

            present(to_rgb(bytes(grid_frame(selected)), menu_palette))
            clock.tick(GAME_FPS)
            menu_frames += 1
            if window_frames and menu_frames >= window_frames:
                print(f"[window] --window-frames reached at the menu "
                      f"({menu_frames}); auto-selecting level {selected}")
                break
        return selected

    selected = run_level_select(0, fade_in=False)
    gp_frames_total = 0
    while selected is not None:
        print(f"[window] level {selected} (world {selected // 3}, road {selected % 3 + 1}) "
              f"selected -- loading VM-free")

        # Fade the menu OUT to black before loading gameplay -- VM-verified pair
        # (see TransitionFader): the real menu->gameplay handoff is a fade-out of
        # the CURRENT screen, then (invisibly, behind full black) the level loads,
        # then a fade-in of the new scene. This is the menu-screen half only (no
        # gameplay state to hold frozen yet); the gameplay fade-in follows once
        # the level below is loaded.
        menu_frame = bytearray(img.data[(menu_seg << 4):(menu_seg << 4) + 64000])
        x0, y0, x1, y1 = level_box(selected)
        for x in range(x0, x1):
            menu_frame[y0 * 320 + x] = HIGHLIGHT_IDX
            menu_frame[(y1 - 1) * 320 + x] = HIGHLIGHT_IDX
        for y in range(y0, y1):
            menu_frame[y * 320 + x0] = HIGHLIGHT_IDX
            menu_frame[y * 320 + (x1 - 1)] = HIGHLIGHT_IDX
        for f in range(FADE_FRAMES):
            percent = round(100 * (f + 1) / FADE_FRAMES)
            present(to_rgb(bytes(menu_frame), fade_palette(menu_palette, BLACK_PALETTE, percent)))
            clock.tick(GAME_FPS)

        # ---- GAMEPLAY: identical to run_window's --cold-native path ----
        st = NativeGameState(bytearray(img.data[dg_base:dg_base + 0x10000]))
        apply_gameplay_segment_init(st.data)
        palette = native_boot_dac(root / "assets")
        decoded = native_level_load(st, selected, game_root=str(root / "assets"))
        world = load_world_assets(selected, game_root=str(root / "assets"))
        song_index, _ = choose_song()                # random gameplay track (1..9)
        song = native_song_load(st, song_index, game_root=str(root / "assets"))
        img.data[dg_base:dg_base + 0x10000] = st.data
        bg_seg = img.rw(DATA_SEG, 0x5170)
        img.data[(bg_seg << 4):(bg_seg << 4) + len(world.background)] = world.background
        for i in range(72):
            palette[i] = tuple(expand6(decoded.palette[3 * i + k]) for k in range(3))
        for i in range(len(world.cmap) // 3):
            palette[CMAP_DAC_BASE + i] = tuple(expand6(world.cmap[3 * i + k]) for k in range(3))
        gate = st.rw(0x4562)
        print(f"[window] level {selected} loaded VM-free (road={len(decoded.road)}B); "
              f"world {selected // 3} + song {song.index} (random 1..9) -- all native")

        view = GameView(img, base=dg_base)
        scratch = apply_level_init(view, gate)
        driver = NativeGameplayDriver(view, gate, scratch, auto_respawn=False)
        # `_switch` fires at the fade's black mid-point. A DEATH (crash/fall/
        # timeout) respawns the SAME level in place (driver.respawn()); a
        # FINISH does NOT -- it returns to the level-select grid instead (see
        # skyroads/native/menus.py's MenuModel.level_ended, VM-verified
        # against demo_colde2e_full_20260713_144604: "finish -- return to the
        # level-select grid"; "a DEATH ... respawns from the start"). The
        # actual return-to-menu happens after this gameplay loop exits below.
        finished = [False]

        def _switch() -> None:
            if not finished[0]:
                driver.respawn()
        fader = TransitionFader(switch=_switch)

        music_engine = music_synth = None
        try:
            from skyroads.audio.opl3_synth import NativeOplSynth
            from skyroads.recovered.music import Engine as _MusicEngine
            music_synth = NativeOplSynth(pygame, present_hz=GAME_FPS)
            if not music_synth.available:
                music_synth = None
            else:
                music_engine = _MusicEngine(lambda o: img.rb(DATA_SEG, o),
                                            lambda o: img.rw(DATA_SEG, o))
                for reg, val in music_engine.reset_opl():     # one-time OPL/percussion init
                    music_synth.write(reg, val)
                for off, b in music_engine.ovl.items():
                    img.wb(DATA_SEG, off, b)
        except Exception as e:                       # noqa: BLE001
            print(f"[window] music disabled ({e})")
        try:
            import numpy as _np
            from skyroads.native.sfx import load_sfx_bank
            if pygame.mixer.get_init() is None:
                pygame.mixer.init()
            mix_rate, _sz, mix_ch = pygame.mixer.get_init()
            sounds = []
            for eff in load_sfx_bank(root / "assets" / "SFX.SND"):
                u8 = _np.frombuffer(eff.pcm, dtype=_np.uint8).astype(_np.float32)
                mono = (u8 - 128.0) / 128.0
                n_out = max(1, int(len(mono) * mix_rate / eff.rate))
                res = _np.interp(_np.linspace(0, len(mono) - 1, n_out),
                                 _np.arange(len(mono)), mono)
                s16 = (res * 32000).astype(_np.int16)
                if mix_ch > 1:
                    s16 = _np.repeat(s16[:, None], mix_ch, axis=1)
                sounds.append(pygame.sndarray.make_sound(_np.ascontiguousarray(s16)))
            driver.on_sfx = lambda i: sounds[i].play() if i < len(sounds) else None
        except Exception as e:                       # noqa: BLE001
            print(f"[window] SFX disabled ({e})")

        gp_frames = 0
        running = True
        quit_requested = False
        paint_dashboard(img.data, SEG_DASHBRD)       # full once; bezel-only per frame below
        fader.start_in()                              # frozen fade IN from black on level start
        while running and (window_frames <= 0 or gp_frames_total + gp_frames < window_frames):
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT or (
                        ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE):
                    running = False
                    quit_requested = True
            keys = pygame.key.get_pressed()
            view.speed = (1 if keys[pygame.K_UP] else 0) - (1 if keys[pygame.K_DOWN] else 0)
            view.steer = ((1 if keys[pygame.K_RIGHT] else 0)
                          - (1 if keys[pygame.K_LEFT] else 0)) & 0xFFFF
            view.jump = 1 if keys[pygame.K_SPACE] else 0
            view.elapsed_ticks = (view.elapsed_ticks + 2) & 0xFFFF
            if music_engine is not None:
                try:
                    for _ in range(MUSIC_TICKS_PER_FRAME):
                        writes = music_engine.run_tick()
                        for off, b in music_engine.ovl.items():
                            img.wb(DATA_SEG, off, b)
                        for reg, val in writes:
                            music_synth.write(reg, val)
                    music_synth.pump()
                except Exception as e:                       # noqa: BLE001
                    print(f"[window] music stopped ({e})")
                    music_engine = None

            # Gameplay is FROZEN for the whole fade window (VM-verified -- see
            # TransitionFader), not just while the screen is black.
            if fader.active:
                fader.tick()
                # A finish doesn't get the fade BACK in (that would reveal the
                # abandoned gameplay scene again) -- stop exactly at the black
                # mid-point (`remaining == FADE_FRAMES`, the same instant
                # `_switch` fires) and let the level-select's own fade-in
                # (below) pick up from there.
                if finished[0] and fader.remaining <= FADE_FRAMES:
                    running = False
            else:
                outcome = driver.tick()
                if outcome.transitioned:
                    print(f"[window] {outcome.reason} (kind={outcome.kind!r}) -- fading out")
                    finished[0] = (outcome.kind == "finish")
                    fader.start_out()
            # Full road re-render every frame -- see the run_window() comment: the
            # delta/skip render paths leave stale edge terrain (edge ghosting), so
            # the windowed viewer always does a full render (~6 ms/frame).
            render_native_frame(img, DATA_SEG, offscreen=1, rebuild=True)
            paint_dashboard(img.data, SEG_DASHBRD, byte_count=DASHBOARD_BEZEL_OVERLAP)
            update_hud(img, DATA_SEG, view.ship_pos)
            update_progress_bar(img, DATA_SEG)
            draw_grav_meter(img, DATA_SEG)
            present(to_rgb(bytes(img.data[0xA0000:0xA0000 + 64000]),
                           fader.palette(palette) or palette))
            clock.tick(GAME_FPS)
            gp_frames += 1
        gp_frames_total += gp_frames

        if quit_requested:
            pygame.quit()
            print(f"[window] closed after {gp_frames_total} gameplay frames")
            return
        if finished[0]:
            selected = run_level_select(selected, fade_in=True)
            continue
        # window_frames budget reached mid-level (not a finish) -- stop here,
        # matching the previous single-level behaviour.
        pygame.quit()
        print(f"[window] closed after {gp_frames_total} gameplay frames")
        return
    pygame.quit()
    print("[window] closed at menu")


def run_cold_verify(root: Path, demo_path: Path, max_ticks: int = 2000) -> None:
    """The strongest form of the cold-run proof: reset the REAL VM (the
    unmodified original game) to the SAME cold level-start state, force zero
    input on every sub-step, and check it independently reaches the same
    level-complete conclusion -- confirming the native cold-run milestone
    isn't just self-consistent, it matches what the original game itself
    would do from the same starting point."""
    frontend = sp.SkyroadsFrontend(root)
    args = player.build_arg_parser(frontend).parse_args(
        ["--play-demo", str(demo_path), "--headless"])
    pb = InputDemoPlayback.load(str(demo_path))
    frontend.apply_demo_metadata(args, pb.manifest.get("metadata", {}))
    rt = (frontend.create_runtime(args) if pb.is_cold_start
          else frontend.load_snapshot_runtime(args, pb.snapshot_path()))
    args.install_replacements = False  # the pure ASM oracle -- the strongest proof
    frontend.apply_hook_mode(rt, args)
    _use_real_console_input(rt)

    r = RespawnState()
    reset_done = [False]
    tick_count = [0]
    result = {}

    def _reset_and_zero_input(cpu):
        m = cpu.mem
        ds = cpu.s.ds
        if not reset_done[0] and m.rw(ds, 0x456E) == 0:
            gate = m.rw(ds, 0x4562)
            m.ww(ds, 0x9618, r.lateral_lo); m.ww(ds, 0x961A, r.lateral_hi)
            m.ww(ds, 0xAF1C, r.vert_af1c); m.ww(ds, 0xAF2C, r.vert_af2c)
            m.ww(ds, 0x5496, r.unknown_5496); m.ww(ds, 0x4568, r.lateral_accel)
            m.ww(ds, 0x9336, r.vvel)
            m.ww(ds, 0x54AC, r.ship_pos_lo); m.ww(ds, 0x54AE, r.ship_pos_hi)
            m.ww(ds, 0x5494, r.level_timer_a); m.ww(ds, 0xB13C, r.level_timer_b)
            m.ww(ds, 0x456E, r.game_state); m.ww(ds, 0x4558, r.frame_ctr)
            m.ww(ds, 0x456A, r.unknown_456a)
            m.ww(ds, 0x54AA, level_gravity(gate))
            m.ww(ds, 0x95F4, 0); m.ww(ds, 0x547A, 0); m.ww(ds, 0x9330, 0)
            for o in range(0x0BD0, 0x0BE0):
                m.wb(ds, o, 0)
            reset_done[0] = True
            print("[vm-cold] VM memory reset to the same cold apply_level_init() state")
        elif reset_done[0]:
            m.ww(ds, 0x95F4, 0); m.ww(ds, 0x547A, 0); m.ww(ds, 0x9330, 0)
            if cpu.s.ip == LOOP_TOP_IP:
                tick_count[0] += 1
                gs = m.rw(ds, 0x456E)
                ship = m.rw(ds, 0x54AC) | (m.rw(ds, 0x54AE) << 16)
                if gs != 0 and "final_tick" not in result:
                    result["final_tick"] = tick_count[0]
                    result["game_state"] = gs
                    result["ship_pos"] = ship

    orig = CPU8086.step

    def patched(self):
        if self.s.cs == 0x1010 and self.s.ip == LOOP_TOP_IP:
            _reset_and_zero_input(self)
        return orig(self)

    CPU8086.step = patched
    try:
        frame = 0
        # Keep advancing PAST the demo's own recorded length -- input is force-
        # zeroed every sub-step regardless (see _reset_and_zero_input), so the
        # demo's own recorded length is irrelevant once the cold reset happens.
        while frame < max_ticks + 200 and "final_tick" not in result:
            if not pb.finished(frame):
                pb.apply_to_runtime(frame, rt, deliver=lambda r, sc: frontend.deliver_input(r, sc))
            try:
                frontend.advance_frame(rt, args, frame)
            except ConsoleInputWouldBlock:
                pass
            except HaltExecution:
                break
            frame += 1
    finally:
        CPU8086.step = orig

    print(f"\n[vm-cold] result: {result}")
    if result.get("game_state") == 2:
        print("*** VM independently confirms: same cold start -> level complete ***")


def run_verify(root: Path, demo_path: Path) -> None:
    """The convergence proof: run the native driver in LOCKSTEP with the VM,
    injecting only input, and report every run's streak length + why it ended."""
    frontend = sp.SkyroadsFrontend(root)
    args = player.build_arg_parser(frontend).parse_args(
        ["--play-demo", str(demo_path), "--headless"])
    pb = InputDemoPlayback.load(str(demo_path))
    frontend.apply_demo_metadata(args, pb.manifest.get("metadata", {}))
    rt = (frontend.create_runtime(args) if pb.is_cold_start
          else frontend.load_snapshot_runtime(args, pb.snapshot_path()))
    args.install_replacements = False
    frontend.apply_hook_mode(rt, args)
    _use_real_console_input(rt)

    CMP_W = {0x9336: "bounce", 0xAF1C: "af1c", 0xAF2C: "af2c", 0x456E: "game_state",
             0x456A: "f456a", 0x4568: "lateral_accel", 0x5496: "u5496", 0x5494: "timer_a",
             0xB13C: "timer_b", 0x4558: "frame_ctr", 0x455A: "f455a",
             0xAF2E: "af2e", 0xAF30: "af30"}
    CMP_D = {0x54AC: "ship_pos", 0x9618: "lateral"}

    ctx = {"nst": None, "nsc": None, "streak": 0}
    runs = []

    def step(cpu):
        m = cpu.mem
        ds = cpu.s.ds
        ss = cpu.s.ss
        bp = cpu.s.bp
        gs = m.rw(ds, 0x456E)

        if ctx["nst"] is None:
            if gs != 0:
                return
            ctx["nst"] = NativeGameState(bytearray(m.data[(ds << 4):(ds << 4) + 0x10000]))
            ctx["nsc"] = GameplayScratch(
                JumpScratch(_bpw(m, ss, bp, 8), _bpw(m, ss, bp, 10), _bpw(m, ss, bp, 6)),
                _bpw(m, ss, bp, 12), _bpw(m, ss, bp, 14), _bpw(m, ss, bp, 24),
                _bpw(m, ss, bp, 28))
            ctx["streak"] = 0
        else:
            st = ctx["nst"]
            diffs = [n for off, n in CMP_W.items() if st.rw(off) != m.rw(ds, off)]
            diffs += [n for off, n in CMP_D.items()
                      if (st.rw(off) | (st.rw(off + 2) << 16)) != (m.rw(ds, off) | (m.rw(ds, off + 2) << 16))]
            if diffs:
                runs.append((ctx["streak"], diffs))
                ctx["nst"] = None
                return
            ctx["streak"] += 1

        st = ctx["nst"]
        for off in INPUT_OFFS:
            st.ww(off, m.rw(ds, off))
        try:
            from skyroads.native.loop import native_gameplay_substep
            ctx["nsc"] = native_gameplay_substep(GameView(st), ctx["nsc"], allow_unmodelled_effect=True)
        except SkyroadsGap as exc:
            runs.append((ctx["streak"], [f"GAP: {exc}"]))
            ctx["nst"] = None

    orig = CPU8086.step

    def patched(self):
        if self.s.cs == 0x1010 and self.s.ip == LOOP_TOP_IP:
            step(self)
        return orig(self)

    CPU8086.step = patched
    try:
        frame = 0
        while not pb.finished(frame):
            pb.apply_to_runtime(frame, rt, deliver=lambda r, sc: frontend.deliver_input(r, sc))
            try:
                frontend.advance_frame(rt, args, frame)
            except ConsoleInputWouldBlock:
                pass
            except HaltExecution:
                break
            frame += 1
    finally:
        CPU8086.step = orig

    total = sum(s for s, _ in runs)
    field_breaks = [r for r in runs if r[1] and not str(r[1][0]).startswith("GAP")]
    print(f"[verify] {len(runs)} lockstep runs, {total} total in-sync steps, "
          f"longest={max((s for s, _ in runs), default=0)}")
    for streak, cause in runs:
        print(f"  {streak:5d} steps in sync -> {cause}")
    if field_breaks:
        print(f"\n*** {len(field_breaks)} run(s) ended on a real field divergence, not a clean gap ***")
    else:
        print("\nAll runs ended on a detected boundary (gap) -- zero silent drift.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--demo", help="demo dir to boot from and seed level data with "
                   "(not needed with --level)")
    p.add_argument("--level", type=int, default=None,
                   help="play THIS level index (0-30) VM-FREE: load its geometry from ROADS.LZS "
                        "and play natively -- no demo, no per-run snapshot")
    p.add_argument("--baseline", default="artifacts/snapshots/gameplay_f640",
                   help="constants-baseline snapshot dir for --level (level-independent startup "
                        "constants; the geometry in it is overwritten). Default: %(default)s")
    p.add_argument("--headless", action="store_true",
                   help="with --level: run the HEADLESS native sim (agent/CI use -- plays the "
                        "level's physics holding accelerate and prints the outcome) instead of "
                        "opening the game window")
    p.add_argument("--window", action="store_true", help=argparse.SUPPRESS)  # deprecated: now the default
    p.add_argument("--window-baseline", default="artifacts/frame_2d1f/snap92",
                   help="full snapshot (memory_1mb.bin + state.json) supplying world graphics "
                        "banks + palette for --window. Default: %(default)s")
    p.add_argument("--window-frames", type=int, default=0,
                   help="auto-quit the window after N frames (0 = run until closed)")
    p.add_argument("--cold-native", action="store_true",
                   help="MILESTONE 2: with --level, build the ENTIRE 1 MB image (program, DGROUP, "
                        "display-list buffers, palette) from the game files alone -- no VM, no "
                        "snapshot at all (ignores --window-baseline)")
    p.add_argument("--extra-ticks", type=int, default=0,
                   help="keep ticking the native driver this many times past the demo's recorded input")
    p.add_argument("--verify", action="store_true",
                   help="run the VM alongside and report native/VM divergence instead of a plain offline replay")
    p.add_argument("--cold", action="store_true",
                   help="THE MILESTONE: reset to a genuine cold level start (ship_pos=0) and play the "
                        "WHOLE LEVEL with zero player input, 100%% native, until it completes")
    p.add_argument("--cold-verify", action="store_true",
                   help="like --cold, but ALSO resets the real VM to the same cold state and confirms "
                        "it independently reaches the same level-complete conclusion")
    p.add_argument("--max-ticks", type=int, default=2000, help="tick budget for --cold/--cold-verify")
    p.add_argument("--boot", action="store_true",
                   help="MILESTONE 2, the full cold start: LOGO.PCX splash + INTRO.SND, then the "
                        "native level-select screen (no --level needed), then real gameplay -- "
                        "program+DGROUP+display-lists+menu+level all built from the game files "
                        "alone. Navigate the grid with arrows, enter/space to play")
    args = p.parse_args()

    if args.boot:
        run_cold_boot(ROOT, args.window_frames)
        return

    if args.level is not None:
        if args.headless:                        # agent/CI: the sim-only run
            baseline = Path(args.baseline)
            if not baseline.is_absolute():
                baseline = ROOT / baseline
            run_level(ROOT, args.level, baseline, args.max_ticks)
            return
        # DEFAULT: the game window -- this is how a person plays.
        wb = Path(args.window_baseline)
        if not wb.is_absolute():
            wb = ROOT / wb
        run_window(ROOT, args.level, wb, args.window_frames, cold_native=args.cold_native)
        return

    if not args.demo:
        p.error("one of --demo or --level is required")
    demo_path = ROOT / args.demo if not Path(args.demo).is_absolute() else Path(args.demo)

    if args.cold_verify:
        run_cold_verify(ROOT, demo_path, args.max_ticks)
        return

    if args.verify:
        run_verify(ROOT, demo_path)
        return

    print(f"[boot] driving the original game to real gameplay via the VM ({demo_path.name})...")
    seed, inputs, _live = boot_and_seed(ROOT, demo_path)
    print(f"[boot] seeded at frame {seed['frame']}, jump_level_gate={seed['jump_level_gate']} "
          f"-- switching to 100% native from here ({len(inputs)} recorded input frames)")

    if args.cold:
        run_cold(seed["state"], seed["jump_level_gate"], args.max_ticks)
        return

    run_offline(seed["state"], seed["scratch"], seed["jump_level_gate"], inputs, args.extra_ticks)


if __name__ == "__main__":
    main()
