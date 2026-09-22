from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .api import call_tool
from .catalog import EXAMPLE_CATALOG, example
from .runner import capabilities, estimate, inspect, load_scene, patch_scene, prepare, query, simulate, validate


def _print(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(prog="physics-demo", description="Bounded agent-driven multiphysics demo")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("capabilities")
    example_command = commands.add_parser("example")
    example_command.add_argument("name", choices=sorted(EXAMPLE_CATALOG))
    system = commands.add_parser("system", help="Build a system config, optionally run it without editing any example")
    system.add_argument("config")
    system.add_argument("--out", help="Dedicated run directory; omit to return scene_json for prepare")
    system.add_argument("--no-video", action="store_true", help="Test/debug only")
    for name in ("validate", "estimate"):
        item = commands.add_parser(name)
        item.add_argument("scene")
        if name == "estimate":
            item.add_argument("--budget", type=float)
    prepare_command = commands.add_parser("prepare")
    prepare_command.add_argument("scene")
    prepare_command.add_argument("--budget", type=float)
    run = commands.add_parser("simulate")
    run.add_argument("scene")
    run.add_argument("--out", required=True)
    run.add_argument("--budget", type=float)
    run.add_argument("--no-video", action="store_true", help="Test/debug only; normal agent runs must produce MP4")
    show = commands.add_parser("inspect")
    show.add_argument("result")
    data_query = commands.add_parser("query")
    data_query.add_argument("result")
    data_query.add_argument("--id", dest="query_id")
    patch = commands.add_parser("patch")
    patch.add_argument("scene")
    patch.add_argument("operations")
    patch.add_argument("--out", required=True)
    tool = commands.add_parser("tool")
    tool.add_argument("request", help="JSON object containing tool and arguments")
    args = parser.parse_args()

    if args.command == "capabilities":
        result = capabilities()
    elif args.command == "example":
        result = example(args.name)
    elif args.command == "system":
        result = call_tool("physics_system", {"spec_json": Path(args.config).read_text(encoding="utf-8")})
        if result["ok"] and args.out:
            result = simulate(result["scene"], args.out, make_video=not args.no_video)
    elif args.command == "validate":
        result = validate(load_scene(args.scene))
    elif args.command == "estimate":
        result = estimate(load_scene(args.scene), args.budget)
    elif args.command == "prepare":
        result = prepare(load_scene(args.scene), args.budget)
    elif args.command == "simulate":
        result = simulate(load_scene(args.scene), args.out, args.budget, make_video=not args.no_video)
    elif args.command == "inspect":
        result = inspect(args.result)
    elif args.command == "query":
        result = query(args.result, args.query_id)
    elif args.command == "patch":
        operations = json.loads(Path(args.operations).read_text(encoding="utf-8"))
        result = patch_scene(load_scene(args.scene), operations)
        if result["ok"]:
            Path(args.out).write_text(json.dumps(result["scene"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            result["output"] = str(Path(args.out).resolve())
    else:
        request = json.loads(args.request)
        result = call_tool(request["tool"], request.get("arguments", {}))
    _print(result)
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
