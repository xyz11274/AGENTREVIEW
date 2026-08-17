#AgentReview/agentreview/arguments.py

import argparse
import logging
import os

logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="Argument parser for configuring OpenAI API and experiment settings")

    # Authentication details for OpenAI API
    parser.add_argument(
        "--openai_key", type=str, default=None,
        help="API key to authenticate with OpenAI. Can be set via this argument or through the OPENAI_API_KEY environment variable."
    )

    parser.add_argument(
        "--deployment", type=str, default=None,
        help="For Azure OpenAI: the deployment name to be used when calling the API."
    )

    parser.add_argument(
        "--openai_client_type", type=str, default="openai", choices=["openai", "azure_openai"],
        help="Specify the OpenAI client type to use: 'openai' for standard OpenAI API or 'azure_openai' for Azure-hosted OpenAI services."
    )

    parser.add_argument(
        "--endpoint", type=str, default=None,
        help="For Azure OpenAI: custom endpoint to access the API. Should be in the format 'https://<your-endpoint>.openai.azure.com'."
    )

    parser.add_argument(
        "--api_version", type=str, default="2023-05-15", help="API version to be used for making requests. Required "
                                                              "for Azure OpenAI clients."
    )

    # Experiment configuration
    parser.add_argument(
        "--ac_scoring_method", type=str, default="ranking", choices=["recommendation", "ranking"],
        help="Specifies the scoring method used by the Area Chair (AC) to evaluate papers: 'recommendation' or 'ranking'."
    )

    parser.add_argument(
        "--conference", type=str, default=None,
        help="Conference name where the papers are being evaluated, e.g., 'ICLR2023'."
    )

    parser.add_argument(
        "--num_reviewers_per_paper", type=int, default=3, help="The number of reviewers assigned to each paper."
    )

    parser.add_argument(
        "--experiment_name",
        type=str, default=None, required=False,
        help="Specifies the name of the experiment to run. Choose from predefined experiment types based on the reviewer and AC behavior or experiment configuration."
    )

    parser.add_argument(
        "--overwrite", action="store_true",
        help="If set, existing results or output files will be overwritten without prompting."
    )
    parser.add_argument(
        "--skip_logging", action="store_true", help="If set, we do not log the messages in the console."
    )

    parser.add_argument(
        "--num_papers_per_area_chair", type=int, default=10,
        help="The number of papers each area chair is assigned for evaluation."
    )

    # Model configuration
    parser.add_argument(
    "--model_name", type=str, default="gpt-5-mini",
    help="GPT model used by the specialist and META reviewers."
    )

    parser.add_argument(
    "--review_temperature", type=float, default=0.3,
    help="Sampling temperature shared by specialist and META reviewers."
    )

    # Output directories
    parser.add_argument(
        "--output_dir", type=str, default="outputs", help="Directory where results, logs, and outputs will be stored."
    )

    # Output directories
    parser.add_argument(
    "--max_num_words", type=int, default=16384, help="Maximum number of words in the paper."
    )

    parser.add_argument(
        "--visual_dir", type=str, default="outputs/visual",
        help="Directory where visualization files (such as graphs and plots) will be stored."
    )

    # System configuration
    parser.add_argument(
        "--device", type=str, default='cuda',
        help="The device to be used for processing (e.g., 'cuda' for GPU acceleration or 'cpu' for standard processing)."
    )



    parser.add_argument(
        "--data_dir", type=str, default='data', help="Directory where input data (e.g., papers) are stored."
    )

    parser.add_argument(
        "--acceptance_rate", type=float, default=0.32,
        help="Percentage of papers to accept. We use 0.32, the average acceptance rate for ICLR 2020 - 2023"
    )

    # new_for_md
    parser.add_argument(
    "--paper_md_path",type=str,default=None,
    help="Path to a single paper in Markdown format for single-paper review."
    )

    parser.add_argument(
    "--paper_md_dir", type=str, default=None,
    help="Directory containing paper subdirectories for batch Markdown review."
    )

    parser.add_argument(
    "--paper_md_glob", type=str, default="*/md_content_no_appendix.md",
    help="Glob pattern under --paper_md_dir used to discover Markdown papers for batch review."
    )

    parser.add_argument(
    "--paper_context_manifest", type=str, default=None,
    help="Frozen paper-context manifest. When set, paper discovery and extractor context are read only from this checksummed manifest."
    )

    parser.add_argument(
    "--frozen_context_bundle_path", type=str, default=None,
    help="Checksummed frozen context bundle for a single --paper_md_path resume."
    )

    parser.add_argument(
    "--paper_context_split", type=str, default="effectiveness_validation",
    help="Split selected from --paper_context_manifest."
    )

    parser.add_argument(
    "--batch_limit", type=int, default=None,
    help="Optional maximum number of Markdown papers to process in batch mode."
    )

    parser.add_argument(
    "--max_retrieved_refs", type=int, default=20,
    help="Maximum number of related references retrieved for each Markdown paper."
    )

    parser.add_argument(
        "--disable_s2_retrieval", action="store_true",
        help="Deprecated compatibility flag; S2 is never injected by default."
    )

    parser.add_argument(
        "--s2_retrieval_pool", type=str, default=None,
        help="Frozen paper-specific Semantic Scholar pool exposed as an optional harness capability."
    )

    parser.add_argument(
    "--disable_memory_retrieval", action="store_true",
    help="Disable retrieval from previous AgentReview memory records during review generation."
    )

    parser.add_argument(
    "--disable_memory_writeback", action="store_true",
    help="Do not append completed target reviews to memory_library.jsonl."
    )

    parser.add_argument(
        "--memory_mode", type=str, default="none",
    choices=["none", "existing", "mstar_program"],
    help="Select no memory, the existing AgentReview retriever, or an evolved MSTAR memory program."
    )

    parser.add_argument(
    "--memory_program_dir", type=str, default=None,
    help="Directory containing independently evolved QUAL/CLAR/SIGN/ORIG/POL/META best_program.py files."
    )

    parser.add_argument(
    "--memory_embedding_adapter", type=str, default=None,
    help="Frozen embedding client as module:factory; use the same BGE-M3 adapter as evolution."
    )

    parser.add_argument(
    "--memory_embedding_model_name", type=str, default="BAAI/bge-m3",
    help="Embedding model identity used by the evolved MemoryPrograms."
    )

    parser.add_argument(
    "--memory_embedding_revision", type=str, default=None,
    help="Immutable embedding model revision used during evolution."
    )

    parser.add_argument(
    "--memory_source_output_dir", type=str, default=None,
    help="Optional output directory containing the prior memory_library.jsonl used by an evolved memory program."
    )

    parser.add_argument(
    "--memory_knowledge_cache_dir", type=str, default=None,
    help="Read-only offline-compiled KnowledgeItem cache. When set, target runs never re-extract the frozen memory library."
    )

    parser.add_argument(
    "--memory_embedding_mode", type=str, default="frozen",
    choices=["frozen", "deterministic_hash"],
    help="Use a pinned embedding adapter or the deterministic hash embedding used by a non-formal pilot evolution."
    )

    parser.add_argument(
    "--memory_top_k", type=int, default=3,
    help="Number of prior memory records to inject for each specialist reviewer."
    )

    parser.add_argument(
    "--memory_max_chars", type=int, default=5000,
    help="Maximum characters of formatted memory context injected into each reviewer prompt."
    )

    parser.add_argument(
    "--reviewer_workers", type=int, default=5,
    help="Maximum number of specialist reviewers to call concurrently for one paper. Defaults to all five review dimensions."
    )

    parser.add_argument(
    "--api_min_interval_seconds", type=float, default=2.0,
    help="Minimum interval between chat completion requests in this process."
    )

    parser.add_argument(
    "--api_request_timeout_seconds", type=float, default=600.0,
    help="Maximum time to wait while reading one chat completion response before retrying."
    )

    parser.add_argument(
    "--api_connect_timeout_seconds", type=float, default=30.0,
    help="Maximum time for API connection, write, and connection-pool operations."
    )

    parser.add_argument(
    "--review_max_tokens", type=int, default=8192,
    help="Initial max_completion_tokens for each specialist reviewer call."
    )

    parser.add_argument(
    "--review_retry_max_tokens", type=int, default=16384,
    help="Maximum specialist max_completion_tokens used after an empty length-limited response."
    )

    parser.add_argument(
    "--reviewer_reasoning_effort",
    choices=["none", "minimal", "low", "medium", "high"],
    default="low",
    help="Reasoning effort for specialist reviewers; unsupported providers automatically fall back to no explicit effort."
    )

    parser.add_argument(
    "--paper_context_max_chars", type=int, default=60000,
    help="Canonical paper-context budget shared by Evolution, historical replay, specialists, META, and deployment."
    )

    parser.add_argument(
    "--replicate_id", type=str, default="",
    help="Explicit replicate/run identity recorded in every output artifact."
    )

    parser.add_argument(
    "--run_seed", type=int, default=None,
    help="Requested generation seed provenance; current backend records but does not enforce it."
    )

    parser.add_argument(
    "--strict_history_resume", action="store_true",
    help="Enable identity-bound prefix checkpoint/resume for A-zone replay."
    )

    parser.add_argument(
    "--reviewer_paper_max_chars", type=int, default=None,
    help="Deprecated alias for --paper_context_max_chars."
    )

    parser.add_argument(
    "--meta_review_max_tokens", type=int, default=4096,
    help="Maximum output tokens for the final meta-reviewer. Kept separate because reasoning models may consume output budget before producing visible text."
    )

    parser.add_argument(
    "--meta_paper_max_chars", type=int, default=None,
    help="Deprecated alias for --paper_context_max_chars."
    )

    parser.add_argument(
    "--reuse_completed_specialists", action="store_true",
    help="Reuse valid specialist checkpoints and request only missing reviewers."
    )

    parser.add_argument(
    "--keyword_model_name",type=str,default="gpt-5-mini",
    help="S2 api using this client for making keyword"
    )


    
    args = parser.parse_args()

    aliases = [
        value
        for value in (args.reviewer_paper_max_chars, args.meta_paper_max_chars)
        if value is not None
    ]
    if aliases:
        if len(set(aliases + [args.paper_context_max_chars])) > 1 and (
            args.paper_context_max_chars != 60000
        ):
            parser.error(
                "paper context aliases disagree; use one --paper_context_max_chars value"
            )
        if len(set(aliases)) > 1:
            parser.error(
                "reviewer/meta paper context budgets must be identical"
            )
        args.paper_context_max_chars = aliases[0]
    args.reviewer_paper_max_chars = args.paper_context_max_chars
    args.meta_paper_max_chars = args.paper_context_max_chars

    # Ensure necessary directories exist
    os.makedirs(args.visual_dir, exist_ok=True)
    os.makedirs(args.output_dir, exist_ok=True)

    # Set 'player_to_test' based on experiment name
    if args.experiment_name is None:
        args.player_to_test = None
    elif "Rx" in args.experiment_name:
        args.player_to_test = "Reviewer"
    elif "ACx" in args.experiment_name:
        args.player_to_test = "Area Chair"
    elif "no_rebuttal" in args.experiment_name or "no_overall_score" in args.experiment_name:
        args.player_to_test = "Review Mechanism"

    # Sanity checks for authentication
    print("Running sanity checks for the arguments...")

    '''
    if args.openai_client_type == "openai":
        if os.environ.get('OPENAI_API_KEY') is None:
            assert isinstance(args.openai_key, str), ("Please specify the `--openai_key` argument OR set the "
                                                      "OPENAI_API_KEY environment variable.")
            raise ValueError("OpenAI key is missing.")
    '''    

    if args.openai_client_type == "openai":
    # If env var is missing, fall back to CLI arg
        if os.environ.get("OPENAI_API_KEY") is None:
            assert isinstance(args.openai_key, str), (
                "Please specify the `--openai_key` argument OR set the "
                "OPENAI_API_KEY environment variable.")
            os.environ["OPENAI_API_KEY"] = args.openai_key
    # If env var exists, sync it into args for downstream code
        args.openai_key = os.environ.get("OPENAI_API_KEY")


    EXISTING_EXPERIMENT_SETTINGS = [
        "BASELINE", "benign_Rx1", "malicious_Rx1", "malicious_Rx2", "malicious_Rx3", "unknowledgeable_Rx1",
        "knowledgeable_Rx1", "responsible_Rx1", "irresponsible_Rx1", "irresponsible_Rx2", "irresponsible_Rx3",
        "inclusive_ACx1", "authoritarian_ACx1", "conformist_ACx1", "no_numeric_ratings"]

    if args.experiment_name is not None and args.experiment_name not in EXISTING_EXPERIMENT_SETTINGS:
        logger.warning(f"Experiment name '{args.experiment_name}' is not recognized. "
                       f"This can happen if you are customizing your own experiment settings. "
                       f"Otherwise, please choose from the following: "
                       f"{EXISTING_EXPERIMENT_SETTINGS}")

    if args.openai_client_type == "azure_openai":
        if os.environ.get('AZURE_OPENAI_KEY') is None:
            assert isinstance(args.openai_key, str), ("Please specify the `--openai_key` argument OR set the "
                                                      "AZURE_OPENAI_KEY environment variable.")
            os.environ['AZURE_OPENAI_KEY'] = args.openai_key

        if os.environ.get('AZURE_DEPLOYMENT') is None:
            assert isinstance(args.deployment, str), ("Please specify the `--deployment` argument OR set the "
                                                      "AZURE_DEPLOYMENT environment variable.")
            os.environ['AZURE_DEPLOYMENT'] = args.deployment

        if os.environ.get('AZURE_ENDPOINT') is None:
            assert isinstance(args.endpoint, str), ("Please specify the `--endpoint` argument OR set the "
                                                    "AZURE_ENDPOINT environment variable.")
            endpoint = args.endpoint
        else:
            endpoint = os.environ.get('AZURE_ENDPOINT')

        if not endpoint.startswith("https://"):
            endpoint = f"https://{endpoint}.openai.azure.com"
        os.environ['AZURE_ENDPOINT'] = endpoint

        if os.environ.get('OPENAI_API_VERSION') is None:
            assert isinstance(args.api_version, str), ("Please specify the `--api_version` argument OR set the "
                                                       "OPENAI_API_VERSION environment variable.")
            os.environ['OPENAI_API_VERSION'] = args.api_version

    return args
