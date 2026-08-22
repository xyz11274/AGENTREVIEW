# -*- coding: utf-8 -*-
"""生成 AgentReview M★ 技术汇报 PPT（2026-08-20，基准 commit 7256ffc）。"""
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml.ns import qn

# ---------- 主题 ----------
NAVY = RGBColor(0x1B, 0x2A, 0x4A)
BLUE = RGBColor(0x2E, 0x6F, 0xDB)
TEAL = RGBColor(0x0E, 0x7C, 0x7B)
ORANGE = RGBColor(0xD9, 0x7A, 0x2B)
RED = RGBColor(0xB8, 0x3A, 0x3A)
GRAY_BG = RGBColor(0xF5, 0xF7, 0xFA)
GRAY_LINE = RGBColor(0xC9, 0xD2, 0xE0)
DARK = RGBColor(0x23, 0x28, 0x33)
MID = RGBColor(0x55, 0x5F, 0x6E)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
LIGHT_BLUE = RGBColor(0xE3, 0xEC, 0xFB)
LIGHT_TEAL = RGBColor(0xE0, 0xF2, 0xF1)
LIGHT_ORANGE = RGBColor(0xFB, 0xEF, 0xE0)

FONT = "Microsoft YaHei"

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
BLANK = prs.slide_layouts[6]
SW, SH = 13.333, 7.5


def _set_font(run, size, color=DARK, bold=False, name=FONT):
    f = run.font
    f.size = Pt(size)
    f.bold = bold
    f.color.rgb = color
    f.name = name
    # 中文字体
    rPr = run._r.get_or_add_rPr()
    ea = rPr.find(qn('a:ea'))
    if ea is None:
        ea = rPr.makeelement(qn('a:ea'), {})
        rPr.append(ea)
    ea.set('typeface', name)


def add_rect(slide, x, y, w, h, fill, line=None, shape=MSO_SHAPE.RECTANGLE, line_w=0.75):
    sp = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    sp.fill.solid()
    sp.fill.fore_color.rgb = fill
    if line is None:
        sp.line.fill.background()
    else:
        sp.line.color.rgb = line
        sp.line.width = Pt(line_w)
    sp.shadow.inherit = False
    return sp


def add_text(slide, x, y, w, h, runs, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
             space_after=4, line_spacing=1.0):
    """runs: list of paragraphs; each paragraph is list of (text, size, color, bold)."""
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    for i, para in enumerate(runs):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_after = Pt(space_after)
        p.line_spacing = line_spacing
        for (t, sz, c, b) in para:
            r = p.add_run()
            r.text = t
            _set_font(r, sz, c, b)
    return tb


def box(slide, x, y, w, h, title, sub=None, fill=WHITE, line=GRAY_LINE,
        title_color=NAVY, tsize=13, ssize=10, title_bold=True, shape=MSO_SHAPE.ROUNDED_RECTANGLE):
    sp = add_rect(slide, x, y, w, h, fill, line=line, shape=shape)
    tf = sp.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_left = Inches(0.06)
    tf.margin_right = Inches(0.06)
    tf.margin_top = Inches(0.02)
    tf.margin_bottom = Inches(0.02)
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run(); r.text = title
    _set_font(r, tsize, title_color, title_bold)
    if sub:
        for line_txt in (sub if isinstance(sub, list) else [sub]):
            p2 = tf.add_paragraph()
            p2.alignment = PP_ALIGN.CENTER
            r2 = p2.add_run(); r2.text = line_txt
            _set_font(r2, ssize, MID, False)
    return sp


def arrow(slide, x, y, w, h, direction="right", color=BLUE):
    shape = {"right": MSO_SHAPE.RIGHT_ARROW, "down": MSO_SHAPE.DOWN_ARROW,
             "left": MSO_SHAPE.LEFT_ARROW, "up": MSO_SHAPE.UP_ARROW}[direction]
    return add_rect(slide, x, y, w, h, color, shape=shape)


def content_slide(title, subtitle=None):
    s = prs.slides.add_slide(BLANK)
    add_rect(s, 0, 0, SW, SH, GRAY_BG)
    add_rect(s, 0, 0, SW, 0.92, NAVY)
    add_rect(s, 0, 0.92, SW, 0.045, BLUE)
    add_text(s, 0.55, 0.13, 10.5, 0.7, [[(title, 26, WHITE, True)]], anchor=MSO_ANCHOR.MIDDLE)
    if subtitle:
        add_text(s, 0.58, 1.06, 12.2, 0.4, [[(subtitle, 12.5, MID, False)]])
    return s


