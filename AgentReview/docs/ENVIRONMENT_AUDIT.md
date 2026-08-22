# AgentReview 最小可运行 Python 环境审计

审计日期：2026-08-22  
审计仓库：`AgentReview_0811/AgentReview`  
目标环境：全新 Ubuntu NVIDIA GPU 服务器

## 结论

正式 AgentReview、M★ memory evolution、effectiveness screening、CoCoReviewBench 适配器与 Qwen3-Embedding-8B 预计算共需要 **11 个直接 Python 依赖**。可视化、文档生成、开发测试、旧 provider、历史数据获取和 CoCo 全文本指标共识别 **20 个可选直接依赖**。

推荐环境为 **Python 3.12 最新补丁版本 + PyTorch 2.7.1 CUDA 12.6 wheel**。该组合与本仓当前通过测试和真实 Qwen embedding smoke 的环境一致。CoCoReviewBench 的 README 也声明 Python 3.12+。PyTorch 官方提供 `2.7.1` 的 `cu126` wheel：<https://pytorch.org/get-started/previous-versions/>。

Qwen3-Embedding-8B 官方模型卡要求 `transformers>=4.51.0`、`sentence-transformers>=2.7.0`，模型规格为 32K 和 4096 维：<https://huggingface.co/Qwen/Qwen3-Embedding-8B>。本清单固定为仓内已验证的 Transformers 4.57.1 与 Sentence Transformers 5.1.2。

## 审计范围与方法

- 扫描 Git 顶层及 `AgentReview/` 文件系统中的 245 个 `.py` 文件，包括顶层 `build_pptx.py`、`agentreview/`、`memory_evolution/`、`effectiveness_screening/`、`evolution_viewer/`、`scripts/`、`tests/`、正式入口、历史 `runs/` 和 `data/` 中保存的 MemoryProgram。
- 扫描 5 个 shell 脚本、`requirements.txt`、`evolution_viewer/requirements.txt` 和 `setup.py`。仓库中没有 `pyproject.toml`、`environment.yml` 或其他 `requirements*.txt`。
- 使用 AST 收集 `import` / `from ... import ...`，再人工核对函数内延迟 import、`try/except ImportError`、`importlib.import_module`、`importlib.util.spec_from_file_location`、`find_spec`、shell 的 `python -m` 和 Python `subprocess`。
- 核对仓外 `/data0/xyj/shengao/CoCoReviewBench` 中由 AgentReview 实际动态加载的 `_2_split.py`、`_3_classify.py`、`_4_evaluation.py`、`_0_human_review.py`、`src/utils.py` 与 `prompt_registry.py`。CoCo 服务训练、benchmark 重建和 vLLM 服务端依赖保持独立环境。
- 没有使用 `pip freeze`。当前环境版本只用于确认已验证基线，不作为依赖发现来源。

发现一个历史生成文件 `runs/pilot_full_iter5_202607300117/ORIG/seed_0/pool/program_004_child.py` 在第 136 行存在语法错误。正式程序不导入该历史 pool 文件，因此它不影响环境依赖集合。

## A. Python 标准库

仓库实际引用的标准库顶层模块为：

`__future__`, `abc`, `argparse`, `ast`, `builtins`, `collections`, `concurrent`, `contextlib`, `copy`, `csv`, `dataclasses`, `datetime`, `difflib`, `gc`, `glob`, `hashlib`, `heapq`, `html`, `importlib`, `io`, `itertools`, `json`, `logging`, `math`, `multiprocessing`, `os`, `pathlib`, `random`, `re`, `resource`, `shlex`, `shutil`, `signal`, `socket`, `sqlite3`, `ssl`, `statistics`, `struct`, `subprocess`, `sys`, `tempfile`, `textwrap`, `threading`, `time`, `traceback`, `types`, `typing`, `unittest`, `urllib`, `uuid`, `warnings`, `xml`。

代码使用 `X | None`、`list[str]`、`tuple[...]` 和 `Path.is_relative_to()`，因此语法下限为 Python 3.10。正式迁移采用 Python 3.12，以匹配当前实测基线和 CoCoReviewBench 声明。

## B. 仓库内部模块

内部顶层包/入口包括：

- `agentreview`：正式 review runtime、OpenAI-compatible backend、Markdown paper context、memory library。
- `memory_evolution`：M★ 搜索、sandbox、CoCo adapter、Qwen embedding、selection、formal protocol。
- `effectiveness_screening`：冻结设计、运行矩阵、proxy evaluation 与汇总。
- `evolution_viewer` 与 `viewer.py`：可选 Gradio 可视化。
- `scripts`：temporal/S2 历史构建工具、formal readiness、embedding prewarm。
- `run_single_paper_review_cli.py`：正式 promoted-program 推理入口。

