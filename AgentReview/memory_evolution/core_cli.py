"""Independent command line entry point for Research Validity Core v1.

This command deliberately does not import or delegate to the legacy formal
CLI.  Real deployments provide a frozen application factory; the built-in
integration-smoke uses fake transports with the same composition root.
"""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
from typing import Callable

from agentreview.review_corpus import canonical_json_bytes

from .core_closure import audit_core_v1_closure
from .core_runtime import load_core_application_from_config
from .core_scheduler import ROLE_ORDER
from .core_smoke import run_production_integration_smoke


def _factory(value: str) -> Callable:
    module_name, separator, attribute = value.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("application factory must be MODULE:CALLABLE")
    result = getattr(importlib.import_module(module_name), attribute)
    if not callable(result):
        raise TypeError("application factory is not callable")
    return result


def _application(args):
    if args.config:
        return load_core_application_from_config(
            config_path=args.config, output_dir=Path(args.output_dir).resolve()
        )
    if not args.application_factory:
        raise ValueError("This command requires --config or --application-factory")
    return _factory(args.application_factory)(output_dir=Path(args.output_dir).resolve())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m memory_evolution.core_cli")
    commands = parser.add_subparsers(dest="command", required=True)

    smoke = commands.add_parser("integration-smoke")
    smoke.add_argument("--output-dir", required=True)

    closure = commands.add_parser("closure-audit")
    closure.add_argument("--smoke-report", required=True)
    closure.add_argument("--formal-artifact-root", required=True)
    closure.add_argument("--output", required=True)

    for name in ("validate-inputs", "freeze-suite", "run-official"):
        command = commands.add_parser(name)
        source = command.add_mutually_exclusive_group(required=True)
        source.add_argument("--config")
        source.add_argument("--application-factory")
        command.add_argument("--output-dir", required=True)

    role = commands.add_parser("run-role")
    source = role.add_mutually_exclusive_group(required=True)
    source.add_argument("--config")
    source.add_argument("--application-factory")
    role.add_argument("--output-dir", required=True)
    role.add_argument("--role", choices=ROLE_ORDER, required=True)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "integration-smoke":
        report = run_production_integration_smoke(args.output_dir)
        print(canonical_json_bytes(report).decode("utf-8"))
        return 0 if report.get("status") == "PASS" else 1
    if args.command == "closure-audit":
        report = audit_core_v1_closure(
            smoke_report_path=args.smoke_report,
            formal_artifact_root=args.formal_artifact_root,
            output_path=args.output,
        )
        print(canonical_json_bytes(report).decode("utf-8"))
        return 0 if report["G1_PRODUCTION_WIRING"]["status"] == "PASS" else 1

    application = _application(args)
    try:
        if args.command == "validate-inputs":
            result = application.validate_inputs()
        elif args.command == "run-role":
            role_result = application.run_role(args.role)
            result = {
                "status": "PASS",
                "role": args.role,
                "winner": role_result.winner.__dict__,
            }
        elif args.command == "freeze-suite":
            suite = application.freeze_suite()
            result = {
                "status": "PASS",
                "suite_identity_sha256": suite.suite_identity_sha256,
                "payload": suite.payload,
            }
        else:
            result = application.run_official()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        application.close()


if __name__ == "__main__":
    raise SystemExit(main())