def bullets(slide, x, y, w, h, items, size=14, space_after=7, line_spacing=1.08):
    """items: list of (lead, rest) or (lead, rest, level)."""
    paras = []
    for it in items:
        lead, rest = it[0], it[1]
        lvl = it[2] if len(it) > 2 else 0
        prefix = "▪ " if lvl == 0 else "– "
        runs = [(prefix, size, BLUE if lvl == 0 else MID, True)]
        if lead:
            runs.append((lead, size, NAVY, True))
        if rest:
            runs.append((rest, size, DARK, False))
        paras.append(runs)
    tb = add_text(slide, x, y, w, h, paras, space_after=space_after, line_spacing=line_spacing)
    for i, it in enumerate(items):
        lvl = it[2] if len(it) > 2 else 0
        tb.text_frame.paragraphs[i].level = min(lvl, 4)
    return tb


def table_slide(slide, x, y, w, rows, col_widths, header_fill=NAVY, size=12, row_h=0.38):
    from pptx.util import Inches as I
    n_r, n_c = len(rows), len(rows[0])
    gt = slide.shapes.add_table(n_r, n_c, I(x), I(y), I(w), I(row_h * n_r)).table
    for j, cw in enumerate(col_widths):
        gt.columns[j].width = I(cw)
    for i, row in enumerate(rows):
        gt.rows[i].height = I(row_h)
        for j, val in enumerate(row):
            cell = gt.cell(i, j)
            cell.margin_top = I(0.02); cell.margin_bottom = I(0.02)
            tf = cell.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            r = p.add_run(); r.text = str(val)
            if i == 0:
                cell.fill.solid(); cell.fill.fore_color.rgb = header_fill
                _set_font(r, size, WHITE, True)
            else:
                cell.fill.solid()
                cell.fill.fore_color.rgb = WHITE if i % 2 else GRAY_BG
                _set_font(r, size, DARK, False)
    return gt


# =========================================================
# Slide 1 标题
# =========================================================
s = prs.slides.add_slide(BLANK)
add_rect(s, 0, 0, SW, SH, NAVY)
add_rect(s, 0, 4.9, SW, 0.05, BLUE)
add_text(s, 0.9, 1.5, 11.5, 1.9, [
    [("AgentReview M★", 48, WHITE, True)],
    [("Memory-Evolution Framework for Improving LLM-based Paper Review Agents", 20, RGBColor(0x9F, 0xB8, 0xE8), False)],
], space_after=10)
add_text(s, 0.9, 3.6, 11.5, 1.1, [
    [("让 Reviewer Agent 通过可执行 MemoryProgram 的自进化，积累历史审稿经验", 18, WHITE, False)],
    [("技术汇报 · 2026-08-20 · 基准 commit 7256ffc", 13, RGBColor(0x8A, 0x97, 0xB5), False)],
], space_after=8)
add_text(s, 0.9, 5.15, 11.5, 1.6, [
    [("关键词：", 13, RGBColor(0x8A, 0x97, 0xB5), True),
     ("MemoryProgram Evolution · Specialist Reviewer · 四区数据隔离 · CoCoReviewBench · BGE-M3 · 沙箱进化", 13, RGBColor(0xC7, 0xD3, 0xE8), False)],
])

# =========================================================
# Slide 2 背景与问题
# =========================================================
s = content_slide("背景：LLM Reviewer 缺的不是表达能力，是经验")
bullets(s, 0.6, 1.35, 7.3, 5.6, [
    ("现状：", "LLM Reviewer 能生成流畅的 review，但每篇论文都从零开始——没有长期经验积累。"),
    ("缺口一：", "缺少针对不同评审维度（质量/清晰度/重要性/原创性/规范性）的专业能力。"),
    ("缺口二：", "评审策略靠人手工设计 prompt / 固定 retrieval，系统本身无法自我改进。"),
    ("缺口三：", "自动 review 与 human reviewer 的评价在完整性、正确性上仍有可度量差距。"),
    ("本项目的回答：", "不手工调 prompt，而是让 Agent 自己进化「如何记取和使用审稿经验」。"),
], size=15)
box(s, 8.3, 1.5, 4.5, 5.2, "", fill=LIGHT_BLUE, line=BLUE)
add_text(s, 8.55, 1.7, 4.0, 4.9, [
    [("核心主张 M★", 16, NAVY, True)],
    [("给 Evolution 足够大的「如何成为一个好 Reviewer」的设计自由；", 13, DARK, False)],
    [("但锁定 Reviewer 的基本职责（Frozen Reviewer Identity）；", 13, DARK, False)],
    [("并把所有 Benchmark 留在系统最外层，作为独立裁判。", 13, DARK, False)],
    [("", 8, DARK, False)],
    [("Memory 是当前的可进化抓手，不是最终目的。", 13, TEAL, True)],
], space_after=8)

# =========================================================
# Slide 3 核心思想：MemoryProgram
# =========================================================
s = content_slide("核心思想：可执行的 MemoryProgram 是进化对象",
                  "每个审稿角色对应一个可执行 Python 程序，在沙箱中运行，由 Evolution 改写")
