"""0930 shared-memory API experiment entry point."""
import argparse
import json


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m agentreview.research_v2")
    stages = parser.add_subparsers(dest="stage", required=True)
    shared = stages.add_parser("shared-experiment")
    shared.add_argument("--protocol", required=True)
    shared.add_argument("--output", required=True)
    shared.add_argument("--action", choices=("preflight", "run", "final-test"), required=True)
    shared.add_argument("--repair-evidence")
    shared.add_argument("--frozen-system")
    report = stages.add_parser("experiment-report")
    report.add_argument("--manifest", required=True)
    report.add_argument("--output", required=True)
    report.add_argument("--report-stage", choices=("development", "final"), default="development")
    args = parser.parse_args(argv)
    if args.stage == "experiment-report":
        from .experiment_report import generate_experiment_report
        result = generate_experiment_report(args.manifest, args.output, stage=args.report_stage)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.stage == "shared-experiment":
        from .experiment_protocol import load_experiment_protocol
        from .shared_experiment import APISharedBackend, run_shared_experiment, run_shared_final_test
        if args.action == "preflight":
            protocol = load_experiment_protocol(args.protocol)
            APISharedBackend(protocol=protocol, output_root=args.output).preflight()
            result = {"status": "ready", "protocol_sha256": protocol.protocol_sha256,
                      "scope": "frozen configuration and development assets; model requests pending"}
        elif args.action == "final-test":
            if args.frozen_system:
                result = run_shared_final_test(protocol_path=args.protocol, output_root=args.output,
                    frozen_system_path=args.frozen_system, repair_evidence=args.repair_evidence)
            else:
                raise ValueError("Final test requires --frozen-system")
        else:
            result = run_shared_experiment(protocol_path=args.protocol, output_root=args.output,
                                           repair_evidence=args.repair_evidence)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") in {"ready", "complete"} else 2
