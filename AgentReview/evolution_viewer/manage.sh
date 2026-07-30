#!/usr/bin/env bash
set -eu

EV_VIEWER_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
EV_PROJECT_DIR=$(CDPATH= cd -- "$EV_VIEWER_DIR/.." && pwd)
EV_RUNS_DIR=${EVOLUTION_VIEWER_RUNS_DIR:-"$EV_PROJECT_DIR/runs"}
EV_HOST=${EVOLUTION_VIEWER_HOST:-"0.0.0.0"}
EV_PORT=${EVOLUTION_VIEWER_PORT:-"7862"}
EV_STATE_DIR=${EVOLUTION_VIEWER_STATE_DIR:-"$EV_VIEWER_DIR/.runtime"}
EV_PID_FILE="$EV_STATE_DIR/viewer.pid"
EV_LOG_FILE="$EV_STATE_DIR/viewer.log"


find_python() {
    if [ -n "${EVOLUTION_VIEWER_PYTHON:-}" ]; then
        if [ ! -x "$EVOLUTION_VIEWER_PYTHON" ]; then
            echo "EVOLUTION_VIEWER_PYTHON 不可执行：$EVOLUTION_VIEWER_PYTHON" >&2
            return 1
        fi
        echo "$EVOLUTION_VIEWER_PYTHON"
        return 0
    fi

    for EV_CANDIDATE in \
        "/data0/xyj/miniconda3/envs/env_shengao/bin/python" \
        "$(command -v python3 2>/dev/null || true)" \
        "$(command -v python 2>/dev/null || true)"
    do
        if [ -n "$EV_CANDIDATE" ] &&
           [ -x "$EV_CANDIDATE" ] &&
           "$EV_CANDIDATE" -c "import gradio" >/dev/null 2>&1
        then
            echo "$EV_CANDIDATE"
            return 0
        fi
    done

    echo "没有找到已安装 Gradio 的 Python。" >&2
    echo "请安装 evolution_viewer/requirements.txt，或设置 EVOLUTION_VIEWER_PYTHON。" >&2
    return 1
}


read_pid() {
    if [ ! -f "$EV_PID_FILE" ]; then
        return 1
    fi
    EV_RECORDED_PID=$(sed -n '1p' "$EV_PID_FILE")
    case "$EV_RECORDED_PID" in
        ""|*[!0-9]*) return 1 ;;
    esac
    echo "$EV_RECORDED_PID"
}


is_running() {
    EV_CHECK_PID=$(read_pid 2>/dev/null || true)
    [ -n "$EV_CHECK_PID" ] && kill -0 "$EV_CHECK_PID" 2>/dev/null
}


show_addresses() {
    echo "本机访问：http://127.0.0.1:$EV_PORT"
    if [ "$EV_HOST" = "0.0.0.0" ]; then
        for EV_ADDRESS in $(hostname -I 2>/dev/null || true); do
            case "$EV_ADDRESS" in
                127.*|::1) ;;
                *) echo "网络访问：http://$EV_ADDRESS:$EV_PORT" ;;
            esac
        done
    else
        echo "监听地址：http://$EV_HOST:$EV_PORT"
    fi
}