labels = [
    ("KnowledgeItem", "记什么：经验的字段与结构（Schema）", LIGHT_BLUE),
    ("KnowledgeBase.write", "怎么存：从历史论文自产经验中提取、压缩、写入", LIGHT_TEAL),
    ("KnowledgeBase.read", "怎么取：按当前论文检索、排序、组合经验", LIGHT_ORANGE),
    ("instruction", "怎么给：把记忆组织成 Reviewer 的上下文", LIGHT_BLUE),
]
for i, (t, d, c) in enumerate(labels):
    bx = 0.6 + i * 3.15
    box(s, bx, 1.75, 2.95, 1.5, t, d, fill=c, line=GRAY_LINE, tsize=14, ssize=10.5)
    if i < 3:
        arrow(s, bx + 2.96, 2.35, 0.2, 0.28, "right", BLUE)
bullets(s, 0.6, 3.65, 12.2, 3.3, [
    ("三种 Seed Program（三条独立 lineage）：", "vector_search / llm_summarizer / experience_learner——对「好审稿系统如何构建」的三种不同初始假设，允许真实分叉。"),
    ("进化产物：", "每任务晋升一个 best_program.py，经 MSTAR_PROGRAM_DIR 部署到在线评审；部署后 Program 冻结，不在线自改。"),
    ("明确不进化：", "五角色基础职责、Task Agent、评估器、Base LLM 生成条件、当前论文正文——保证实验对象不漂移。"),
], size=14)

# =========================================================
# Slide 4 系统总体架构（五平面）
# =========================================================
s = content_slide("系统总体架构：五个平面 + 最外层独立 Benchmark")
# 外部数据源
box(s, 0.45, 1.5, 2.1, 4.3, "", fill=WHITE, line=GRAY_LINE)
add_text(s, 0.55, 1.6, 1.9, 0.4, [[("外部数据源", 13, NAVY, True)]], align=PP_ALIGN.CENTER)
for i, t in enumerate(["OpenReview\n论文+人审", "CoCoReviewBench\n3900 submissions", "Semantic Scholar\nAPI"]):
    box(s, 0.62, 2.1 + i * 1.25, 1.76, 1.05, t.split("\n")[0], t.split("\n")[1], fill=GRAY_BG, tsize=11, ssize=9.5)
planes = [
    ("① 资产冻结", "scripts/（一次性）", ["family 去重时序切分 A/B/C/D", "时间安全 S2 冻结池 v2", "只读审计→asset_manifest_v2"], LIGHT_BLUE),
    ("② 离线进化", "memory_evolution/", ["3 条 seed lineage 种群搜索", "Reflector 突变+沙箱评测", "产出 6 个 best_program.py"], LIGHT_TEAL),
    ("③ 有效性筛选", "effectiveness_screening/", ["预注册三臂两重复矩阵", "盲评 proxy 评测", "bootstrap CI 汇总"], LIGHT_ORANGE),
    ("④ 在线评审", "run_single_paper_review_cli.py", ["5 specialist 并发 + META", "注入 M★ 程序记忆", "锚点校验+断点续跑"], LIGHT_BLUE),
    ("⑤ 最外层 Benchmark", "Program 冻结后", ["Benchmark Adapter", "CoCoReviewBench 官方 pipeline", "D 区 untouched test"], LIGHT_TEAL),
]
px = 2.95
for i, (t, sub, items, c) in enumerate(planes):
    box(s, px, 1.5, 1.95, 4.3, "", fill=c, line=GRAY_LINE)
    add_text(s, px + 0.08, 1.6, 1.8, 0.85, [[(t, 13, NAVY, True)], [(sub, 9.5, MID, False)]], align=PP_ALIGN.CENTER, space_after=2)
    add_text(s, px + 0.1, 2.55, 1.78, 3.2, [[("· " + it, 10, DARK, False)] for it in items], space_after=6)
    if i < 4:
        arrow(s, px + 1.96, 3.4, 0.16, 0.3, "right", BLUE)
    px += 2.12
add_text(s, 0.55, 6.05, 12.4, 1.2, [
    [("只读查看器：", 12, NAVY, True), ("viewer.py :7861（评审结果） / evolution_viewer :7862（进化 lineage+scores）", 12, DARK, False)],
    [("关键事实：", 12, NAVY, True), ("主实验入口是顶层 run_single_paper_review_cli.py；旧 Arena/Environment 多论文链路为遗产代码，主流程不经过。", 12, DARK, False)],
], space_after=5)

# =========================================================
# Slide 5 Reviewer 拓扑
# =========================================================
s = content_slide("Reviewer 拓扑：五个 Specialist + META",
                  "Frozen Reviewer Identity：角色职责永久锁定，Evolution 不得修改")
