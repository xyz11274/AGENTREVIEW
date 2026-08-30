#!/usr/bin/env python3
"""Materialize the model-free CORPUS_BUILD_BOOTSTRAP gate."""

from __future__ import annotations

import argparse
import json

from agentreview.corpus_bootstrap import materialize_corpus_bootstrap


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodes-root", required=True)
    parser.add_argument("--selection", required=True)
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--served-model-name", required=True)
    parser.add_argument("--output-token-cap", type=int, required=True)
    parser.add_argument("--model-context-limit", type=int, required=True)
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.95)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        args.model_path,
        local_files_only=True,
        trust_remote_code=False,
    )
    result = materialize_corpus_bootstrap(
        episodes_root=args.episodes_root,
        selection_path=args.selection,
        model_path=args.model_path,
        tokenizer=tokenizer,
        output_token_cap=args.output_token_cap,
        model_revision=args.model_revision,
        served_model_name=args.served_model_name,
        model_context_limit=args.model_context_limit,
        gpu_memory_utilization=args.gpu_memory_utilization,
    )
    result.write(args.output)
    print(
        json.dumps(
            {
                "a_manifest_sha256": result.a_manifest["a_manifest_sha256"],
                "request_count": len(result.requests),
                "corpus_request_materialization_sha256": result.request_manifest[
                    "corpus_request_materialization_sha256"
                ],
                "required_context_tokens": result.runtime_profile[
                    "required_context_tokens"
                ],
                "corpus_build_max_model_len": result.runtime_profile[
                    "corpus_build_max_model_len"
                ],
                "corpus_build_runtime_profile_sha256": result.runtime_profile[
                    "corpus_build_runtime_profile_sha256"
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
