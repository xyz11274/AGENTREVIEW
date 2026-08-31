#!/usr/bin/env bash

# Post-Corpus execution-only diagnostic. This never freezes MAIN_FORMAL_RUNTIME_PROFILE
# and never starts Evolution or Official evaluation.

source /data/xyj/envs/env_shengao/bin/activate
source /data/xyj/envs/qwen38serve/bin/activate
set -eo pipefail

REPO=/data/xyj/shengao/AgentReview_0811/AgentReview
OUT=/data/xyj/shengao/AgentReview_0811/artifacts/core_v1_preformal_20260830/postcorpus_capacity_diagnostic_r1
SUPERVISOR_STATUS=/data/xyj/shengao/AgentReview_0811/artifacts/core_v1_preformal_20260830/corpus_supervisor_review_request_r6/status.json
PROJECTION=/data/xyj/shengao/AgentReview_0811/artifacts/core_v1_preformal_20260830/corpus_projection_review_request_r6
WORKLOAD="$OUT/workload.jsonl"
EARLIEST_START_EPOCH=$(date -d '2026-08-31 17:40:00 +0800' +%s)
RESOURCE_WAIT_DEADLINE=$((EARLIEST_START_EPOCH + 43200))
ACTIVE_SESSION=

log() {
  printf '%s %s\n' "$(date -Is)" "$*"
}

write_status() {
  local state=$1
  local detail=${2:-}
  /data/xyj/envs/env_shengao/bin/python - "$OUT/status.json" "$state" "$detail" <<'PY'
import json, os, sys, tempfile
from datetime import datetime
from pathlib import Path
path = Path(sys.argv[1])
value = {
    "schema_version": "postcorpus-capacity-diagnostic-status-v1",
    "diagnostic_only": True,
    "state": sys.argv[2],
    "detail": sys.argv[3],
    "updated_at": datetime.now().astimezone().isoformat(),
    "main_formal_runtime_profile_frozen": False,
    "formal_started": False,
}
path.parent.mkdir(parents=True, exist_ok=True)
fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}

stop_active_qwen() {
  if [[ -n "$ACTIVE_SESSION" ]] && tmux has-session -t "$ACTIVE_SESSION" 2>/dev/null; then
    log "requesting graceful shutdown for $ACTIVE_SESSION"
    tmux send-keys -t "$ACTIVE_SESSION":0 C-c
    for _ in $(seq 1 180); do
      tmux has-session -t "$ACTIVE_SESSION" 2>/dev/null || break
      sleep 1
    done
    if tmux has-session -t "$ACTIVE_SESSION" 2>/dev/null; then
      log "ERROR: $ACTIVE_SESSION did not stop after SIGINT"
      return 1
    fi
  fi
  ACTIVE_SESSION=
}

trap 'stop_active_qwen || true' EXIT INT TERM

write_status SCHEDULED "earliest_start=2026-08-31T17:40:00+08:00"
log "scheduled; earliest start is 2026-08-31T17:40:00+08:00"
while (( $(date +%s) < EARLIEST_START_EPOCH )); do
  sleep 30
done

write_status WAITING_FOR_CORPUS_AND_PROJECTION ""
while true; do
  if [[ ! -f "$SUPERVISOR_STATUS" ]]; then
    write_status BLOCKED "supervisor status missing"
    log "BLOCKED: supervisor status missing"
    exit 10
  fi
  state=$(/data/xyj/envs/env_shengao/bin/python -c 'import json,sys; print(json.load(open(sys.argv[1]))["state"])' "$SUPERVISOR_STATUS")
  case "$state" in
    CORPUS_AND_PROJECTION_READY)
      [[ -f "$PROJECTION/manifest.json" ]] || {
        write_status BLOCKED "projection manifest missing"
        log "BLOCKED: projection manifest missing"
        exit 11
      }
      break
      ;;
    WAITING_FOR_CORPUS_BUILD)
      log "Corpus is still running; waiting for actual completion"
      sleep 60
      ;;
    *)
      write_status BLOCKED "supervisor terminal state=$state"
      log "BLOCKED: supervisor terminal state=$state"
      exit 12
      ;;
  esac
done

# This diagnostic only uses the Qwen TP island on GPU0-3. Processes on GPU4-7
# are outside its execution scope and do not block it. The watcher never kills
# another process; it waits for the target island and then stops blocked.
write_status WAITING_FOR_TARGET_GPU_ISLAND "GPU0-3"
while true; do
  compute=$(nvidia-smi -i 0,1,2,3 --query-compute-apps=pid,process_name --format=csv,noheader,nounits 2>/dev/null || true)
  if [[ -z "${compute//[[:space:]]/}" ]]; then
    break
  fi
  if (( $(date +%s) >= RESOURCE_WAIT_DEADLINE )); then
    printf '%s\n' "$compute" > "$OUT/compute_processes_at_resource_timeout.txt"
    write_status BLOCKED "target GPU0-3 island unavailable"
    log "BLOCKED: target GPU0-3 island unavailable"
    exit 13
  fi
  log "GPU0-3 compute processes still present; waiting without killing them"
  sleep 300
done

[[ -f "$WORKLOAD" ]] || {
  write_status BLOCKED "frozen diagnostic workload missing"
  exit 14
}
write_status RUNNING "fresh TP4 capacities 1,2,4; three repetitions each"