box(s, 0.5, 2.9, 1.9, 1.3, "论文 Markdown", ["正文解析", "source anchors", "冻结 S2 池(可选)"], fill=WHITE, tsize=13, ssize=10)
arrow(s, 2.42, 3.35, 0.35, 0.35, "right", BLUE)
specs = [("QUAL", "技术质量"), ("CLAR", "写作清晰度"), ("SIGN", "重要性"), ("ORIG", "原创性"), ("POL", "规范性")]
for i, (t, d) in enumerate(specs):
    by = 1.30 + i * 1.02
    box(s, 2.95, by, 2.5, 0.88, f"{t}", d + "  +  专属 MemoryProgram", fill=LIGHT_BLUE, line=BLUE, tsize=13, ssize=9.5)
    arrow(s, 5.5, by + 0.31, 0.5, 0.26, "right", GRAY_LINE)
box(s, 6.15, 2.7, 2.2, 1.6, "META", ["聚合 5 份 specialist review", "产出最终 meta-review"], fill=LIGHT_TEAL, line=TEAL, tsize=16, ssize=10.5)
arrow(s, 8.4, 3.35, 0.35, 0.35, "right", TEAL)
box(s, 8.85, 2.7, 2.2, 1.6, "Meta-Review", ["锚点校验/修复", "结构化输出落盘"], fill=WHITE, line=TEAL, tsize=14, ssize=10.5)
bullets(s, 0.5, 6.42, 12.4, 1.0, [
    ("硬约束：", "5 个 specialist 相互独立、可并行，互不可见对方结果；只有 META 聚合。当前最小闭环先跑 QUAL。"),
    ("唯一来源 REVIEW_Request.md：", "5 角色 + 23 个子维度，运行时校验 SHA-256（609eb930…7be42），被两个外部包反向引用作契约。"),
], size=11.5, space_after=3)

# =========================================================
# Slide 6 进化循环
# =========================================================
s = content_slide("MemoryProgram Evolution 循环", "memory_evolution/search.py · 正式预算与协议数字固化于 cli.py")
steps = [
    ("父程序选择", "softmax 按 static score\n温度 0.15，保持种群多样性", LIGHT_BLUE),
    ("Reflector 突变", "LLM contextual V4A patch\ncompile_fix_loop ≤ 3 次", LIGHT_TEAL),
    ("质量门", "静态检查：import 白名单 /\n协议 / 读上限 + 编译冒烟", LIGHT_ORANGE),
    ("沙箱评测", "bubblewrap+rlimit\nA 区回放→B 区评审→fitness", LIGHT_BLUE),
    ("持久化", "lineage.jsonl / scores.jsonl\n断点续跑", LIGHT_TEAL),
]
px = 0.5
for i, (t, d, c) in enumerate(steps):
    box(s, px, 1.55, 2.3, 1.55, t, d.split("\n"), fill=c, line=GRAY_LINE, tsize=14, ssize=10)
    if i < 4:
        arrow(s, px + 2.32, 2.15, 0.22, 0.3, "right", BLUE)
    px += 2.56
add_rect(s, 0.5, 3.6, 11.7, 0.02, GRAY_LINE)
bullets(s, 0.6, 4.35, 12.2, 2.9, [
    ("正式预算（代码固化）：", "21 次 successful child evaluations，每条 seed lineage 7 个；单个预注册 outer seed 0。"),
    ("验证集：", "static 25（只评分，不进 Reflector）+ rotating 5（有限 trace 反馈），BGE-M3 冻结选择，正式模式无随机回退。"),
    ("适应度：", "Judge 五项质量分（1–5）与 CoCo 类别覆盖率的调和平均；B 区 gold 只进 evaluator_only 产物。"),
    ("宿主安全：", "宿主进程永不执行候选代码——全部评测在 bubblewrap 沙箱内完成。"),
], size=13.5)

# =========================================================
# Slide 7 进化语义与公平性
# =========================================================
s = content_slide("进化语义：为什么这个实验是公平的（ADR-0007）")
bullets(s, 0.6, 1.35, 12.2, 5.8, [
    ("Fresh-State 语义：", "每次 fitness evaluation = fresh empty Memory → 按冻结顺序回放 A 区 → 自产 Experience/Memory → B 区 Evolution Validation。"),
    ("时序协议（硬规则）：", "对 Paper_i：先读取、可读 Paper_1..i-1 的已有 Memory，自己审稿完成后才把 Paper_i 的经验写入——不能先写再审、不能读自己刚产的经验。"),
    ("继承的是代码，不是记忆：", "Candidate 不继承父 Candidate 的运行时 Memory State；mutation 传递的是 Program / code lineage。"),
    ("Self-Generated Experience：", "所有 Seed 共享同一批历史原材料（论文全文+metadata+anchors），各自形成自己的经验——共享的是题目，不是答案。"),
    ("Engineering Budget：", "同一实验内所有 Candidate 用相同 Base LLM 与 API 能力边界；实际 token / context / latency 如实记录，用于 performance–cost 分析，而非强行拉平。"),
    ("Unified Interface：", "evolution 与 deployment 走同一 canonical paper-content builder、同一裁剪实现——不允许 evolution 60K vs deployment 35K 的分裂。"),
], size=14)

