"""Select whole development cases for reflection without altering score selection."""
from copy import deepcopy
import json

from .io import value_sha256


def pack_reflection_feedback(packet, *, prefix, count_chat_tokens, input_limit, output_budget):
    history = packet.get("history", {})
    if not isinstance(history, (dict, list)):
        raise TypeError("Reflection history requires complete attempt records")
    selected = {"parent": deepcopy(packet["parent"]), "history": {} if isinstance(history, dict) else []}
    required = packet.get("required_case_ids")
    if required is not None:
        selected["required_case_ids"] = list(required)
        selected["rotating_overview"] = deepcopy(packet["rotating_overview"])
    if "contract_failure" in packet:
        selected["contract_failure"] = packet["contract_failure"]
    else:
        selected.update(current_rotating_feedback=[], trajectories={role: [] for role in packet["trajectories"]})
    trace = {"protocol": "whole-development-reflection-cases-v1", "full_packet_sha256": value_sha256(packet),
             "cases": [], "history": [], "output_budget": output_budget, "input_limit": input_limit}

    def measure(candidate):
        prompt = prefix + json.dumps(candidate, ensure_ascii=False)
        tokens = count_chat_tokens([{"role": "user", "content": prompt}])
        return prompt, tokens, (tokens["input_tokens"] <= input_limit and
                              tokens["input_tokens"] < tokens["max_model_len"])

    if not measure(selected)[2]:
        raise ValueError("Complete parent source and mandatory reflection instructions exceed token budget")
    # Original MSTAR uses underperforming validation cases. All scores still
    # determine fitness; this only bounds the reflector's diagnostic input.
    rows = sorted(packet.get("current_rotating_feedback", []), key=lambda row: (row["metrics"]["Paper."], row["paper_id"]))
    for row in rows:
        pid = row["paper_id"]
        trial = deepcopy(selected)
        trial["current_rotating_feedback"].append(row)
        for role, reviews in packet["trajectories"].items():
            matches = [review for review in reviews if review["paper_id"] == pid]
            if len(matches) != 1:
                raise ValueError(f"Reflection requires one complete {role} trajectory for {pid}")
            trial["trajectories"][role].extend(matches)
        fits = measure(trial)[2]
        if required is not None and not fits:
            raise ValueError("Both preselected complete reflection cases must fit; cannot silently replace or drop a case")
        trace["cases"].append({"paper_id": pid, "selected": fits, "case_sha256": value_sha256(row)})
        if fits:
            selected = trial
    if rows and not selected["current_rotating_feedback"]:
        raise ValueError("No complete development feedback case fits the reflection token budget")
    history_items = history.items() if isinstance(history, dict) else enumerate(history)
    for key, item in history_items:
        trial = deepcopy(selected)
        if isinstance(history, dict):
            trial["history"][key] = item
        else:
            trial["history"].append(item)
        fits = measure(trial)[2]
        trace["history"].append({"key": key, "sha256": value_sha256(item), "selected": fits})
        if fits:
            selected = trial
    prompt, tokens, _ = measure(selected)
    trace["tokens"] = tokens
    trace["effective_max_tokens"] = min(output_budget, tokens["max_model_len"] - tokens["input_tokens"])
    trace["selected_packet_sha256"] = value_sha256(selected)
    return prompt, selected, trace
