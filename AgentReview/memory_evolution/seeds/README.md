# M★ seed baselines

The three population seeds implement distinct memory bottlenecks so their
effects remain interpretable during evolution.

## Vector search

`vector_search.py` performs lossless, chunked writes followed by selective
dense-vector retrieval. It is the non-parametric retrieval baseline, following
the retrieval-augmented generation design in Lewis et al.,
[Retrieval-Augmented Generation for Knowledge-Intensive NLP
Tasks](https://arxiv.org/abs/2005.11401).

## LLM summarizer

`llm_summarizer.py` performs lossless writes followed by lossy,
query-focused compression at read time. It retains the original episodes,
builds a bounded and balanced view across them, and asks the LLM for a concise
summary conditioned on the current query. This is the query-focused
summarization baseline, related to Zhang et al.,
[Beyond Relevant Documents: A Knowledge-Intensive Approach for Query-Focused
Summarization using Large Language
Models](https://arxiv.org/abs/2408.10357).

The write-side `KnowledgeItem` contains routing metadata only. The raw episode
is already supplied separately to `KnowledgeBase.write`, so asking the model to
copy it into a generated JSON field would add latency without adding memory.
At read time, each stored episode receives an equal excerpt budget with both
its beginning and ending represented. The aggregate history is capped at
12,000 characters and the returned summary at 1,500 characters.

## Experience learner

`experience_learner.py` performs lossy abstraction at write time and structured
recall at read time. It stores reusable lessons and concrete facts rather than
the original episode. This is the experiential-learning baseline, following
the natural-language experience abstractions used by Zhao et al.,
[ExpeL: LLM Agents Are Experiential
Learners](https://arxiv.org/abs/2308.10144), and the verbal episodic memory in
Shinn et al., [Reflexion: Language Agents with Verbal Reinforcement
Learning](https://proceedings.neurips.cc/paper_files/paper/2023/hash/1b44b878bb782e6954cd888628510e90-Abstract-Conference.html).

Together the baselines compare:

1. lossless write and selective retrieval;
2. lossless write and lossy read-time compression;
3. lossy write-time abstraction and structured recall.
