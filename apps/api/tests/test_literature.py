"""Europe PMC tools, and the citation check that keeps the agent honest."""

from __future__ import annotations

import httpx
import pytest

from ohdp_agent.literature import (
    Article,
    CitationRegistry,
    LiteratureClient,
    LiteratureError,
    unverified_citations,
)

SEARCH_PAYLOAD = {
    "hitCount": 1,
    "resultList": {
        "result": [
            {
                "pmid": "39123456",
                "pmcid": "PMC11223344",
                "doi": "10.1001/jama.2024.1234",
                "title": "RSV hospitalisation in adults over 65",
                "authorString": "Smith J, Doe A.",
                "journalInfo": {
                    "yearOfPublication": 2024,
                    "journal": {"title": "JAMA", "medlineAbbreviation": "JAMA"},
                },
                "pubYear": "2024",
                "abstractText": "Population-level surveillance of RSV.",
            }
        ]
    },
}


def _client(handler: object) -> LiteratureClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return LiteratureClient(client=httpx.AsyncClient(transport=transport))


@pytest.mark.asyncio
async def test_search_parses_and_registers() -> None:
    client = _client(lambda r: httpx.Response(200, json=SEARCH_PAYLOAD))
    articles = await client.search_literature("RSV over 65")
    assert articles[0].title.startswith("RSV hospitalisation")
    # Every identifier form is now citable.
    assert client.registry.knows("39123456")
    assert client.registry.knows("PMC11223344")
    assert client.registry.knows("10.1001/jama.2024.1234")


@pytest.mark.asyncio
async def test_empty_query_is_refused_before_the_network() -> None:
    client = _client(lambda r: httpx.Response(500))
    with pytest.raises(LiteratureError):
        await client.search_literature("   ")


@pytest.mark.asyncio
async def test_get_article_builds_the_right_id_query() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = request.url.params.get("query", "")
        return httpx.Response(200, json=SEARCH_PAYLOAD)

    client = _client(handler)
    await client.get_article("10.1001/jama.2024.1234")
    assert seen["query"] == "DOI:10.1001/jama.2024.1234"
    await client.get_article("PMC11223344")
    assert seen["query"] == "PMCID:PMC11223344"
    await client.get_article("39123456")
    assert seen["query"] == "EXT_ID:39123456"


def test_real_citations_pass_the_check() -> None:
    registry = CitationRegistry()
    registry.register(
        (
            Article(
                pmid="39123456",
                pmcid="PMC11223344",
                doi="10.1001/jama.2024.1234",
                title="t",
                authors="a",
                journal="j",
                year="2024",
                abstract="x",
            ),
        )
    )
    answer = "RSV admissions rose (PMID: 39123456; doi:10.1001/jama.2024.1234)."
    assert unverified_citations(answer, registry) == []


def test_a_fabricated_citation_is_caught() -> None:
    """The failure mode this whole module exists to prevent."""
    registry = CitationRegistry()
    registry.register(
        (
            Article(
                pmid="39123456",
                pmcid="",
                doi="",
                title="t",
                authors="a",
                journal="j",
                year="2024",
                abstract="x",
            ),
        )
    )
    answer = "Two studies agree (PMID: 39123456, PMID: 12345678)."
    assert unverified_citations(answer, registry) == ["12345678"]


def test_trailing_punctuation_is_not_part_of_a_doi() -> None:
    registry = CitationRegistry()
    registry.register(
        (
            Article(
                pmid="",
                pmcid="",
                doi="10.1001/jama.2024.1234",
                title="t",
                authors="a",
                journal="j",
                year="2024",
                abstract="x",
            ),
        )
    )
    assert unverified_citations("See 10.1001/jama.2024.1234.", registry) == []


def test_an_answer_with_no_citations_is_clean() -> None:
    assert unverified_citations("No literature was consulted.", CitationRegistry()) == []


@pytest.mark.asyncio
async def test_journal_comes_from_the_nested_core_shape() -> None:
    """Regression: `resultType=core` has no top-level `journalTitle` — it nests
    the journal under `journalInfo.journal.title`. Reading the flat key silently
    produced an empty journal on every real result."""
    client = _client(lambda r: httpx.Response(200, json=SEARCH_PAYLOAD))
    articles = await client.search_literature("RSV")
    assert articles[0].journal == "JAMA"


@pytest.mark.asyncio
async def test_journal_falls_back_to_the_lite_shape() -> None:
    payload = {"resultList": {"result": [{"pmid": "1", "journalTitle": "Lancet"}]}}
    client = _client(lambda r: httpx.Response(200, json=payload))
    articles = await client.search_literature("x")
    assert articles[0].journal == "Lancet"
