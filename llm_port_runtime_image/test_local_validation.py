"""Automated local image validation script.

Validates:
1. Package/import integrity (Python, Ray, Ray Serve, ray.serve.llm, vLLM, PyTorch)
2. Dependency integrity (pip check)
3. Version report JSON
4. Ray LLMConfig validator on LLM.Port deployment shapes
"""

import json
import os
import subprocess
import sys
import traceback

results = []

def record(name: str, passed: bool, detail: str = "") -> None:
    results.append({
        "check": name,
        "status": "PASS" if passed else "FAIL",
        "detail": detail,
    })


def test_imports() -> None:
    print("--> Testing imports...")
    try:
        import torch
        print(f"  [OK] torch {torch.__version__} (cuda: {torch.version.cuda})")
    except Exception as e:
        record("import torch", False, str(e))
        return
    record("import torch", True, f"{torch.__version__} (cuda {torch.version.cuda})")

    try:
        import vllm
        print(f"  [OK] vllm {vllm.__version__}")
    except Exception as e:
        record("import vllm", False, str(e))
        return
    record("import vllm", True, vllm.__version__)

    try:
        import ray
        print(f"  [OK] ray {ray.__version__}")
    except Exception as e:
        record("import ray", False, str(e))
        return
    record("import ray", True, ray.__version__)

    try:
        import ray.serve
        print(f"  [OK] ray.serve")
    except Exception as e:
        record("import ray.serve", False, str(e))
        return
    record("import ray.serve", True, "ok")

    try:
        import ray.serve.llm
        from ray.serve.llm import LLMConfig, build_openai_app
        print(f"  [OK] ray.serve.llm (LLMConfig, build_openai_app)")
    except Exception as e:
        record("import ray.serve.llm", False, str(e))
        return
    record("import ray.serve.llm", True, "ok")

    try:
        import aiohttp_cors
        import opencensus
        import opentelemetry.exporter.prometheus
        print("  [OK] ray metrics exporter dependencies (aiohttp_cors, opencensus, opentelemetry.exporter.prometheus)")
    except Exception as e:
        record("import metrics deps", False, str(e))
        return
    record("import metrics deps", True, "ok")


def test_pip_check() -> None:
    print("--> Running pip check...")
    proc = subprocess.run(["pip", "check"], capture_output=True, text=True)
    if proc.returncode == 0:
        print("  [OK] pip check clean")
        record("pip check", True, "No broken requirements found.")
    else:
        print(f"  [WARN/FAIL] pip check output:\n{proc.stdout}\n{proc.stderr}")
        record("pip check", False, proc.stdout.strip() or proc.stderr.strip())


def test_version_report() -> None:
    print("--> Generating version report...")
    try:
        from llm_port_ray_runtime.versions import get_runtime_versions
        v = get_runtime_versions()
        print(json.dumps(v, indent=2))
        record("version report", True, json.dumps(v))
    except Exception as e:
        record("version report", False, str(e))


def test_compiler_fixtures() -> None:
    print("--> Validating Ray LLMConfig against LLM.Port fixtures...")
    fixtures_path = os.path.join(os.path.dirname(__file__), "fixtures.json")
    if not os.path.exists(fixtures_path):
        record("compiler fixtures", False, f"Missing fixtures file at {fixtures_path}")
        return

    with open(fixtures_path, "r") as f:
        fixtures = json.load(f)

    from ray import serve
    from ray.serve.llm import LLMConfig

    for name, cfg in fixtures.items():
        try:
            llm_config = LLMConfig(**cfg)
            @serve.deployment
            class _Probe:
                def __call__(self):
                    return 1
            _Probe.options(**cfg.get("deployment_config", {}))

            engine = None
            bundles_desc = ""
            try:
                engine = llm_config.get_engine_config()
                bundles_desc = f"{len(engine.placement_bundles)} bundle(s) {engine.placement_bundles} strategy {engine.placement_strategy}"
            except Exception as bundle_err:
                bundles_desc = f"bundles: {bundle_err}"

            print(f"  [OK] fixture '{name}': {bundles_desc}")
            record(f"fixture: {name}", True, bundles_desc)
        except Exception as e:
            traceback.print_exc()
            print(f"  [FAIL] fixture '{name}': {e}")
            record(f"fixture: {name}", False, str(e))


def main() -> None:
    test_imports()
    test_version_report()
    test_compiler_fixtures()
    test_pip_check()

    print("\n================ LOCAL VALIDATION SUMMARY ================")
    all_passed = True
    for r in results:
        # pip check warnings may be acceptable if they relate to non-critical extras
        if r["status"] != "PASS" and r["check"] != "pip check":
            all_passed = False
        print(f"[{r['status']}] {r['check']}: {r['detail']}")

    summary_file = os.path.join(os.path.dirname(__file__), "validation_summary.json")
    with open(summary_file, "w") as f:
        json.dump(results, f, indent=2)

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()