历史 `runs/**/program_*.py` 和 `data/effectiveness_screening_v1/programs/**` 是保存的 MemoryProgram 资产，仅使用 sandbox 允许的标准库，不新增正式第三方依赖。

## C. 正式运行直接依赖

下表与 `requirements-runtime.txt` 一一对应。每项都具有直接源码证据；传递依赖由 pip 解析。

| pip 包 | 直接 import | 正式用途与源码证据 |
|---|---|---|
| `httpx==0.28.1` | `httpx` | OpenAI-compatible 请求异常和 timeout：`agentreview/backends/openai.py:10` |
| `numpy==2.2.6` | `numpy` | Qwen float32 归一化与 prewarm 审计：`memory_evolution/embedding_adapter.py:657`、`scripts/prewarm_embedding_cache.py:159`；正式 review 启动还加载 `agentreview/role_descriptions.py:6` 与 `agentreview/utility/utils.py:11` |
| `openai==1.109.1` | `openai` | 正式 LLM client：`agentreview/backends/openai.py:17`、`agentreview/utility/authentication_utils.py:6`；CoCo ReviewSplit/Classify client：`memory_evolution/coco_adapter.py:13` |
| `packaging==25.0` | `packaging` | Qwen runtime 最低版本检查：`memory_evolution/embedding_adapter.py:516` |
| `pandas==2.2.3` | `pandas` | `run_single_paper_review_cli.py` 调用 `project_setup()`，其模块顶层和函数均加载 pandas：`agentreview/utility/utils.py:12,94` |
| `requests==2.32.5` | `requests` | effectiveness/S2 数据处理：`effectiveness_screening/prepare.py:588`、`scripts/build_frozen_s2_pool.py:16`；CoCo 动态加载的 `src/utils.py` 也在模块顶层引用 requests |
| `sentence-transformers==5.1.2` | `sentence_transformers` | Qwen3-Embedding-8B model-native encode：`memory_evolution/embedding_adapter.py:553` |
| `tenacity==9.1.2` | `tenacity` | Agent 基类与 OpenAI retry：`agentreview/agent.py:9`、`agentreview/backends/openai.py:11` |
| `torch==2.7.1` | `torch` | Qwen BF16/CUDA/inference_mode：`memory_evolution/embedding_adapter.py:552,655`、`scripts/prewarm_embedding_cache.py:119` |
| `tqdm==4.67.1` | `tqdm` | AgentReview 动态导入的 CoCo `_2_split.py` 和 `_4_evaluation.py` 在模块顶层引用 tqdm |
| `transformers==4.57.1` | `transformers` | Qwen tokenizer、legacy BGE/HF backend：`memory_evolution/embedding_adapter.py:424,625`、`agentreview/backends/hf_transformers.py:34` |

### 关键链路覆盖

| 链路 | 直接依赖 |
|---|---|
| AgentReview 正式运行 | `openai`, `httpx`, `tenacity`, `numpy`, `pandas` |
| memory evolution | 上述 API 依赖；Qwen selection 再使用 `torch`, `transformers`, `sentence-transformers`, `packaging`, `numpy` |
| CoCo adapter / evaluator reference | `openai`, `requests`, `numpy`, `tqdm`；ReviewSplit/Classify 模型由外部 OpenAI-compatible vLLM 服务提供 |
| Qwen3-Embedding-8B | `torch`, `transformers`, `sentence-transformers`, `numpy`, `packaging` |
| frozen Semantic Scholar 数据 | JSON/SQLite 等标准库；只有重新获取 S2 时使用 `requests` |
| OpenReview formal 数据处理 | 已下载 JSON/Markdown 由标准库读取；重新抓取 OpenReview 时使用可选 `openreview-py` |
| 本地模型加载 | Qwen embedding 使用 Transformers/Sentence Transformers；AgentReview LLM 和 CoCo Split/Classify 均走 HTTP client |

## D. 可选依赖

`requirements-optional.txt` 是按功能选择安装的清单，适合逐段选取，不作为正式环境的整体安装输入。

