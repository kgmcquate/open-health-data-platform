"""File a GitHub issue on a user's behalf, from the hub chat.

The hub's own chat page reaches this endpoint as a normal REST route, and the
configured `ohdp-tools` connection is pointed at the narrow OpenAPI spec this
module mounts, so its model sees exactly one tool. That is the whole reason
this lives in hub-api rather than in a separate MCP server — one
implementation, one set of guardrails, two callers.

**This is the first write tool in the platform.** Everything else a chat user
can reach is read-only: Cube queries, catalog lookups, literature search. This
one creates public, durable, human-visible artifacts in a repository, driven by
a model that is reading text supplied by that same user. The guardrails below
are therefore the substance of this module and the GitHub call is the
afterthought:

  - `_redact` runs over every string before it leaves the process, because chat
    transcripts contain whatever the user pasted into them.
  - `_is_duplicate` costs one API call and stops the failure mode a model is
    most prone to: filing the same report on each retry.
  - `_rate_limited` is a spam ceiling, not a quota. See its docstring for what
    the shared chatbot bucket does and does not protect.
  - `report_issue`'s daily-cap check (`OHDP_FREE_DAILY_ISSUES`/
    `OHDP_PLUS_DAILY_ISSUES`) is the actual per-user quota, on top of that
    ceiling — one free report a day, a few more on a plus tier. It only
    applies to a reporter we can name, which today means the browser Support
    page; a chat-initiated report is always anonymous (see `get_reporter`).
  - Titles and bodies are truncated rather than rejected, so a long transcript
    still produces a usable report instead of an error the model will retry.

The token is a fine-grained PAT scoped to issues:write on one repository
(`OHDP_GITHUB_TOKEN`). Nothing here can read code, merge anything, or reach a
second repo, and that is deliberate — the blast radius of this endpoint is
"noisy issues in one tracker", which is recoverable by deleting them.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.engine import Engine

from hub_api import db
from hub_api.chat import issue_allowance
from ohdp_shared import get_logger, settings

log = get_logger(__name__)

GITHUB_API = "https://api.github.com"
GITHUB_TIMEOUT_SECONDS = 10.0

# Every issue opened through this endpoint carries LABEL, so the tracker can be
# filtered — and, if this is ever abused, drained — in one query. The per-source
# label is added alongside it so "the chatbot filed this" is visible without
# opening the issue.
LABEL = "user-reported"
SOURCE_LABELS = {"web": "source:web", "chatbot": "source:chatbot"}
# `kind` is a closed set on the request model; this maps it onto the label names
# the tracker already uses, so reports sort in with hand-filed issues.
KIND_LABELS = {"bug": "bug", "data-quality": "data-quality", "feature": "enhancement"}

# GitHub's own ceilings are far higher (256 chars / 65536 bytes); these are ours.
# A report longer than this is a transcript dump, and truncating it loses
# nothing a maintainer would have read.
MAX_TITLE = 120
MAX_BODY = 4000

RATE_LIMIT = 10
RATE_WINDOW_SECONDS = 3600.0

# Token shapes worth catching before they reach a public repository. This is a
# net, not a filter: it exists because a user pasting an error message that
# happens to contain their own key should not have it published, and it is not
# a reason to relax anything else. Anything not matched here is published as
# written, which is why the tool description tells the model to summarize rather
# than paste.
_SECRET_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    re.compile(
        r"-----BEGIN[ A-Z]*PRIVATE KEY-----.*?-----END[ A-Z]*PRIVATE KEY-----",
        re.DOTALL,
    ),
)

_recent_reports: dict[str, deque[float]] = defaultdict(deque)


class IssueRequest(BaseModel):
    title: str = Field(min_length=8, max_length=300)
    body: str = Field(min_length=20, max_length=20000)
    kind: str = Field(default="bug", pattern="^(bug|data-quality|feature)$")


class IssueResponse(BaseModel):
    number: int
    url: str
    duplicate: bool


@dataclass(frozen=True)
class Reporter:
    """Who is filing, and how far we can actually vouch for it.

    `source` is derived from *which* credential arrived, never from anything the
    caller asserts about itself — there is no request field that sets it.
    """

    source: str
    email: str | None
    # "free" for a chatbot reporter (no session to read a tier from) — never
    # read for one anyway, since `report_issue`'s daily cap only applies when
    # `email` is set. See that check for why an anonymous reporter isn't
    # given the benefit of the doubt on tier.
    tier: str = "free"

    @property
    def rate_key(self) -> str:
        return self.email or self.source

    @property
    def attribution(self) -> str:
        return self.email or "an anonymous chat user"


def get_reporter(
    request: Request,
    # include_in_schema=False: this carries the ohdp-tools connection's shared
    # bearer, an infrastructure credential proving "this is the chat surface"
    # — not something the model should ever see as a fillable argument. Left
    # visible, FastMCP.from_openapi (hub_api.tool_connections) turns every
    # declared parameter into an editable MCP tool arg, and a model with no
    # real token will supply something for it — we saw one submit literally
    # "Bearer " (empty), which h11 then rejects as an illegal header value
    # before the request ever reaches this dependency. Hiding it from the
    # spec still lets FastAPI read the real header off the request; it stops
    # the model from ever being offered the chance to set it.
    authorization: Annotated[str | None, Header(include_in_schema=False)] = None,
) -> Reporter:
    """Resolve the caller to one of two identities, by credential.

    A browser carries the hub's own signed session cookie (hub_api.auth) — the
    same one `chat.get_user_email` trusts, since `tools_app` is mounted on the
    same ASGI app and shares its SessionMiddleware scope.

    The configured tool connection arrives by cluster DNS with no wall in
    front of it, so it carries a shared bearer token instead. That token proves
    the *caller* is the chat surface. It proves nothing about which human is
    typing, so those issues are attributed anonymously rather than to an
    identity we would be inventing.
    """
    user = request.session.get("user")
    if user and user.get("email"):
        return Reporter(source="web", email=str(user["email"]), tier=str(user.get("tier", "free")))

    token = settings.tools_auth_token
    scheme, _, presented = (authorization or "").partition(" ")
    if token and scheme.lower() == "bearer" and presented.strip() == token:
        return Reporter(source="chatbot", email=None)

    raise HTTPException(401, "Not signed in.")


def get_engine(request: Request) -> Engine:
    """`request.app.state.engine` — resolves to `tools_app.state.engine` for
    the chat surface's route and `app.state.engine` for the browser's, same
    mechanism `hub_api.main`'s lifespan comment describes: a mounted app
    resets `scope["app"]` to itself, and both are set to the same pool."""
    engine: Engine | None = getattr(request.app.state, "engine", None)
    if engine is None:
        raise HTTPException(503, "The database is not available.")
    return engine


def _redact(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _normalize(title: str) -> str:
    """Collapse a title to what it is *about*, for comparison only."""
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def _rate_limited(key: str) -> bool:
    """Sliding window over recent reports, in process memory.

    In process is sufficient because both charts that serve this run a single
    replica by design; it becomes wrong the moment either scales out, and the
    replacement then is a counter in Postgres, not a stickier load balancer.

    Every chatbot report shares one bucket, since `get_reporter` deliberately
    cannot tell those users apart. That makes this a global ceiling on how fast
    the chat surface can fill the tracker — which is the property worth having —
    at the cost that one abusive user locks out the rest until the window rolls.
    """
    now = time.monotonic()
    window = _recent_reports[key]
    while window and now - window[0] > RATE_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= RATE_LIMIT:
        return True
    window.append(now)
    return False


async def _open_issues(client: httpx.AsyncClient, repo: str) -> list[dict[str, object]]:
    response = await client.get(
        f"{GITHUB_API}/repos/{repo}/issues",
        params={"state": "open", "labels": LABEL, "per_page": 100},
    )
    response.raise_for_status()
    return list(response.json())


def _duplicate_of(title: str, existing: list[dict[str, object]]) -> dict[str, object] | None:
    wanted = _normalize(title)
    for issue in existing:
        if _normalize(str(issue.get("title", ""))) == wanted:
            return issue
    return None


def _compose_body(body: str, reporter: Reporter, kind: str) -> str:
    return (
        f"{body}\n\n---\n"
        f"Reported from the {reporter.source} chat surface by {reporter.attribution}. "
        f"Filed automatically by hub-api; the reporter's wording may have been "
        f"summarized by the assistant, and category was recorded as `{kind}`."
    )


async def report_issue(body: IssueRequest, reporter: Reporter, engine: Engine) -> IssueResponse:
    """Create the issue, or return the open one it duplicates."""
    repo = settings.github_issues_repo
    if not repo or not settings.github_token:
        raise HTTPException(503, "Issue reporting is not configured on this deployment.")

    if _rate_limited(reporter.rate_key):
        raise HTTPException(
            429,
            "Too many issues reported recently. Please try again later, or open "
            f"one directly at https://github.com/{repo}/issues.",
        )

    # A per-identity daily cap, on top of the hourly ceiling above — that one
    # is a spam brake shared by every anonymous chatbot caller, this one is
    # "how many *new* issues can this person file today." Only a reporter we
    # can actually name gets one; an anonymous chatbot report has no email to
    # key it on, so it stays covered by the hourly ceiling alone.
    if reporter.email is not None:
        allowed = issue_allowance(reporter.tier)
        if db.issues_today(engine, reporter.email) >= allowed:
            raise HTTPException(
                429,
                f"Daily limit of {allowed} issue report(s) reached. Try again tomorrow, "
                f"or open one directly at https://github.com/{repo}/issues.",
            )

    title = _truncate(_redact(body.title), MAX_TITLE)
    detail = _truncate(_redact(body.body), MAX_BODY)
    labels = [LABEL, SOURCE_LABELS[reporter.source], KIND_LABELS[body.kind]]

    headers = {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=GITHUB_TIMEOUT_SECONDS, headers=headers) as client:
        try:
            existing = _duplicate_of(title, await _open_issues(client, repo))
            if existing is not None:
                log.info("issue_duplicate", number=existing.get("number"), source=reporter.source)
                return IssueResponse(
                    number=int(str(existing["number"])),
                    url=str(existing["html_url"]),
                    duplicate=True,
                )

            response = await client.post(
                f"{GITHUB_API}/repos/{repo}/issues",
                json={
                    "title": title,
                    "body": _compose_body(detail, reporter, body.kind),
                    "labels": labels,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # The reason is ours, not GitHub's: a raised-for-status body can
            # carry the repo path and rate-limit detail, and this string reaches
            # a chat user.
            log.error("issue_not_filed", error=str(exc), source=reporter.source)
            raise HTTPException(
                502, "GitHub could not be reached. The issue was not filed."
            ) from exc

    created = response.json()
    log.info("issue_filed", number=created["number"], source=reporter.source, kind=body.kind)
    if reporter.email is not None:
        db.record_filed_issue(engine, user_email=reporter.email, github_number=created["number"])
    return IssueResponse(number=created["number"], url=created["html_url"], duplicate=False)


# --- the surface the browser sees ------------------------------------------
#
# The hub's own Support page (apps/web/src/pages/Support.tsx) posts here
# directly, on the main app rather than the `/tools` sub-app below — this is a
# normal `/api` route for a signed-in human, not an operation the chat model
# should ever see in its narrowed spec. `get_reporter` already handles a
# plain browser session (source="web"), so it is reused as-is: the same
# guardrails (rate limit, daily cap, dedup, redaction, truncation) apply to
# both callers.

router = APIRouter(prefix="/api", tags=["support"])


@router.post("/support/issue", summary="Report a problem")
async def submit_issue(
    body: IssueRequest,
    reporter: Annotated[Reporter, Depends(get_reporter)],
    engine: Annotated[Engine, Depends(get_engine)],
) -> IssueResponse:
    return await report_issue(body, reporter, engine)


# --- the surface the chat sees --------------------------------------------
#
# A separate FastAPI app, mounted at /tools, purely so that its `/openapi.json`
# describes this one operation. The configured tool connection turns *every*
# operation in the spec it is given into a callable tool, so pointing it at
# hub-api's root spec would hand the model `POST /api/chat` — a chat endpoint
# able to invoke itself — plus `/api/me`. Narrowing the spec is the access
# control here; there is no per-operation allowlist on the tool-connection side.

tools_app = FastAPI(
    title="Open Health Data Platform — assistant tools",
    version="1.0.0",
    description="Actions the assistant can take on a user's behalf.",
    # The interactive docs are for the configured tool connection, not a human;
    # the spec is the contract and it is served regardless.
    docs_url=None,
    redoc_url=None,
)


@tools_app.post("/report_issue", operation_id="report_issue", summary="Report a problem")
async def report_issue_tool(
    body: IssueRequest,
    reporter: Annotated[Reporter, Depends(get_reporter)],
    engine: Annotated[Engine, Depends(get_engine)],
) -> IssueResponse:
    """File a GitHub issue about a problem with this platform.

    Use this only when the user has described something wrong with the platform
    itself — a number that looks incorrect, a dataset that is stale or missing,
    a broken page — and has confirmed they want it reported. Never call it to
    answer a question, and never call it twice for the same problem.

    Write `title` as a short summary of the problem, and `body` in your own
    words: what the user was doing, what they expected, and what happened
    instead. Summarize rather than pasting the conversation, and do not include
    credentials, tokens, or personal health information — issues are public.

    Returns the issue number and URL; give the user the URL. If `duplicate` is
    true, this was already reported and the existing issue is returned — tell
    the user that rather than filing again.
    """
    return await report_issue(body, reporter, engine)
