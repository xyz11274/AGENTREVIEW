#!/usr/bin/env python3
"""Generate the benchmark-independent mixed Qwen capacity workload."""

import argparse
import json
from pathlib import Path


ROLES = ("QUAL", "CLAR", "SIGN", "ORIG", "POL")
CLASSES = ("reviewer", "applicability", "scoring", "reflector", "compile_fix")


def messages(role, request_class, body):
    if request_class in {"applicability", "scoring"}:
        instruction = (
            "Return exactly this JSON object and no other text: "
            '{"status":"ok","score":2,"events":[]}'
        )
    elif request_class == "reflector":
        instruction = "Return one deterministic Python function named mutate()."
    elif request_class == "compile_fix":
        instruction = "Return the supplied valid Python unchanged, without a code fence."
    else:
        instruction = "Return a concise deterministic synthetic specialist review."
    return [
        {
            "role": "system",
            "content": f"Synthetic {role} {request_class} capacity smoke. {instruction}",
        },
        {
            "role": "user",
            "content": "UNTRUSTED SYNTHETIC INPUT\n" + body,
        },
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--typical-chars", type=int, default=16000)
    parser.add_argument("--near-max-chars", type=int, required=True)
    parser.add_argument("--max-output-tokens", type=int, default=256)
    args = parser.parse_args()
    lengths = (512, args.typical_chars, args.near_max_chars)
    values = []
    for role_index, role in enumerate(ROLES):
        for class_index, request_class in enumerate(CLASSES):
            for length_index, length in enumerate(lengths):
                atom = f"synthetic-{role}-{request_class}-evidence "
                body = (atom * (length // len(atom) + 1))[:length]
                values.append(
                    {
                        "role": role,
                        "request_class": request_class,
                        "length_class": ("short", "typical", "near_max")[length_index],
                        "messages": messages(role, request_class, body),
                        "max_tokens": args.max_output_tokens,
                        "seed": role_index * 100 + class_index * 10 + length_index,
                    }
                )
    target = Path(args.output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for value in values:
            handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"wrote {len(values)} benchmark-independent requests to {target}")


if __name__ == "__main__":
    main()