cd "$REPO"
for capacity in 1 2 4; do
  ACTIVE_SESSION="core_v1_postcorpus_capacity_c${capacity}"
  if tmux has-session -t "$ACTIVE_SESSION" 2>/dev/null; then
    write_status BLOCKED "tmux session already exists: $ACTIVE_SESSION"
    exit 15
  fi
  runtime_dir="$OUT/capacity_${capacity}"
  mkdir -p "$runtime_dir"
  nvidia-smi > "$runtime_dir/nvidia_smi_before_launch.txt"
  launch="source /data/xyj/envs/env_shengao/bin/activate && source /data/xyj/envs/qwen38serve/bin/activate && export CUDA_VISIBLE_DEVICES=0,1,2,3 && exec python $REPO/scripts/numa_exec.py --cpus 0-35,72-107 --memory-node 0 -- vllm serve /data/xyj/models/Qwen3.8-27B --served-model-name qwen3.8-27b --host 127.0.0.1 --port 8000 --tensor-parallel-size 4 --dtype bfloat16 --max-model-len 32768 --max-num-seqs $capacity --gpu-memory-utilization 0.939 --language-model-only --reasoning-parser qwen3 --enable-prefix-caching --disable-custom-all-reduce 2>&1 | tee $runtime_dir/qwen_tp4.log"
  printf '%s\n' "$launch" > "$runtime_dir/launch_command.txt"
  tmux new-session -d -s "$ACTIVE_SESSION" bash -lc "$launch"
  log "launched fresh capacity=$capacity session=$ACTIVE_SESSION"

  healthy=0
  for _ in $(seq 1 180); do
    if curl -fsS --max-time 5 http://127.0.0.1:8000/v1/models > "$runtime_dir/models_response.json" 2>/dev/null; then
      healthy=1
      break
    fi
    tmux has-session -t "$ACTIVE_SESSION" 2>/dev/null || break
    sleep 5
  done
  if [[ "$healthy" != 1 ]]; then
    write_status FAILED "capacity=$capacity service health preflight failed"
    log "FAILED: capacity=$capacity service health preflight failed"
    exit 20
  fi
  nvidia-smi > "$runtime_dir/nvidia_smi_after_load.txt"

  monitor="$runtime_dir/gpu_monitor.csv"
  (
    while true; do
      printf '%s,' "$(date -Is)"
      nvidia-smi --query-gpu=index,memory.used,memory.free,utilization.gpu,power.draw,temperature.gpu --format=csv,noheader,nounits | paste -sd ';' -
      sleep 1
    done
  ) >> "$monitor" 2>&1 &
  monitor_pid=$!

  for repetition in 0 1 2; do
    log "benchmark capacity=$capacity repetition=$repetition"
    if ! /data/xyj/envs/env_shengao/bin/python scripts/benchmark_core_v1_qwen_capacity.py \
      --endpoint http://127.0.0.1:8000/v1 \
      --model qwen3.8-27b \
      --workload "$WORKLOAD" \
      --max-num-seqs "$capacity" \
      --repetition "$repetition" \
      --timeout 600 \
      --output "$runtime_dir/sample_${repetition}.json"; then
      kill "$monitor_pid" 2>/dev/null || true
      wait "$monitor_pid" 2>/dev/null || true
      write_status FAILED "capacity=$capacity repetition=$repetition benchmark failed"
      exit 21
    fi
  done
  kill "$monitor_pid" 2>/dev/null || true
  wait "$monitor_pid" 2>/dev/null || true
  stop_active_qwen
  sleep 10
done

samples=()
for capacity in 1 2 4; do
  for repetition in 0 1 2; do
    samples+=("$OUT/capacity_${capacity}/sample_${repetition}.json")
  done
done
PYTHONPATH="$REPO" /data/xyj/envs/env_shengao/bin/python scripts/select_core_v1_capacity.py \
  "${samples[@]}" --output "$OUT/diagnostic_selection.json"

/data/xyj/envs/env_shengao/bin/python - "$OUT" <<'PY'
import json, statistics, sys
from pathlib import Path
root = Path(sys.argv[1])
summary = {
    "schema_version": "postcorpus-capacity-diagnostic-summary-v1",
    "diagnostic_only": True,
    "main_formal_runtime_profile_frozen": False,
    "formal_started": False,
    "capacities": {},
}
for capacity in (1, 2, 4):
    samples = [json.loads((root / f"capacity_{capacity}" / f"sample_{i}.json").read_text()) for i in range(3)]
    throughputs = [s["completed_requests"] / s["elapsed_seconds"] for s in samples]
    output_stable = all(
        (s["canonical_output_sha256"], s["finish_reasons"], s["protocol_dispositions"])
        == (samples[0]["canonical_output_sha256"], samples[0]["finish_reasons"], samples[0]["protocol_dispositions"])
        for s in samples[1:]
    )
    summary["capacities"][str(capacity)] = {
        "median_requests_per_second": statistics.median(throughputs),
        "median_p95_latency_seconds": statistics.median(s["p95_latency_seconds"] for s in samples),
        "min_tp_gpu_free_mib": min(v for s in samples for v in s["tp_gpu_free_mib"]),
        "within_capacity_output_stable": output_stable,
        "oom_count": sum(s["oom_count"] for s in samples),
        "schema_failure_count": sum(s["schema_failure_count"] for s in samples),
        "protocol_failure_count": sum(s["protocol_failure_count"] for s in samples),
    }
summary["diagnostic_selection"] = json.loads((root / "diagnostic_selection.json").read_text())
(root / "diagnostic_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
PY

write_status COMPLETED "diagnostic results ready; MAIN profile remains unfrozen"
log "post-Corpus capacity diagnostic completed"