# =========================================================
# Slide 8 数据协议：四区隔离
# =========================================================
s = content_slide("数据协议：family 级时序四区隔离（ADR-0006）")
box(s, 0.5, 1.6, 2.6, 1.5, "CoCoReviewBench", ["3900 条 submission", "只读 source pool"], fill=WHITE, tsize=13, ssize=10.5)
arrow(s, 3.15, 2.15, 0.35, 0.35, "right", BLUE)
box(s, 3.6, 1.6, 3.0, 1.5, "family 去重", ["paperhash / normalized title", "传递闭包 → deterministic", "选 representative"], fill=LIGHT_BLUE, tsize=13, ssize=10)
arrow(s, 6.65, 2.15, 0.35, 0.35, "right", BLUE)
box(s, 7.1, 1.6, 2.4, 1.5, "3889 个", ["family-level targets", "冻结 manifest+SHA"], fill=LIGHT_TEAL, tsize=14, ssize=10.5)
zones = [
    ("A 区 Historical", "2391 篇（≤2022）", "Program 回放历史、自产经验；candidate 不可见 gold", LIGHT_BLUE),
    ("B 区 Evolution Validation", "300 篇（2023）", "selection / mutation / promotion 的 fitness 依据", LIGHT_TEAL),
    ("C 区 Effectiveness Holdout", "300 篇（2023）", "Program 冻结后的首次独立 effectiveness 测量", LIGHT_ORANGE),
    ("D 区 Held-out Benchmark Test", "899 篇（2024–25）", "最外层 Official Benchmark；看过即失去 untouched 身份", LIGHT_BLUE),
]
for i, (t, n, d, c) in enumerate(zones):
    bx = 0.5 + i * 3.15
    box(s, bx, 3.55, 2.95, 1.85, t, [n, d], fill=c, line=GRAY_LINE, tsize=12.5, ssize=10)
bullets(s, 0.6, 5.7, 12.2, 1.6, [
    ("硬约束：", "四区两两 paper ID / normalized title / family 零重叠，且 max(A/B/C cutoff) ≤ min(D cutoff)；formal 入口强制审计。"),
    ("S2 时间安全：", "每篇带 paper_review_cutoff（保守 year-level）；publication date 晚于 cutoff 的检索结果拒绝，缓存记录 SHA。"),
], size=12.5, space_after=4)

# =========================================================
# Slide 9 防泄漏设计
# =========================================================
s = content_slide("Human Reference Boundary：泄漏防控是系统级设计")
box(s, 0.5, 1.45, 5.9, 2.6, "", fill=LIGHT_TEAL, line=TEAL)
add_text(s, 0.7, 1.6, 5.5, 2.4, [
    [("✓ gold 只允许进入三处", 15, TEAL, True)],
    [("· external evaluator（fitness 计算）", 12.5, DARK, False)],
    [("· evolution fitness computation", 12.5, DARK, False)],
    [("· benchmark evaluation（最外层）", 12.5, DARK, False)],
    [("", 6, DARK, False)],
    [("Evolution 只能获得 scalar fitness / 不泄漏答案的 structured metrics", 11.5, MID, False)],
], space_after=5)
box(s, 6.7, 1.45, 6.1, 2.6, "", fill=RGBColor(0xFB, 0xE9, 0xE7), line=RED)
add_text(s, 6.9, 1.6, 5.7, 2.4, [
    [("✗ 不得进入", 15, RED, True)],
    [("candidate-visible Episode · Reviewer prompt · MemoryProgram 输入", 12.5, DARK, False)],
    [("KnowledgeItem 提取输入 · retrieved Memory · mutation prompt", 12.5, DARK, False)],
    [("", 6, DARK, False)],
    [("旁路规则：judge rationale 若复述 gold，不得原样回灌给 Candidate", 11.5, MID, False)],
], space_after=5)
bullets(s, 0.6, 4.35, 12.2, 2.9, [
    ("Candidate 可见的只有：", "论文全文 + Candidate-Visible Metadata（paper_id/title/venue/year/role）+ source anchors + 可选 time-safe S2 池。"),
    ("代码级保障：", "agentreview_data.py 的 assert_no_human_reference_leak 泄漏审计；B 区 gold 只落 evaluator_only/ 产物。"),
    ("Blind Judging（强制）：", "judge 看不到臂标签 / memory mode / program path；送评前剥离 memory context 痕迹，两臂同一脱敏管道，2 replicates。"),
    ("教训已制度化：", "旧 pilot episode 曾把 VERIFIED HUMAN REFERENCE 拼进 candidate 文本——该 run 产物已降级为仅历史分析，正式结论需重新进化。"),
], size=13)

