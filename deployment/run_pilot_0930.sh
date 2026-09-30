#!/usr/bin/env bash
set -euo pipefail
source /data/xyj/.config/agentreview/0930.env
cd /data/xyj/shengao/AgentReview_0930
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4
export PYTHONPATH=/data/xyj/shengao/AgentReview_0930/AgentReview
if [ "$#" -eq 0 ]; then
    set -- status
fi
exec /data/xyj/envs/coco-original/bin/python -u -B deployment/pilot_0930.py "$@"
