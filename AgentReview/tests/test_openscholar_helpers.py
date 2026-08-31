import ast
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from agentreview.openscholar_bridge.src import use_search_apis
from agentreview.openscholar_bridge.src import utils as openscholar_utils
from agentreview.utility import s2_retrieval


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)
        self.content = self.text.encode("utf-8")

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")


class SemanticScholarHelpersTest(unittest.TestCase):
    def test_api_key_is_resolved_when_each_request_is_sent(self):
        response = FakeResponse({"data": []})
        with (
            mock.patch.dict(os.environ, {"S2_API_KEY": "runtime-test-key"}),
            mock.patch.object(
                use_search_apis.requests, "get", return_value=response
            ) as request,
        ):
            self.assertEqual(
                [], use_search_apis.search_paper_via_query("retrieval augmented")
            )

        self.assertEqual(
            {"x-api-key": "runtime-test-key"}, request.call_args.kwargs["headers"]
        )

    def test_failed_query_returns_empty_without_character_retries(self):
        response = FakeResponse({"error": "unavailable"}, status_code=503)
        with (
            mock.patch.object(
                use_search_apis.requests, "get", return_value=response
            ) as request,
        ):
            papers = use_search_apis.search_paper_via_query("whole query")

        self.assertEqual([], papers)
        request.assert_called_once()

    def test_google_search_has_an_explicit_optional_dependency_error(self):
        with self.assertRaisesRegex(RuntimeError, "googlesearch-python"):
            use_search_apis.search_google_non_restricted("language models")

    def test_you_search_reads_its_key_from_the_environment(self):
        response = FakeResponse({"hits": []})
        with (
            mock.patch.dict(os.environ, {"YOU_API_KEY": "runtime-you-key"}),
            mock.patch.object(
                use_search_apis.requests, "get", return_value=response
            ) as request,
            mock.patch.object(use_search_apis, "retrieve_passages", return_value={}),
            mock.patch.object(use_search_apis, "batch_paper_data", return_value={}),
            mock.patch.object(
                use_search_apis, "batch_paper_data_pubmed", return_value={}
            ),
        ):
            self.assertEqual(
                [], use_search_apis.search_youcom_non_restricted("language models")
            )

        self.assertEqual(
            {"X-API-Key": "runtime-you-key"}, request.call_args.kwargs["headers"]
        )

    def test_pes2o_requires_an_explicit_endpoint(self):
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(use_search_apis.requests, "post") as request,
            self.assertRaisesRegex(ValueError, "PES2O_INDEX_URL"),
        ):
            use_search_apis.retrieve_pes2o_passages(
                "language models", n_docs=3, domains=["computer science"]
            )

        request.assert_not_called()

    def test_pes2o_forwards_the_requested_domains(self):
        response = FakeResponse(
            {"results": {"passages": ["passage"], "pes2o IDs": ["123"]}}
        )
        with (
            mock.patch.dict(
                os.environ, {"PES2O_INDEX_URL": "https://example.test/search"}
            ),
            mock.patch.object(
                use_search_apis.requests, "post", return_value=response
            ) as request,
            mock.patch.object(
                use_search_apis,
                "get_paper_data",
                return_value={"title": "Paper"},
            ),
        ):
            contexts = use_search_apis.retrieve_pes2o_passages(
                "language models", n_docs=3, domains=["computer science"]
            )

        self.assertEqual("passage", contexts[0]["text"])
        self.assertEqual(
            ["computer science"], request.call_args.kwargs["json"]["domains"]
        )

    def test_combined_search_uses_one_stable_context_schema(self):
        keyword_result = {
            "paperId": "keyword-paper",
            "title": "Keyword Paper",
            "year": 2024,
            "abstract": None,
            "authors": [{"name": "Ada"}],
            "citationCount": 0,
            "url": "https://example.test/keyword",
            "externalIds": {},
        }
        title_result = {
            "paperId": "title-paper",
            "title": "Title Paper",
            "year": 2023,
            "abstract": "Abstract",
            "authors": ["Grace"],
            "citationCount": 2,
            "url": "https://example.test/title",
            "externalIds": {},
            "text": "Abstract",
            "title_query": True,
        }
        with (
            mock.patch.object(
                use_search_apis,
                "search_paper_via_query",
                return_value=[keyword_result],
            ) as keyword_search,
            mock.patch.object(
                use_search_apis,
                "search_paper_via_titles",
                return_value=[title_result],
            ),
        ):
            keyword_papers, title_papers, keywords = (
                use_search_apis.search_semantic_scholar(
                    "question",
                    new_keywords=["retrieval"],
                    new_titles=["Title Paper"],
                    max_paper_num=4,
                    s2_api_key="runtime-s2-key",
                )
            )

        self.assertEqual(["retrieval"], keywords)
        self.assertEqual(2024, keyword_papers[0]["year"])
        self.assertEqual(["Ada"], keyword_papers[0]["authors"])
        self.assertFalse(keyword_papers[0]["title_query"])
        self.assertTrue(title_papers[0]["title_query"])
        self.assertEqual(4, keyword_search.call_args.kwargs["max_paper_num"])
        self.assertEqual(
            "runtime-s2-key", keyword_search.call_args.kwargs["s2_api_key"]
        )