| 功能 | 可选直接依赖 | 源码证据 |
|---|---|---|
| 旧 provider adapters | `anthropic`, `bardapi`, `cohere`, `langchain`, `supabase` | `agentreview/backends/{anthropic,bard,cohere,langchain}.py`、`agentreview/database.py` |
| 交互 CLI | `colorama`, `prompt-toolkit`, `rich` | `agentreview/ui/cli.py`、`agentreview/utility/text_utils.py` |
| Viewer | `gradio>=5,<6` | `viewer.py`、`evolution_viewer/app.py` |
| 技术汇报 PPT 生成 | `python-pptx` | Git 顶层 `build_pptx.py` |
| PDF/OpenReview 获取 | `pdfminer.six`, `openreview-py` | `agentreview/paper_processor.py`、`agentreview/dataset/download_openreview_paper.py` |
| OpenScholar 搜索 helper | `beautifulsoup4`, `jsonlines` | `agentreview/openscholar_bridge/src/use_search_apis.py`、`src/utils.py` |
| CoCo 全文本附加指标 | `nltk`, `rouge-score`, `bert-score` | 仓外 CoCo `_4_evaluation.py:431-437` 的函数内 import；正式 M★ `coco_review` fitness 不调用该函数 |
| 开发/测试 | `pytest`, `ruff` | 测试目录与本次仓库校验命令；生产入口不导入 |
| Qwen 加速 | `flash-attn==2.8.3` | `memory_evolution/embedding_adapter.py:564` 使用 `find_spec("flash_attn")` 后优先选择 `flash_attention_2` |

FlashAttention 是 CUDA 扩展。官方要求 Linux、PyTorch 2.2+、CUDA 12+，并支持 RTX 4090 的 Ada 架构：<https://github.com/Dao-AILab/flash-attention>。安装顺序固定为 CUDA PyTorch wheel → build tools → `flash-attn --no-build-isolation`。

## E. 排除项与历史环境残留

| 包/组件 | 审计结论 |
|---|---|
| `faiss` / `faiss-cpu` | 仓库没有有效 import 或动态加载；当前环境即使存在也属于残留。subset selection 使用仓内纯 Python cosine/facility-location。 |
| `scipy`, `scikit-learn` | 仓库没有直接 import。Sentence Transformers 会自行解析它们的传递依赖，因此不手工写入直接 requirements。正式 selection 没有调用 sklearn KMeans。 |
| `pydantic` | 仓库没有直接 import；OpenAI client 等包可将其作为传递依赖安装。 |
| `PyYAML` / `yaml` | 仓库没有 import；配置格式为 JSON/JSONL。 |
| `jsonlines` | 只用于可选 OpenScholar helper，正式链路使用标准库逐行 JSON。 |
| `torchvision` | Qwen adapter 仅执行兼容性探测并支持包缺席。文本正式环境省略 torchvision；这也消除 torch/torchvision 二进制错配风险。 |
| `vllm` | AgentReview 通过 `openai` client 访问外部 vLLM endpoints，主环境不导入 vLLM。ReviewSplit、ReviewClassify 和其他本地生成服务使用独立服务环境。 |
| `datasets` | AgentReview 仓内没有 import；它属于仓外 CoCo 完整 pipeline/service requirements。 |
| `setuptools` | 只由 `setup.py` 引用，属于构建工具。当前 `setup.py` 仅声明 name/version，没有 `install_requires`，不能作为环境依赖真相来源。 |
| `FlagEmbedding`, `flask`, `spacy` | 只出现在 `openscholar_api.py` / `open_scholar.py` 的整段三引号禁用文本中，不属于可执行 import。 |

## 动态和条件 import 审计

- `memory_evolution/cli.py` 与 `run_single_paper_review_cli.py` 通过 `module:factory` 动态载入 embedding/classifier adapter。formal 默认解析到仓内 `memory_evolution.embedding_adapter:create_qwen3_embedding` 与 `memory_evolution.coco_adapter:create_frozen_coco_adapter`，对应依赖已进入 runtime。
- `memory_evolution/coco_adapter.py` 动态导入仓外 CoCo `src.evaluation._2_split`、`_3_classify`、`src.utils`、`src.benchmark.prompt_registry`。顶层实际需要 `openai`, `requests`, `tqdm`。
- `memory_evolution/coco_reference.py` 通过文件路径载入 CoCo `_0_human_review.py` 与 `_4_evaluation.py`。模块顶层实际需要 `numpy`, `tqdm`, `openai`, `requests`；`nltk`, `rouge-score`, `bert-score` 仅在调用全文本附加指标函数时导入。
- Qwen 相关 `torch`, `transformers`, `sentence_transformers`, `numpy`, `packaging` 均为函数内延迟 import；formal embedding 实际执行时全部需要。
- `torchvision` 位于容错探测中；`flash_attn` 使用 `find_spec` 探测。前者省略，后者按 GPU 需求单独安装。
- Anthropic/Bard/Cohere/LangChain/Supabase 使用 `try/except import`，正式 OpenAI-compatible backend 不加载这些模块。

