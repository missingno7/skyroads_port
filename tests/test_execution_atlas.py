from __future__ import annotations

import json
from pathlib import Path
import shutil

from dos_re.atlas import ExecutionAtlas
from dos_re.replay import ReplayArtifact

from skyroads.execution import ATLAS_DIR, catalog, coverage
from skyroads.identities import (
    PROGRAM_ROOT,
    execution_point_identity,
    function_identity,
)

ROOT = Path(__file__).resolve().parents[1]
ORACLE_REPLAY = ROOT / "recovery" / "replays" / "oracle_atlas_smoke"
CANDIDATE_REPLAY = ROOT / "recovery" / "replays" / "candidate_smoke"


def test_committed_atlas_combines_retained_ir_and_real_oracle_replay():
    atlas = coverage()
    functions = atlas.nodes(kind="function")
    refused = [
        node for node in functions if node.metadata.get("liftable") is False]

    assert len(functions) == 180
    assert len(refused) == 3
    assert atlas.resolve("1010:61F3").identity == function_identity(0x61F3)
    assert atlas.unresolved()
    assert any(edge.status == "observed" for edge in atlas.edges())

    replay = ReplayArtifact.open(ORACLE_REPLAY)
    profile = replay.profiles()[0][0]
    evidence = replay.execution_evidence()
    assert profile.role == "oracle"
    assert evidence is not None
    assert evidence.transfers
    assert replay.function_visits()

    covered = [
        node for node in functions if atlas.replay_coverage(node.identity)]
    assert len(covered) == 158
    hot = atlas.best_replay(function_identity(0x4153))
    assert hot.complete
    assert hot.invocation_count > 100_000
    assert hot.first_entry.ordinal == 365
    assert hot.last_exit.ordinal == 1991
    assert atlas.best_replay(function_identity(0x3B17)).complete

    candidate = ReplayArtifact.open(CANDIDATE_REPLAY)
    assert candidate.trusted
    assert len(candidate.timeline_coordinates) == 2378
    assert len(candidate.function_visits()) == 158
    assert len(candidate.execution_evidence().transfers) == 814


def test_atlas_is_the_planner_coverage_authority():
    atlas = coverage()
    product = atlas.coverage_for("game")

    assert product.roots == (PROGRAM_ROOT,)
    assert function_identity(0x61F3) in product.reachable
    assert execution_point_identity(0x22F8) in product.reachable
    assert execution_point_identity(0x434A) in product.reachable
    assert execution_point_identity(0x47CD) in product.reachable
    assert product.unresolved_edges
    assert len(product.edges) > 1_000
    assert product.evidence_identity == atlas.identity_digest

    implementation_view = atlas.implementation_view(catalog())
    root = next(item for item in implementation_view
                if item["function_id"] == function_identity(0x61F3))
    assert {item["implementation_id"] for item in root["implementations"]} >= {
        "baseline:interpreted-exe",
        "baseline:generated-vmless",
        "baseline:generated-cpuless",
    }


def test_committed_atlas_indexes_rematerialize_byte_identically(tmp_path):
    copied = tmp_path / "atlas"
    shutil.copytree(ATLAS_DIR, copied)
    atlas = ExecutionAtlas.open(copied)
    before = {
        path.relative_to(copied): path.read_bytes()
        for path in copied.rglob("*.json")
    }

    atlas.rematerialize()

    after = {
        path.relative_to(copied): path.read_bytes()
        for path in copied.rglob("*.json")
    }
    assert before == after
    manifest = json.loads((copied / "manifest.json").read_text(encoding="utf-8"))
    assert {source["kind"] for source in manifest["sources"]} == {
        "manual", "replay", "static"}