class OpenScholarUtilityTest(unittest.TestCase):
    def test_extract_titles_has_no_stdout_side_effect(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            titles = openscholar_utils.extract_titles(
                "Summary\nReferences:\n[1] First Paper\n[2] Second Paper"
            )

        self.assertEqual(["First Paper", "Second Paper"], titles)
        self.assertEqual("", stdout.getvalue())

    def test_save_tsv_supports_a_filename_in_the_current_directory(self):
        previous_directory = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                openscholar_utils.save_tsv_dict(
                    [{"title": "Paper"}], "papers.tsv", ["title"]
                )
                self.assertEqual(
                    "title\nPaper\n",
                    Path("papers.tsv")
                    .read_text(encoding="utf-8")
                    .replace("\r\n", "\n"),
                )
            finally:
                os.chdir(previous_directory)

    def test_helpers_contain_no_bare_exception_handlers(self):
        for module_path in (
            Path(use_search_apis.__file__),
            Path(openscholar_utils.__file__),
        ):
            tree = ast.parse(module_path.read_text(encoding="utf-8"))
            bare_handlers = [
                node.lineno
                for node in ast.walk(tree)
                if isinstance(node, ast.ExceptHandler) and node.type is None
            ]
            self.assertEqual([], bare_handlers, module_path)

    def test_core_imports_do_not_require_optional_workflow_packages(self):
        tree = ast.parse(Path(use_search_apis.__file__).read_text(encoding="utf-8"))
        top_level_imports = {
            alias.name.split(".", 1)[0]
            for node in tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertTrue(
            top_level_imports.isdisjoint(
                {"bs4", "googlesearch", "jsonlines", "openai", "pandas", "tqdm"}
            )
        )

    def test_jsonlines_round_trip_uses_utf8(self):
        records = [{"title": "论文"}, {"title": "Paper"}]
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "records.jsonl"
            openscholar_utils.save_file_jsonl(records, output_path)
            self.assertEqual(records, openscholar_utils.load_jsonlines(output_path))


class OpenScholarWrapperTest(unittest.TestCase):
    def test_llm_keyword_mode_requires_a_client(self):
        with self.assertRaisesRegex(ValueError, "client"):
            s2_retrieval.retrieve_with_openscholar(
                "language models", use_llm_keywords=True
            )

    def test_llm_keyword_mode_forwards_limit_and_s2_key(self):
        with (
            mock.patch.object(
                s2_retrieval.use_search_apis,
                "retrieve_keywords",
                return_value=["language model retrieval"],
            ),
            mock.patch.object(
                s2_retrieval.use_search_apis,
                "search_semantic_scholar",
                return_value=([{"paperId": "p1"}], [], []),
            ) as search,
        ):
            papers = s2_retrieval.retrieve_with_openscholar(
                "language models",
                use_llm_keywords=True,
                client=object(),
                model_name="test-model",
                max_papers=4,
                s2_api_key="runtime-s2-key",
            )

        self.assertEqual([{"paperId": "p1"}], papers)
        self.assertEqual(4, search.call_args.kwargs["max_paper_num"])
        self.assertEqual("runtime-s2-key", search.call_args.kwargs["s2_api_key"])

    def test_direct_and_title_modes_return_the_context_schema(self):
        direct_context = {
            "semantic_scholar_id": "direct",
            "type": "ss_abstract",
        }
        title_context = {
            "semantic_scholar_id": "title",
            "type": "ss_abstract",
            "title_query": True,
        }
        with mock.patch.object(
            s2_retrieval.use_search_apis,
            "search_semantic_scholar",
            side_effect=[
                ([direct_context], [], ["query"]),
                ([], [title_context], []),
            ],
        ):
            direct_papers = s2_retrieval.retrieve_with_openscholar("query")
            title_papers = s2_retrieval.retrieve_with_openscholar(
                "query", title_query="Exact Title"
            )

        self.assertEqual([direct_context], direct_papers)
        self.assertEqual([title_context], title_papers)


if __name__ == "__main__":
    unittest.main()