# =========================================================
# Slide 10 Embedding 系统
# =========================================================
s = content_slide("BGE-M3 Embedding：选型与检索基础设施", "embedding 不生成 review，只服务选择、检索与缓存")
uses = [
    ("Episode 选择", "2391 个 A 区 episode → BGE-M3 →\nfacility-location 选出有代表性的训练经验", LIGHT_BLUE),
    ("Validation 选择", "B 区验证问题 → embedding 聚类 →\n冻结 static 25 + rotating 5", LIGHT_TEAL),
    ("Runtime Retrieval", "query → embedding → 向量相似度 →\nMemoryProgram 检索记忆", LIGHT_ORANGE),
]
for i, (t, d, c) in enumerate(uses):
    box(s, 0.5 + i * 4.28, 1.5, 4.05, 1.75, t, d.split("\n"), fill=c, line=GRAY_LINE, tsize=14, ssize=10.5)
box(s, 0.5, 3.6, 12.3, 1.9, "", fill=WHITE, line=BLUE)
add_text(s, 0.75, 3.75, 11.9, 1.7, [
    [("SQLite Embedding Cache（2026-08 新增，embedding_adapter.py）", 14.5, NAVY, True)],
    [("cache key = text SHA256 + model revision + embedding semantics    →    value = float32 vector", 12.5, DARK, False)],
    [("路径 cache/embedding_cache.sqlite3 · schema embedding-cache-sqlite-v1 · 固定数据一次计算、后续 evolution 全部 cache hit · 不消耗 Kimi API", 12.5, DARK, False)],
], space_after=6)
bullets(s, 0.6, 5.75, 12.2, 1.4, [
    ("为什么需要：", "此前每次 evolution 启动都重算 2391 episodes + validation 的 embedding，启动极慢；cache 后不影响实验 identity。"),
    ("资源策略：", "BGE-M3 显存约 2GB，放 A30 预生成 cache；A100 留给正式 evolution run。"),
], size=12.5, space_after=4)

# =========================================================
# Slide 11 CoCo evaluator
# =========================================================
s = content_slide("CoCoReviewBench：最外层独立裁判", "经 Benchmark Adapter 接入；gold/taxonomy/judge 不进 Reviewer 推理输入")
chain = [
    ("Generated Review", "Reviewer 产出", LIGHT_BLUE),
    ("ReviewSplit", "Qwen3-8B-Split\nvLLM :13099", LIGHT_TEAL),
    ("ReviewClassify", "Qwen3-8B-Classify\nvLLM :13098", LIGHT_TEAL),
    ("意见类别集合", "从文本重新提取\n不信任候选自报类别", LIGHT_ORANGE),
    ("vs Human Reference", "比较 → score", LIGHT_BLUE),
]
px = 0.5
for i, (t, d, c) in enumerate(chain):
    box(s, px, 1.55, 2.25, 1.5, t, d.split("\n"), fill=c, line=GRAY_LINE, tsize=13, ssize=10)
    if i < 4:
        arrow(s, px + 2.27, 2.1, 0.18, 0.3, "right", BLUE)
    px += 2.47
bullets(s, 0.6, 3.45, 12.2, 3.6, [
    ("部署：", "两个 Qwen3-8B 经 vLLM OpenAI-compatible API 部署，接口自检 SPLIT_OK / CLASSIFY_OK；FrozenCoCoAdapter 固定官方 commit。"),
    ("类别覆盖率：", "|AI categories ∩ human categories| / |human categories|。"),
    ("Specialist fitness：", "Judge 五项质量分（alignment/completeness/grounding/verifiability/clarity，1–5）与类别覆盖率的调和平均。"),
    ("Benchmark-agnostic：", "Reviewer / MemoryProgram 的推理输入中不得出现任何 benchmark 的 gold / taxonomy / judge 信息；换 benchmark 只换 adapter，不改 Reviewer 核心。"),
    ("分层评估：", "B 区（进化选择）→ C 区（development effectiveness，不称正式成绩）→ D 区（Program 冻结后的 Official Benchmark，只跑一次）。"),
], size=13.5)

