"""`GET /api/search/summary` — the AI summary at the top of the search page.

One model call, no tools: the route re-runs the same search the page is
showing (`hub_api.content.search_results`), hands the hits to the model, and
gets back a two-or-three sentence overview plus a few questions worth taking
to the chat. The model and its prompt are models.yaml's
`search_summary_model:` section, not one of the chat's `models:`. The
questions are the point — the summary is only as good as the search hits it
was given, and the chat is where a question gets answered from
the semantic layer with sources.

Cost controls, in the order they are applied:

  - **Sign-in required.** `/api/search` itself is public; this is not, because
    every call that misses the cache is a model call.
  - **The chat's daily token gate.** A user already over `token_allowance` gets
    a 429 here too. Summaries are not written to the turn log, so they do not
    count *towards* that allowance — they are small and capped below instead.
  - **A process-local cache per query.** The same query from anyone reuses one
    answer for `CACHE_TTL_SECONDS`; search results change nightly at most.
  - **A per-user sliding window** (`settings.search_summaries_per_hour`) over
    cache misses. In-process, which is enough for the single replica this runs
    as — same caveat as `hub_api.issues._rate_limited`.
"""

from __future__ import annotations

import time
from collections import OrderedDict, defaultdict, deque
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from sqlalchemy.engine import Engine

from hub_api import db
from hub_api.auth import get_user_tier
from hub_api.chat import get_engine, get_user_email, token_allowance
from hub_api.content import search_results
from hub_api.models import SearchSummaryConfig
from ohdp_shared import get_logger, settings

log = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["search"])

CACHE_TTL_SECONDS = 60 * 60
CACHE_MAX_ENTRIES = 512
RATE_WINDOW_SECONDS = 60 * 60
# Hits of each kind handed to the model. The page shows more; the summary only
# needs enough to say what is here.
HITS_PER_SECTION = 6


class SearchSummary(BaseModel):
    summary: str = Field(max_length=1200)
    prompts: list[str] = Field(max_length=3)


_cache: OrderedDict[str, tuple[float, SearchSummary]] = OrderedDict()
_recent: defaultdict[str, deque[float]] = defaultdict(deque)


def _cached(key: str) -> SearchSummary | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    stored_at, value = entry
    if time.monotonic() - stored_at > CACHE_TTL_SECONDS:
        del _cache[key]
        return None
    _cache.move_to_end(key)
    return value


def _store(key: str, value: SearchSummary) -> None:
    _cache[key] = (time.monotonic(), value)
    _cache.move_to_end(key)
    while len(_cache) > CACHE_MAX_ENTRIES:
        _cache.popitem(last=False)


def _rate_limited(user_email: str) -> bool:
    now = time.monotonic()
    window = _recent[user_email]
    while window and now - window[0] > RATE_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= settings.search_summaries_per_hour:
        return True
    window.append(now)
    return False


def get_search_summary_config(request: Request) -> SearchSummaryConfig | None:
    """Built once at startup from models.yaml's `search_summary_model:`
    (`hub_api.models.build_search_summary_model`)."""
    return getattr(request.app.state, "search_summary", None)


def _render_results(results: dict[str, Any]) -> str:
    """The hits as compact lines — names and one-line descriptions, nothing
    the model would need to parse."""

    def lines(title: str, rows: list[str]) -> str:
        return f"{title}:\n" + ("\n".join(f"- {row}" for row in rows) if rows else "- (none)")

    def clip(text: str | None, length: int = 200) -> str:
        text = " ".join((text or "").split())
        return text if len(text) <= length else text[: length - 1] + "…"

    assets = results["assets"][:HITS_PER_SECTION]
    return "\n\n".join(
        [
            lines(
                "Topics",
                [
                    f"{t['name']}: {clip(t['description'])}"
                    for t in results["topics"][:HITS_PER_SECTION]
                ],
            ),
            lines(
                "Dashboards",
                [
                    f"{d['title']}: {clip(d['description'])}"
                    for d in results["dashboards"][:HITS_PER_SECTION]
                ],
            ),
            lines(
                "Datasets and metrics",
                [f"{a['name']} ({a['entity_type']}): {clip(a['description'])}" for a in assets],
            ),
            lines(
                "Literature",
                [
                    f"{p['title']} ({p.get('year') or 'n.d.'})"
                    for p in results["literature"][:HITS_PER_SECTION]
                ],
            ),
        ]
    )


@router.get("/search/summary")
async def search_summary(
    request: Request,
    user_email: Annotated[str, Depends(get_user_email)],
    tier: Annotated[str, Depends(get_user_tier)],
    engine: Annotated[Engine, Depends(get_engine)],
    config: Annotated[SearchSummaryConfig | None, Depends(get_search_summary_config)],
    q: str = Query(min_length=1, max_length=200),
) -> dict[str, Any]:
    """A short overview of what `q` matched, plus three chat questions."""
    key = " ".join(q.lower().split())
    if not key:
        raise HTTPException(400, "Empty query.")

    if db.tokens_today(engine, user_email) >= token_allowance(tier):
        raise HTTPException(429, "You have used today's AI allowance.")

    cached = _cached(key)
    if cached is not None:
        return cached.model_dump()

    if config is None:
        raise HTTPException(503, "No model is configured for AI summaries.")

    if _rate_limited(user_email):
        raise HTTPException(429, "Too many AI summaries this hour — try again later.")

    results = await search_results(request, q, viewer_email=user_email)
    summarizer = Agent(config.model, output_type=SearchSummary, instructions=config.system_prompt)
    try:
        run = await summarizer.run(
            f"Query: {q}\n\n<results>\n{_render_results(results)}\n</results>",
            model_settings={"max_tokens": 600},
        )
    except Exception as exc:  # noqa: BLE001 — any model failure is "no summary", not a 500
        log.warning("search_summary_failed", error=str(exc))
        raise HTTPException(502, "The AI summary is unavailable right now.") from exc

    summary = run.output
    summary.prompts = [p.strip() for p in summary.prompts if p.strip()][:3]
    _store(key, summary)
    usage = run.usage
    log.info(
        "search_summary",
        user=user_email,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
    )
    return summary.model_dump()
