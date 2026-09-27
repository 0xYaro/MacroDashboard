"""Re-running the migration over unchanged sources must be byte-identical."""
import json

from macro_migrate import config, emit, model


def test_emitting_twice_produces_identical_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUT", tmp_path / "a")
    emit.emit(model.build_all())
    first = {p.name: p.read_bytes() for p in (tmp_path / "a").iterdir()}

    monkeypatch.setattr(config, "OUT", tmp_path / "b")
    emit.emit(model.build_all())
    second = {p.name: p.read_bytes() for p in (tmp_path / "b").iterdir()}

    assert sorted(first) == sorted(second)
    for name in first:
        assert first[name] == second[name], name


def test_the_checked_in_manifest_matches_the_checked_in_files():
    manifest = json.loads((config.OUT / "manifest.json").read_text())
    import hashlib
    for name, digest in manifest.items():
        p = config.OUT / name
        assert p.exists(), name
        assert hashlib.sha256(p.read_bytes()).hexdigest() == digest, name


def test_ids_are_stable_across_runs():
    a = {r["id"] for r in model.build_all().rows["scenario_target"]}
    b = {r["id"] for r in model.build_all().rows["scenario_target"]}
    assert a == b and len(a) == 210
