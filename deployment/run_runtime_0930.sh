#!/usr/bin/env bash
set -euo pipefail
source /data/xyj/.config/agentreview/0930.env
cd /data/xyj/shengao/AgentReview_0930
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4
export PYTHONPATH=/data/xyj/shengao/AgentReview_0930/AgentReview
export PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}127.0.0.1,localhost,::1"
export no_proxy="$NO_PROXY"
action=${1:?Specify acceptance, small or formal}
shift
case "$action" in
    acceptance) entry=deployment/validate_evolution_runtime.py ;;
    small) entry=deployment/run_budgeted_0930.py ;;
    formal) entry=deployment/run_budgeted_0930.py ;;
    *) exit 2 ;;
esac
exec /data/xyj/envs/coco-original/bin/python -u -B "$entry" "$@"
