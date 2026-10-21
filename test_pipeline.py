"""Run: python test_pipeline.py - checks step order, build pinning and the dry-run path, with stub steps."""

from pathlib import Path

import pipeline


def run_with_stubs(argv):
    """Run pipeline.main with every step stubbed, on a fake build '9.9.9' that is not decoded yet."""
    calls = []
    modules = [module for _, module, _ in pipeline.steps(pipeline.parse_args([]), "x")]
    saved_mains = {module: module.main for module in modules}
    saved = (pipeline.read_build, pipeline.decode.DATA_JSON_ROOT, pipeline.write_manifest)
    try:
        for module in modules:
            module.main = lambda step_argv, name=module.__name__: calls.append((name, step_argv)) or 0
        pipeline.read_build = lambda _channel_dir: {"Version": "9.9.9"}
        pipeline.decode.DATA_JSON_ROOT = Path("nonexistent-data-json")
        pipeline.write_manifest = lambda *_args: "manifest"
        assert pipeline.main(argv) == 0
    finally:
        for module, main in saved_mains.items():
            module.main = main
        pipeline.read_build, pipeline.decode.DATA_JSON_ROOT, pipeline.write_manifest = saved
    return calls


def test_full_run_order_and_build():
    calls = run_with_stubs(["--force"])
    assert [name for name, _ in calls] == ["extract", "decode", "explore", "normalize", "storage_locations"]
    assert "--force" in calls[0][1] and "--force" in calls[1][1] and "--force" not in calls[2][1]
    assert all("9.9.9" in step_argv for _, step_argv in calls[1:])


def test_dry_run_only_lists_phase_1_when_build_is_new():
    calls = run_with_stubs(["--dry-run"])
    assert [name for name, _ in calls] == ["extract"] and calls[0][1][-1] == "--dry-run"


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print(f"ok {name}")