## Shell、subprocess 和系统级依赖

以下项目属于系统包或外部服务，不进入 pip requirements：

| 系统依赖 | 必需性 | 证据/用途 |
|---|---|---|
| `bash` | 正式 shell 入口必需 | `run.sh`, `pilot_full.sh`, `QUAL_test.sh` |
| `bubblewrap` (`bwrap`) | formal evolution 必需 | `memory_evolution/sandbox.py` 默认 `require_bubblewrap=True`，隔离 Candidate 进程 |
| `git` | effectiveness freeze 与 pinned CoCo checkout 必需 | `effectiveness_screening/run_matrix.py:174` 执行 `git rev-parse HEAD` |
| `curl` | viewer 健康检查和 API preflight 操作需要 | `evolution_viewer/manage.sh` |
| `rsync` | 迁移模型/cache 的推荐工具 | 仓内 Python 不调用；迁移时保留 Hugging Face snapshot symlink/blob 结构 |
| `iproute2` (`ss`) | viewer 端口检查可选 | `evolution_viewer/manage.sh` |
| `procps`, `coreutils` | viewer 后台管理可选 | `ps`, `nohup`, `tail`, `hostname`, `kill` |
| NVIDIA driver | GPU 正式环境必需 | 需要支持 CUDA 12.6 runtime；PyTorch wheel 自带 CUDA 用户态 runtime |
| CUDA Toolkit / `nvcc` | 仅源码编译 FlashAttention 时需要 | 普通 PyTorch cu126 wheel 和 SDPA 路径只需要兼容 NVIDIA driver |
| `ninja`, C/C++ toolchain, Python headers | 仅 FlashAttention 源码编译需要 | `ninja-build`, `build-essential` 和 Python/conda headers |
| vLLM services | CoCo live classifier/splitter 需要 | 独立环境监听 13099/13098；AgentReview 主环境只使用 HTTP |

shell 还发现 `end2end_smoke.sh` 含硬编码 API credential。迁移前执行凭据轮换，并从迁移包与后续日志中排除该值。

## 推荐新服务器安装流程

下面使用全新 Miniforge 环境，不复制旧 conda prefix。Ubuntu 系统包命令为：

```bash
sudo apt-get update
sudo apt-get install -y \
  bash bubblewrap ca-certificates curl git rsync \
  build-essential ninja-build iproute2 procps
```

创建干净 Python 3.12 环境：

```bash
curl -L -o /tmp/Miniforge3.sh \
  https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh
bash /tmp/Miniforge3.sh -b -p "$HOME/miniforge3"
source "$HOME/miniforge3/etc/profile.d/conda.sh"
conda create -n agentreview312 -y python=3.12 pip
conda activate agentreview312
python -m pip install --upgrade pip setuptools wheel
```

先从 PyTorch 官方 CUDA 12.6 index 安装 GPU wheel，再安装仓内 runtime 直接依赖：

```bash
cd /path/to/AgentReview_0811/AgentReview

python -m pip install torch==2.7.1 \
  --index-url https://download.pytorch.org/whl/cu126
python -m pip install -r requirements-runtime.txt
python -m pip check
```

验证核心 import、CUDA 与 BF16：

```bash
python - <<'PY'
import httpx
import numpy
import openai
import packaging
import pandas
import requests
import sentence_transformers
import tenacity
import torch
import tqdm
import transformers

print("torch:", torch.__version__)
print("torch CUDA runtime:", torch.version.cuda)
print("CUDA available:", torch.cuda.is_available())
print("BF16 supported:", torch.cuda.is_bf16_supported())
print("transformers:", transformers.__version__)
print("sentence-transformers:", sentence_transformers.__version__)
PY

bwrap --unshare-all --ro-bind / / --proc /proc --dev /dev true
python -m memory_evolution.cli --help
python run_single_paper_review_cli.py --help
```

在 RTX 4090 24GB 上运行 32K Qwen embedding 时推荐 FlashAttention。安装动作放在 torch 之后：

```bash
python -m pip install psutil ninja packaging
MAX_JOBS=4 python -m pip install flash-attn==2.8.3 --no-build-isolation
python -c 'import flash_attn; print(flash_attn.__version__)'
```