# =========================================================
# Slide 12 实验设计
# =========================================================
s = content_slide("实验设计：如何证明「进化有效」")
rows = [
    ["比较臂 / 消融", "作用"],
    ["no_memory → seed（Three Arms 之一）", "验证 memory 本身是否有用"],
    ["seed → evolved（Three Arms 之二）", "验证 evolution 带来额外收益"],
    ["iterations=0", "三个初始程序、无进化基线"],
    ["mutation_scope=instructions_only / schema_logic", "定位收益来自外围指令还是记忆结构"],
    ["parent_policy=best", "无种群采样的线性最优父程序对照"],
    ["AgentReview 0716 原系统", "外部基线"],
]
table_slide(s, 0.55, 1.35, 7.2, rows, [3.4, 3.8], size=11.5, row_h=0.52)
bullets(s, 8.15, 1.4, 4.7, 5.5, [
    ("控制变量：", "同论文、同 Base LLM（kimi-k3）、同 S2 冻结池、同 evaluator、同 reviewer 设定。"),
    ("记录单位：", "Program Lineage（evolved_from_vector_search 等），携带 parent / SHA256 / fitness / promotion 溯源。"),
    ("统计：", "work-family 级 paired bootstrap 95% CI；改善/下降论文比例；格式失败率与 API 成本。"),
    ("终点：", "META 为主要终点，五个 specialist 为机制与分维度结果。"),
], size=12.5, space_after=6)

