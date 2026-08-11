1. QUAL (Quality): Is the submission technically sound? Are claims well supported (e.g., by theoretical analysis or experimental results)? Are the methods used appropriate? Is this a complete piece of work or work in progress? Are the authors careful and honest about evaluating both the strengths and weaknesses of their work?
● QUAL-MET (Methodological Soundness): Evaluates whether the proposed algorithms, models or system architectures are technically correct and free of conceptual or implementation errors.

    ○ Strength: Highlights that the method is well-formulated, mathematically consistent and correctly implemented; no major flaws are apparent.
    ○ Weakness: Points out conceptual errors, mis-specified objectives, algorithmic flaws or misuses of optimisation methods.
● QUAL-EXP (Experimental Design & Evaluation): Assesses the adequacy of the experimental setup: choice of datasets, baselines, evaluation metrics, ablation studies, and statistical tests.

    ○ Strength: Notes comprehensive experiments, appropriate baselines, sufficiently large data and proper metrics; experiments convincingly validate claims.
    ○ Weakness: Points out inadequate baselines, missing standard benchmarks, inappropriate metrics or lack of ablation studies.
● QUAL-REP (Reproducibility & Implementation Details): Considers whether the submission provides enough detail (e.g., hyperparameters, code availability, dataset splits) to replicate the work and whether the implementation follows best practices.

    ○ Strength: Commends open-sourced code, detailed hyperparameters, clear dataset descriptions and comprehensive training details enabling easy reproduction.
    ○ Weakness: Notes lack of code, incomplete hyperparameters, unspecified data splits or other missing details that hinder reproduction.
● QUAL-CMP (Comparisons to Prior Work): Evaluates whether the paper sufficiently compares against relevant prior work and state-of-the-art methods.

    ○ Strength: Acknowledges thorough comparisons against recent and appropriate baselines and proper citation of related work.
    ○ Weakness: Notes missing comparisons to well-known recent methods, outdated baselines, or incomplete literature review.
● QUAL-STA (Statistical Rigor & Validation): Evaluates whether statistical analyses (e.g., significance tests, confidence intervals) are properly applied and whether reported improvements are statistically meaningful.

    ○ Strength: Praises appropriate statistical tests, robust validation, confidence intervals and transparent reporting of variance.
    ○ Weakness: Criticises absence of statistical tests, reliance on single runs, or misuse of statistical methods.
2. CLAR (Clarity): Is the submission clearly written? Is it well organized? (If not, please make constructive suggestions for improving its clarity.) Does it adequately inform the reader? (Note that a superbly written paper provides enough information for an expert reader to reproduce its results.)
● CLAR-WRT (Writing, Terminology & Algorithm Presentation): Covers overall writing quality and organization, precision of terminology and key concepts, and the clarity of algorithm presentations (code snippets, pseudocode, workflow diagrams).

    ○ Strength: Well-structured sections and smooth narrative; concise, readable prose; all specialized terms and assumptions are clearly defined and consistently used; pseudocode/code/workflows are self-contained, well-documented (clear variable names, inputs/outputs), and easy to follow step-by-step.
    ○ Weakness: Unclear or disorganized exposition, excessive jargon or redundancy; missing/ambiguous definitions or misuse of terms; confusing or incomplete pseudocode/code/workflows (undefined parameters, missing steps, opaque variable names).
● CLAR-NOT (Notation & Mathematical Explanation Clarity): Considers whether mathematical notation is consistent, well defined and explained.

    ○ Strength: Highlights clear definitions, consistent notation and well-explained derivations.
    ○ Weakness: Notes inconsistent symbols, missing definitions or unexplained mathematical steps.
● CLAR-FIG (Figures & Visual Aids Clarity): Evaluates whether figures, tables, plots and visualisations are legible, properly labelled, and aid understanding.

    ○ Strength: Praises informative and well-designed figures that clearly illustrate results or architectures.
    ○ Weakness: Points out illegible plots, missing labels, confusing colour schemes or misleading visualisations.
3. SIGN (Significance): Are the results impactful for the community? Are others (researchers or practitioners) likely to use the ideas or build on them? Does the submission address a difficult task in a better way than previous work? Does it advance our understanding/knowledge on the topic in a demonstrable way? Does it provide unique data, unique conclusions about existing data, or a unique theoretical or experimental approach?
● SIGN-BRD (Broad Research Impact): Evaluates whether the work addresses a broadly important problem or advances understanding in a way that is likely to influence multiple research areas.

    ○ Strength: Notes that the work tackles a significant, widely relevant challenge or introduces a paradigm that may influence many researchers.
    ○ Weakness: Suggests that the work addresses a niche or already well-explored problem with limited broader interest.
● SIGN-DOM (Domain/Applied Impact): Assesses the significance of the contribution for a specific application domain (e.g., NLP, robotics, healthcare).

    ○ Strength: Highlights how the method outperforms existing approaches or addresses an important challenge in a specialised field.
    ○ Weakness: Notes limited domain relevance, inadequate demonstration on domain-specific benchmarks or unconvincing domain benefits.
● SIGN-SOT (Improvement Over State of the Art): Focuses on the magnitude and importance of improvements relative to current state-of-the-art methods.

    ○ Strength: Praises substantial, consistent performance gains over strong baselines with appropriate statistical support.
    ○ Weakness: Criticises marginal or statistically insignificant improvements, or improvements only on favourable datasets.
