"""Protect portable builds, safe delivery migration and cwd-independent launchers."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("delivery_layout", ROOT / "scripts/export_v4.py")
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


def test_portable_build_has_one_source_and_no_local_state():
    files, sources = delivery.build_files()
    assert "src/task2_video_panel/manage.py" in files
    assert "tests/run_tests.py" in files
    assert "scripts/windows/start_windows.ps1" in files
    assert "deploy/supervise.py" in files
    assert "config/runtime_config.example.json" in files
    assert {n for n in files if n.startswith("windows_preview/")} == {"windows_preview/README.md"}
    assert not any(n.startswith(("archive/", "artifacts/")) for n in files)
    assert "config/runtime_config.json" not in files
    assert "config/last_v4.json" not in files
    assert files["docs/使用说明.md"] == delivery.usage(ROOT, False).encode("utf-8")
    assert str(ROOT).encode("utf-8") not in files["docs/使用说明.md"]
    for name, data in files.items():
        if name.endswith((".py", ".ps1", ".bat")):
            assert data == (ROOT / name).read_bytes(), name
    assert sources["src/task2_video_panel/manage.py"] == delivery.digest(files["src/task2_video_panel/manage.py"])


def sandbox_delivery(tmp_path, monkeypatch):
    root = tmp_path / "v3"
    target = tmp_path / "v4"
    manifest = root / "config/last_v4.json"
    old = {"old_source/main.py": b"old source\n", "README.md": b"old readme\n"}
    delivery.write_files(target, old)
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"target": str(target), "files": {n: delivery.digest(d) for n, d in old.items()}}), encoding="utf-8")
    monkeypatch.setattr(delivery, "ROOT", root)
    monkeypatch.setattr(delivery, "TARGET", target)
    monkeypatch.setattr(delivery, "MANIFEST", manifest)
    return root, target, old


def test_delivery_migration_preserves_user_data_and_backs_up_old_files(tmp_path, monkeypatch):
    root, target, old = sandbox_delivery(tmp_path, monkeypatch)
    user_files = {"windows_preview/runtime/data/example.db": b"user database", "config/runtime_config.json": b"{}"}
    delivery.write_files(target, user_files)
    new = {"src/main.py": b"new source\n", "README.md": b"new readme\n"}
    run = root / "artifacts/release"
    delivery.synchronize(new, run)
    assert not (target / "old_source/main.py").exists()
    for name, data in {**user_files, **new}.items():
        assert (target / name).read_bytes() == data
    for name, data in old.items():
        assert (run / "previous_delivery" / name).read_bytes() == data


def test_delivery_rejects_modified_obsolete_file_before_any_mutation(tmp_path, monkeypatch):
    root, target, old = sandbox_delivery(tmp_path, monkeypatch)
    (target / "old_source/main.py").write_bytes(b"user edit")
    with pytest.raises(RuntimeError, match="locally modified"):
        delivery.synchronize({"src/main.py": b"new"}, root / "artifacts/release")
    assert (target / "old_source/main.py").read_bytes() == b"user edit"
    assert (target / "README.md").read_bytes() == old["README.md"]
    assert not (target / "src/main.py").exists()


def test_delivery_rolls_back_partial_write_failure(tmp_path, monkeypatch):
    root, target, old = sandbox_delivery(tmp_path, monkeypatch)
    replace = Path.replace

    def fail_last(path, destination):
        if Path(destination) == target / "src/failure.py":
            raise OSError("simulated write failure")
        return replace(path, destination)

    monkeypatch.setattr(Path, "replace", fail_last)
    with pytest.raises(OSError, match="simulated write failure"):
        delivery.synchronize({"README.md": b"changed", "src/first.py": b"first", "src/failure.py": b"last"}, root / "artifacts/release")
    for name, data in old.items():
        assert (target / name).read_bytes() == data
    assert not list((target / "src").rglob("*.*"))


def test_real_model_assets_require_explicit_configuration(tmp_path):
    from model_assets import load_assets
    with pytest.raises(ValueError, match="Configure model_path"):
        load_assets(tmp_path, {})
    (tmp_path / 'config').mkdir()
    values = {}
    for name in ('model_path', 'server_path', 'mmproj'):
        (tmp_path / name).write_text('fixture', encoding='utf-8')
        values[name] = name
    (tmp_path / 'config/runtime_config.json').write_text(json.dumps(values), encoding='utf-8')
    (tmp_path / 'override').write_text('fixture', encoding='utf-8')
    result = load_assets(tmp_path, {'SCREEN_GUARD_MMPROJ': 'override'})
    assert result['model_path'] == str(tmp_path / 'model_path')
    assert result['mmproj'] == str(tmp_path / 'override')


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher configuration")
@pytest.mark.parametrize("use_example", [False, True])
def test_fresh_windows_config_uses_project_venv_and_optional_models(tmp_path, use_example):
    script = tmp_path / 'scripts/windows/runtime_config.ps1'
    script.parent.mkdir(parents=True)
    script.write_bytes((ROOT / 'scripts/windows/runtime_config.ps1').read_bytes())
    if use_example:
        (tmp_path / 'config').mkdir()
        (tmp_path / 'config/runtime_config.json').write_bytes((ROOT / 'config/runtime_config.example.json').read_bytes())
    other_cwd = tmp_path / 'other'
    other_cwd.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith('SCREEN_GUARD_')}
    command = "$ErrorActionPreference='Stop'; . '" + str(script).replace("'", "''") + "'; Get-ScreenGuardRuntime | ConvertTo-Json -Compress"
    result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', command],
                            cwd=other_cwd, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    values = json.loads(result.stdout)
    assert Path(values['python']) == tmp_path / '.venv/Scripts/python.exe'
    assert Path(values['data_dir']) == tmp_path / 'windows_preview/runtime/data'
    assert all(values[key] == '' for key in ('model_path', 'server_path', 'mmproj'))


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher configuration")
@pytest.mark.parametrize("outside", [False, True])
def test_windows_config_resolves_from_other_working_directory(tmp_path, outside):
    env = {k: v for k, v in os.environ.items() if not k.startswith("SCREEN_GUARD_")}
    relative = tmp_path.relative_to(ROOT) / "isolated"
    env.update(SCREEN_GUARD_DATA_DIR=str(ROOT.parent / "outside") if outside else str(relative / "data"),
               SCREEN_GUARD_STATE_DIR=str(relative / "state"), SCREEN_GUARD_PORT="18765",
               SCREEN_GUARD_PYTHON='.venv/Scripts/python.exe', SCREEN_GUARD_MMPROJ='models/mmproj.gguf')
    script = str(ROOT / "scripts/windows/runtime_config.ps1").replace("'", "''")
    command = "$ErrorActionPreference='Stop'; . '" + script + "'; Get-ScreenGuardRuntime | ConvertTo-Json -Compress"
    result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=20)
    if outside:
        assert result.returncode != 0
        assert "Runtime path must remain inside this project" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        values = json.loads(result.stdout)
        assert Path(values["data_dir"]) == ROOT / relative / "data"
        assert Path(values["state_dir"]) == ROOT / relative / "state"
        assert values["port"] == 18765
        assert Path(values['python']) == ROOT / '.venv/Scripts/python.exe'
        assert Path(values['mmproj']) == ROOT / 'models/mmproj.gguf'