# =========================================================
# Slide 13 工程质量
# =========================================================
s = content_slide("工程质量：让「LLM 生成代码并进化」可运行、可复现")
cards = [
    ("候选沙箱", "bubblewrap + rlimit 隔离执行候选程序；清空环境、资源限制、宿主进程永不执行候选代码", LIGHT_BLUE),
    ("质量门", "import 白名单 / KnowledgeBase 协议 / 读上限静态检查 + 编译冒烟 + 编译修复循环", LIGHT_TEAL),
    ("身份绑定 checkpoint", "canonical SHA256 + memory_library 快照；断点续跑只恢复自身 verified prefix", LIGHT_ORANGE),
    ("Asset Manifest", "每项资产记录解析路径 / SHA256 / 版本 / split，不靠目录名推断身份", LIGHT_BLUE),
    ("审计门禁", "finalize_formal_readiness 只读审计通过才登记正式资产；formal_ready=true", LIGHT_TEAL),
    ("测试与查看器", "21 个 unittest 文件；viewer :7861 / evolution_viewer :7862 只读", LIGHT_ORANGE),
]
for i, (t, d, c) in enumerate(cards):
    bx = 0.5 + (i % 3) * 4.28
    by = 1.5 + (i // 3) * 2.15
    box(s, bx, by, 4.05, 1.95, t, [d], fill=c, line=GRAY_LINE, tsize=14, ssize=11)
add_text(s, 0.55, 6.0, 12.3, 1.1, [
    [("规模：", 12.5, NAVY, True), ("约 2 万行 Python；memory_evolution/ ≈9900 行，effectiveness_screening/ ≈2560 行，主 CLI 2135 行；遗产代码标记保留不删除（docs/DEPRECATED.md）。", 12.5, DARK, False)],
])

# =========================================================
# Slide 14 当前进度
# =========================================================
s = content_slide("当前进度（截至 2026-08-20，HEAD 7256ffc）")
done = [
    "数据冻结：四区 temporal split v2 + 冻结 S2 池 v2 + asset_manifest_v2，formal_ready=true",
    "canonical 进化数据 evolution_coco_v2_20260819 五任务 bundle 已冻结（SHA 核验，勿重建）",
    "CoCo evaluator：Qwen3-8B Split/Classify vLLM 部署，接口自检通过",
    "Evolution pipeline smoke：2391 episodes 加载、embedding/CoCo adapter、Kimi API 全链路打通",
    "Embedding SQLite cache 实现并测试通过（7256ffc）",
    "2026-08-18 运行前审计 + 08-20 复审完成（docs/RUN_AUDIT_20260818.md）",
]
doing = [
    "QUAL 正式 evolution 准备中（当前最小闭环先跑 QUAL，降本验证 pipeline）",
    "A30 预生成 episodes/validation embedding 写入 cache",
    "已知未修：QUAL_test.sh / end2end_smoke.sh 硬编码 key 待清理；在线管线 B1（环境变量名分裂）",
]
box(s, 0.5, 1.4, 6.1, 5.4, "", fill=LIGHT_TEAL, line=TEAL)
add_text(s, 0.72, 1.55, 5.7, 5.2, [[("✅ 已完成", 16, TEAL, True)]] + [[("· " + d, 12, DARK, False)] for d in done], space_after=8)
box(s, 6.85, 1.4, 5.95, 5.4, "", fill=LIGHT_ORANGE, line=ORANGE)
add_text(s, 7.07, 1.55, 5.55, 5.2, [[("🔄 进行中 / 待修", 16, ORANGE, True)]] + [[("· " + d, 12, DARK, False)] for d in doing], space_after=8)

# =========================================================
# Slide 15 下一步
# =========================================================
s = content_slide("下一步：从 smoke 到正式 QUAL Evolution")
steps = [
    ("Step 1", "A30 预生成 embedding", "QUAL episodes + validation\n写入 embedding_cache.sqlite3", LIGHT_BLUE),
    ("Step 2", "重跑 smoke 验证", "cache hit · CoCo evaluator ·\nKimi 生成 · pipeline 启动", LIGHT_TEAL),
    ("Step 3", "正式 QUAL evolution", "21 次成功子代评测（7/lineage×3）\nstatic 25 + rotating 5 · outer seed 0", LIGHT_ORANGE),
    ("Step 4", "扩展六任务 → META", "五 specialist 冻结 → out-of-fold\nMETA 输入 → META 独立进化", LIGHT_BLUE),
    ("Step 5", "C 区 → D 区", "effectiveness holdout 盲评 →\n封存 D 区 Official Benchmark 跑一次", LIGHT_TEAL),
]
px = 0.5
for i, (tag, t, d, c) in enumerate(steps):
    box(s, px, 1.6, 2.35, 2.15, f"{tag} · {t}", d.split("\n"), fill=c, line=GRAY_LINE, tsize=12.5, ssize=10)
    if i < 4:
        arrow(s, px + 2.37, 2.5, 0.18, 0.3, "right", BLUE)
    px += 2.56
bullets(s, 0.6, 4.15, 12.2, 3.0, [
    ("正式 run 模型配置：", "task / reflector / judge 均为 kimi-k3（三处默认，代码固化）。"),
    ("环境：", "云服务器 gpu1/gpu3（A100 40GB 用于正式 run；A30 用于 embedding）；env_shengao / env_shengao_a100。"),
    ("云端运行权威：", "docs/CLOUD_FORMAL_ALIGNMENT_20260820.md（含完整命令、resume 程序与 readiness gate）。"),
    ("纪律：", "outer seed 0 是预注册最小闭环；不看 test 回改 Program；final-evaluate-suite 只执行一次且拒绝覆盖。"),
], size=13.5)

# =========================================================
# Slide 16 风险与边界
# =========================================================
s = content_slide("风险与表述边界：现在能说什么、不能说什么")
rows = [
    ["风险 / 边界", "当前状态", "应对"],
    ["不能宣称已有稳定进化增益", "smoke 只证明工程闭环成立；正式进化尚未完成", "汇报口径限定为「架构+协议+管线就绪」"],
    ["旧 pilot 产物违反泄漏边界", "pilot_full_iter5 产物已降级为仅历史分析", "修复边界后重新进化出正式结论"],
    ["验证集过拟合", "Reflector 可能利用反馈", "static 不进反馈；rotating 只给有限 trace"],
    ["可执行候选风险", "MemoryProgram 是 LLM 生成代码", "bubblewrap 沙箱 + 白名单 + 资源限制 + 无网络"],
    ["结果选择偏差", "可能挑最好的 seed", "预注册 seed 0；其余 seed 仅供未来稳定性分析"],
    ["API / 分类成本", "Task/Reflector/Judge/CoCo 全链路调用", "QUAL 单轮计时外推预算；固定服务版本监控吞吐"],
]
table_slide(s, 0.55, 1.4, 12.2, rows, [3.3, 4.4, 4.5], size=11.5, row_h=0.62)

# =========================================================
# Slide 17 总结
# =========================================================
s = content_slide("总结：三句话口径")
msgs = [
    ("架构", "把 M★ 的任务专属可执行 MemoryProgram 搜索映射到五个独立 specialist + 一个 META；reviewer 设定、Task Agent、评估器与系统拓扑不参与进化。", LIGHT_BLUE),
    ("工程", "四区 family 级数据隔离、泄漏审计、种群搜索、变异修复、沙箱执行、冻结 CoCo/BGE-M3/S2 资产与 embedding cache 全部就绪，formal_ready=true。", LIGHT_TEAL),
    ("实验", "全链路 smoke 已验证（2391 episodes + CoCo evaluator + Kimi 生成）；当前进入 QUAL 正式进化阶段——在 D 区封存测试执行前，不宣称已获得稳定进化增益。", LIGHT_ORANGE),
]
for i, (t, d, c) in enumerate(msgs):
    box(s, 0.6, 1.55 + i * 1.75, 12.1, 1.55, "", fill=c, line=GRAY_LINE)
    add_text(s, 0.9, 1.72 + i * 1.75, 1.4, 1.2, [[(t, 20, NAVY, True)]], anchor=MSO_ANCHOR.MIDDLE)
    add_text(s, 2.4, 1.7 + i * 1.75, 10.0, 1.3, [[(d, 14.5, DARK, False)]], anchor=MSO_ANCHOR.MIDDLE, line_spacing=1.15)
add_text(s, 0.6, 6.95, 12.1, 0.4, [
    [("AgentReview M★ · Memory-Evolution Framework · 2026-08-20", 11, MID, False)],
], align=PP_ALIGN.CENTER)

out = "/data0/xyj/shengao/AgentReview_0811/AgentReview_Mstar_技术汇报_20260820.pptx"
prs.save(out)
print("saved:", out, "slides:", len(prs.slides.__iter__.__self__._sldIdLst))