● SIGN-IMP (Real-World & Societal Impact): Considers the potential practical and societal ramifications of the work, including benefits, risks, fairness, environmental impacts and accessibility.

    ○ Strength: Recognises thoughtful discussion of societal benefits, harms, fairness, environmental impact and possible mitigations.
    ○ Weakness: Points out a lack of consideration of societal impact or failure to discuss who benefits and who may be harmed.
4. ORIG (Originality): Does the work provide new insights, deepen understanding, or highlight important properties of existing methods? Is it clear how this work differs from previous contributions, with relevant citations provided? Does the work introduce novel tasks or methods that advance the field? Does this work offer a novel combination of existing techniques, and is the reasoning behind this combination well-articulated? As the questions above indicates, originality does not necessarily require introducing an entirely new method. Rather, a work that provides novel insights by evaluating existing methods, or demonstrates improved efficiency, fairness, etc. is also equally valuable.
● ORIG-PROB (Novel Problem Formulation): Comments on the introduction of a new problem, task, or dataset. Includes innovative problem definitions that highlight previously unaddressed challenges.

    ○ Strength: Highlights creative and meaningful new problem formulations or datasets that open new avenues for research.
    ○ Weakness: Notes that the problem is a minor variation of existing tasks or lacks clear motivation.
● ORIG-MTH (Novel Methodology or Algorithm): Assesses whether the paper introduces a genuinely new algorithmic approach or architectural design rather than a minor tweak of existing methods.

    ○ Strength: Recognises innovative algorithmic designs with clear conceptual advances.
    ○ Weakness: Points out that the method is a straightforward modification or re-implementation of existing techniques.
● ORIG-ANL (Novel Analysis or Insights): Pertains to original theoretical insights or analyses that deepen understanding of existing methods or phenomena.

    ○ Strength: Notes insightful analysis that sheds new light on known methods or uncovers unexpected behaviours.
    ○ Weakness: Suggests that the analysis is trivial, already known, or does not yield meaningful insights.
● ORIG-EXP (Novel Experimental Setup or Data): Evaluates whether the paper proposes new experimental setups, benchmarks, evaluation protocols or collects new datasets.

    ○ Strength: Praises introduction of meaningful new datasets, benchmarks or evaluation protocols.
    ○ Weakness: Notes that the experimental setup is derivative or does not add value beyond existing benchmarks.
● ORIG-COM (Creative Combination of Existing Methods): Considers whether the paper combines well-known techniques in an original way and whether the rationale behind the combination is compelling.

    ○ Strength: Recognises innovative synergies or architectures that combine methods in a way that yields new capabilities.
    ○ Weakness: Suggests that the combination is superficial or lacks a clear justification for being novel.
● ORIG-NEG (Negative Results or Critical Assessments): Covers papers that provide critical evaluations, ablations or negative results showing limitations of existing methods.

    ○ Strength: Commends thoughtful critical analysis or the presentation of negative results that challenge assumptions.
    ○ Weakness: Notes superficial or poorly justified criticism, or negative results that lack rigour.
5. POL (Policy/Compliance): Encompasses policy- or compliance-related concerns such as ethics, data/privacy compliance, anonymity rules, plagiarism, licensing, and broader impact. These issues often require checking adherence to conference or legal policies and may involve ethical considerations.
● POL-ETH (Ethics & Responsible AI Compliance): Addresses ethical concerns, including fairness, bias, harms to marginalised populations, dual use and whether the authors appropriately discuss and mitigate ethical risks.

    ○ Strength: Notes thorough consideration of potential harms, fairness, privacy and mitigation strategies.
    ○ Weakness: Flags unaddressed ethical issues, potential harms or insufficient discussion of biases.
● POL-DAT (Data Usage & Privacy Compliance): Considers whether the data used in the paper comply with privacy regulations and licensing terms (e.g., consent, personally identifiable information).

    ○ Strength: Praises adherence to data-use policies, proper anonymisation and appropriate licensing.
    ○ Weakness: Points out potential privacy violations, insufficient data consent or misuse of licensed data.
● POL-ANO (Double-Blind or Anonymity Violations): Concerns whether the submission inadvertently reveals author identities or institutional affiliations, violating double-blind review policies.

    ○ Strength: Confirms that the paper appropriately anonymises authors and affiliations.
    ○ Weakness: Highlights self-identifying text, acknowledgements or URLs that compromise anonymity.
● POL-PLG (Plagiarism & Dual Submission): Addresses issues of plagiarism, self-plagiarism, or dual submission to multiple venues.

    ○ Strength: Confirms originality of content, proper citations and no evidence of duplicate submission.
    ○ Weakness: Notes textual or figure overlap, uncredited reuse of material or simultaneous submission.
● POL-IMP (Broader Impact & Societal Considerations): Evaluates whether the authors complete required checklists and discuss the broader impact of their work on society.

    ○ Strength: Comprehensive and honest broader-impact statements and required checklists; documented IRB/ethics approvals where applicable; explicit and correct software/data licences (and usage within licence terms); clear consent/approval identifiers and compliance evidence.
    ○ Weakness: Missing or boilerplate impact statements; absent/unclear required checklists; missing IRB or ethics approval where needed; ambiguous or incompatible licensing; evidence of non-compliant use (e.g., violating dataset or code licence terms).