start_viewer() {
    mkdir -p "$EV_STATE_DIR"
    if is_running; then
        echo "Viewer 已在运行，PID=$(read_pid)"
        show_addresses
        return 0
    fi
    if command -v ss >/dev/null 2>&1 &&
       ss -ltn | awk '{print $4}' | grep -Eq "[:.]${EV_PORT}$"
    then
        echo "端口 $EV_PORT 已被其他进程占用。请先停止该进程，或设置：" >&2
        echo "EVOLUTION_VIEWER_PORT=其他端口 ./evolution_viewer/manage.sh start" >&2
        return 1
    fi

    EV_PYTHON=$(find_python)
    (
        cd "$EV_PROJECT_DIR"
        NO_PROXY=127.0.0.1,localhost \
        no_proxy=127.0.0.1,localhost \
        nohup "$EV_PYTHON" -m evolution_viewer.app \
            --runs-dir "$EV_RUNS_DIR" \
            --host "$EV_HOST" \
            --port "$EV_PORT" \
            >"$EV_LOG_FILE" 2>&1 &
        echo "$!" >"$EV_PID_FILE"
    )

    EV_STARTED_PID=$(read_pid)
    EV_ATTEMPT=0
    while [ "$EV_ATTEMPT" -lt 12 ]; do
        if ! kill -0 "$EV_STARTED_PID" 2>/dev/null; then
            echo "Viewer 启动失败：" >&2
            tail -n 40 "$EV_LOG_FILE" >&2 || true
            return 1
        fi
        if curl --noproxy '*' -fsS --max-time 1 \
            "http://127.0.0.1:$EV_PORT/config" >/dev/null 2>&1
        then
            echo "Viewer 已启动，PID=$EV_STARTED_PID"
            show_addresses
            echo "日志：$EV_LOG_FILE"
            return 0
        fi
        EV_ATTEMPT=$((EV_ATTEMPT + 1))
        sleep 1
    done

    echo "进程仍在运行，但健康检查尚未通过。请查看日志：" >&2
    echo "$EV_LOG_FILE" >&2
    return 1
}


stop_viewer() {
    if ! is_running; then
        echo "Viewer 当前未由此脚本管理或已经停止。"
        return 0
    fi
    EV_STOP_PID=$(read_pid)
    EV_COMMAND=$(ps -p "$EV_STOP_PID" -o args= 2>/dev/null || true)
    case "$EV_COMMAND" in
        *evolution_viewer.app*) ;;
        *)
            echo "PID $EV_STOP_PID 不是 evolution_viewer.app，拒绝停止。" >&2
            return 1
            ;;
    esac
    kill "$EV_STOP_PID"
    EV_ATTEMPT=0
    while kill -0 "$EV_STOP_PID" 2>/dev/null && [ "$EV_ATTEMPT" -lt 10 ]; do
        EV_ATTEMPT=$((EV_ATTEMPT + 1))
        sleep 1
    done
    if kill -0 "$EV_STOP_PID" 2>/dev/null; then
        echo "进程未在 10 秒内退出，请检查 PID $EV_STOP_PID。" >&2
        return 1
    fi
    rm -f "$EV_PID_FILE"
    echo "Viewer 已停止。"
}


show_status() {
    if is_running; then
        echo "Viewer 正在运行，PID=$(read_pid)"
        show_addresses
        echo "日志：$EV_LOG_FILE"
    else
        echo "Viewer 未运行。"
        return 1
    fi
}


show_help() {
    cat <<'EOF'
用法：
  ./evolution_viewer/manage.sh start     后台启动
  ./evolution_viewer/manage.sh status    查看状态和访问地址
  ./evolution_viewer/manage.sh logs      持续查看日志（Ctrl+C 退出）
  ./evolution_viewer/manage.sh stop      停止脚本启动的 Viewer
  ./evolution_viewer/manage.sh restart   重启

可选环境变量：
  EVOLUTION_VIEWER_PYTHON
  EVOLUTION_VIEWER_RUNS_DIR
  EVOLUTION_VIEWER_HOST
  EVOLUTION_VIEWER_PORT
  EVOLUTION_VIEWER_STATE_DIR
EOF
}


case "${1:-help}" in
    start) start_viewer ;;
    stop) stop_viewer ;;
    restart)
        stop_viewer
        start_viewer
        ;;
    status) show_status ;;
    logs)
        mkdir -p "$EV_STATE_DIR"
        touch "$EV_LOG_FILE"
        tail -f "$EV_LOG_FILE"
        ;;
    help|-h|--help) show_help ;;
    *)
        echo "未知命令：$1" >&2
        show_help >&2
        exit 2
        ;;
esac