FlashAttention 编译使用与 PyTorch `torch.version.cuda` 同一 CUDA 12.x 系列的 toolkit。预编译 wheel 可用时不需要本地 `nvcc`；源码构建时安装 NVIDIA 官方 CUDA 12.6 toolkit。

Viewer 或测试采用选择式安装：

```bash
# Viewer
python -m pip install 'gradio>=5,<6'

# Test/lint
python -m pip install pytest ruff
python -m pytest -q
```

CoCo ReviewSplit/ReviewClassify vLLM 服务使用单独环境。服务环境按 pinned CoCo checkout 自带 `requirements.txt` 和服务脚本建立；AgentReview 环境只访问 `http://localhost:13099/v1` 与 `http://localhost:13098/v1`。这种分离保留 AgentReview 的 `torch==2.7.1+cu126`，同时允许 vLLM 使用自身要求的 torch/CUDA 组合。

## 主要版本兼容风险

1. **torch/torchvision 二进制配对。** 当前旧环境实际为 torch 2.7.1 与 torchvision 0.24.0，导入 torchvision 会触发缺失 `torchvision::nms` 的运行时错误。PyTorch 官方给 torch 2.7.1 配套 torchvision 0.22.1。文本主环境直接省略 torchvision；确有视觉需求时安装 `torchvision==0.22.1` 的同一 cu126 wheel。
2. **FlashAttention ABI。** wheel/编译产物绑定 Python、torch、CUDA 和 C++ ABI。安装 torch 后再安装 FlashAttention，并在任何 torch/CUDA 变化后重装 FlashAttention。24GB 4090 的 32K 输入优先使用 FlashAttention；代码可回退 SDPA/eager。
3. **vLLM 与 torch 约束。** CoCo 仓的 vLLM pin 可能要求另一 torch 版本。vLLM 服务环境与 AgentReview 主环境保持分离。
4. **OpenAI SDK 双版本。** AgentReview 当前通过测试的是 `openai==1.109.1`，仓外 CoCo 完整 requirements 固定 `openai==2.6.0`。AgentReview 动态使用的 CoCo helper 只依赖稳定的 `OpenAI`/`AsyncOpenAI` 接口；完整 CoCo pipeline 继续使用自己的环境。
5. **旧 provider SDK。** Anthropic completion API、`langchain.llms.OpenAI`、旧 Cohere chat 与 Bard adapter 面向历史 SDK。它们属于可选兼容代码，正式 OpenAI-compatible 链路无需安装；启用前需要逐个锁定历史 SDK 或更新 adapter。
6. **Python 下限与 CoCo 声明。** AgentReview 语法要求 Python 3.10+，CoCo README 声明 3.12+，统一选择 Python 3.12。
7. **NumPy 2.x 扩展 ABI。** runtime 的纯 Python/官方 wheels 已在 NumPy 2.2.6 上通过。新增本地编译扩展时使用支持 NumPy 2 的 wheel或在最终环境中重新编译。
8. **bubblewrap 权限。** 安装二进制之后还需要宿主允许 user namespaces；上面的 bwrap probe 是 formal evolution 的启动前门禁。
9. **模型与资产迁移。** Hugging Face snapshot 依赖 `snapshots/` 到 `blobs/` 的链接结构；复制完整 `models--Qwen--Qwen3-Embedding-8B/` 目录并保留链接。CoCo checkout 需要保留 `.git` 目录与固定 commit。

## requirements 逐项证据复核

`requirements-runtime.txt` 的 11 行均能映射到上文“正式运行直接依赖”表中的源码文件，没有加入源码未引用的包。特别结论如下：

- `faiss`, `scipy`, `scikit-learn`, `pydantic`, `PyYAML`, `jsonlines`, `gradio`, `vllm` 均未进入 runtime。
- `torch` 作为源码直接 import 保留在 runtime，并通过官方 cu126 index 单独安装 GPU wheel。
- `httpx` 虽然也是 OpenAI SDK 的传递依赖，仓内 `agentreview/backends/openai.py` 直接 import，因此保留。
- `packaging` 虽然通常是构建依赖，Qwen adapter 直接 import 并执行版本比较，因此保留。
- `pandas` 虽然主要用于旧 utility，正式 review 入口导入并调用该 utility 的 `project_setup()`，因此当前代码状态下属于直接 runtime。
- `requests` 和 `tqdm` 由 formal effectiveness/CoCo 动态链路直接需要，因此保留。
