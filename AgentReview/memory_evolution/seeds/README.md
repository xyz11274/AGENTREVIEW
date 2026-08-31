# Memory Program seeds

本目录同时保存 Core v1 seed 与 legacy M★ seed。文件名中的版本号决定协议归属。

## Core v1

core_formal.core_v1_seed_paths() 按固定顺序加载：

1. vector_search_v2.py
2. llm_summarizer_v2.py
3. experience_learner_v2.py

三份 v2 Program 声明 research-validity-core-v1 协议和一个封闭 memory_read_mode，只返回 retrieval plan。Host 负责 Review Memory 检索、预算、ledger 和可选的一次检索后摘要。

| Seed | Read mode | 行为 |
|---|---|---|
| vector_search_v2.py | vector search | 生成有界查询并读取匹配片段 |
| llm_summarizer_v2.py | post-retrieval LLM summary | 先读取片段，再由 Host 执行一次有界摘要 |
| experience_learner_v2.py | structured experience | 读取确定性结构化经验视图 |

它们共同进入每个角色的 shared candidate pool；seed 名称只表达初始读取策略。

## Legacy M★

vector_search.py、llm_summarizer.py、experience_learner.py 实现旧 KnowledgeItem / Query / KnowledgeBase 协议。旧 memory_evolution.cli 和 standalone Reviewer 使用这组三份文件。

Core v1 session 接受 v2 协议，legacy ReviewerSession 接受旧协议。两组 Program 保持独立身份。
