"""CLI entrypoint for llm_port_ray_runtime.

This is the **JSON control contract** between the host Node Agent and the
certified runtime image (03_PHASED_MIGRATION_PLAN.md 4B, "Node Agent/runtime
helper boundary").  The host owns inventory, the OCI digest, container
lifecycle and mounts; every Ray-aware SDK/status/deployment operation happens
here, inside the image, with the exact Ray version the cluster runs.

Every subcommand prints a single JSON document on stdout and nothing else, so
the agent can parse it without screen-scraping.  Exit status is 0 when the
operation succeeded, 1 otherwise; the agent reads the JSON either way.
"""

import argparse
import json
import sys

from llm_port_ray_runtime.core import RayCoreClient
from llm_port_ray_runtime.serve import RayServeClient
from llm_port_ray_runtime.versions import get_runtime_versions


def _emit(payload: dict, *, ok: bool) -> None:
    """Print one JSON document and exit with the matching status."""
    print(json.dumps(payload, indent=2, default=str))
    sys.exit(0 if ok else 1)


def _read_document(raw: str | None) -> dict:
    """Read a JSON document from an argument, a file path, or stdin.

    A Serve config is far too large for an argv-safe string on some runtimes,
    so ``-`` (or omitting the argument) reads stdin.
    """
    if raw is None or raw == "-":
        return json.loads(sys.stdin.read())
    candidate = raw.strip()
    if candidate.startswith("{"):
        return json.loads(candidate)
    with open(candidate, encoding="utf-8") as handle:
        return json.load(handle)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="llm-port-ray-runtime",
        description="LLM.Port Ray runtime CLI helper for DGX Spark / GB10",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── read verbs ────────────────────────────────────────────────────────
    subparsers.add_parser("versions", help="Display runtime versions as JSON")
    subparsers.add_parser("probe", help="Perform a quick in-process Ray Core probe")
    subparsers.add_parser("cluster-status", help="Display full cluster status as JSON")

    serve_status = subparsers.add_parser("serve-status", help="Display Ray Serve status as JSON")
    serve_status.add_argument("--app-name", default=None, help="Limit the report to one application")

    # ── write verbs ───────────────────────────────────────────────────────
    run_app = subparsers.add_parser("run-serve-app", help="Deploy a named Serve LLM application")
    run_app.add_argument("--app-name", required=True)
    run_app.add_argument(
        "--config",
        default="-",
        help="LLMServingArgs document: inline JSON, a file path, or '-' for stdin (default)",
    )
    run_app.add_argument("--route-prefix", default="/")

    delete_app = subparsers.add_parser("delete-serve-app", help="Delete a named Serve application")
    delete_app.add_argument("--app-name", required=True)

    args = parser.parse_args()

    if args.command == "versions":
        _emit(get_runtime_versions(), ok=True)

    elif args.command == "probe":
        status = RayCoreClient().probe()
        _emit(
            {
                "alive": status.alive,
                # ``version`` mirrors ``ray_version`` so the host agent sees the
                # same key the host-SDK path emits; dropping it meant the
                # backend parsed ``version=None`` for every containerized
                # cluster and lost the observed Ray version.
                "version": status.ray_version,
                "ray_version": status.ray_version,
                "num_nodes": status.num_nodes,
                "total_gpus": status.total_gpus,
                "available_gpus": status.available_gpus,
                "total_cpus": status.total_cpus,
                "available_cpus": status.available_cpus,
                "cluster_address": status.cluster_address,
                "head_address": status.head_address,
            },
            ok=status.alive,
        )

    elif args.command == "cluster-status":
        status = RayCoreClient().probe()
        payload = json.loads(status.model_dump_json())
        payload["version"] = status.ray_version
        _emit(payload, ok=status.alive)

    elif args.command == "serve-status":
        status = RayServeClient().status()
        payload = json.loads(status.model_dump_json())
        app_name = getattr(args, "app_name", None)
        if app_name:
            apps = payload.get("applications") or {}
            payload["applications"] = {k: v for k, v in apps.items() if k == app_name}
        _emit(payload, ok=status.available)

    elif args.command == "run-serve-app":
        try:
            config = _read_document(args.config)
        except (OSError, ValueError) as exc:
            _emit({"deployed": False, "app_name": args.app_name, "error": f"bad config: {exc}"}, ok=False)
            return
        llm_serving_args = config.get("llm_serving_args", config)
        http_options = config.get("http_options")
        try:
            result = RayServeClient().run_app(
                args.app_name,
                llm_serving_args,
                http_options=http_options,
                route_prefix=args.route_prefix,
            )
        except Exception as exc:  # noqa: BLE001 - the agent needs the reason, not a traceback
            _emit({"deployed": False, "app_name": args.app_name, "error": str(exc)}, ok=False)
            return
        _emit(result, ok=bool(result.get("deployed")))

    elif args.command == "delete-serve-app":
        result = RayServeClient().delete_app(args.app_name)
        _emit(result, ok=bool(result.get("deleted")))


if __name__ == "__main__":
    main()
