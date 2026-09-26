import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CornerDownLeft, Search } from "lucide-react";
import { fetchSearch, type SearchResults } from "../lib/api";

/** How long to wait after the last keystroke before asking the server. Long
 * enough that typing a word is one request rather than five, short enough that
 * the dropdown still feels like it is keeping up. */
const DEBOUNCE_MS = 500;

/** One row in the dropdown, flattened out of the grouped `SearchResults` so
 * arrow-key navigation is an index into a single list rather than a walk
 * across four of them. `href` is an OpenMetadata or publisher link and opens
 * in a new tab; `to` is a route inside the hub. */
interface Hit {
  key: string;
  group: string;
  label: string;
  detail: string;
  to?: string;
  href?: string;
}

function flatten(results: SearchResults): Hit[] {
  return [
    ...results.topics.map((topic) => ({
      key: `topic:${topic.id}`,
      group: "Topics",
      label: topic.name,
      detail: topic.description,
      to: `/topics/${encodeURIComponent(topic.name)}`,
    })),
    ...results.dashboards.map((dashboard) => ({
      key: `dashboard:${dashboard.id}`,
      group: "Dashboards",
      label: dashboard.title,
      detail: dashboard.description,
      to: `/dashboards/${encodeURIComponent(dashboard.name)}`,
    })),
    ...results.assets.map((asset) => ({
      key: `asset:${asset.id}`,
      // The entity type is the useful label here: "metric" and "table" are
      // different enough things that grouping them together would hide it.
      group: asset.entity_type === "metric" ? "Metrics" : "Data assets",
      label: asset.name,
      detail: asset.description,
      href: asset.catalog_url,
    })),
    ...results.literature.map((item) => ({
      key: `paper:${item.id}`,
      group: "Literature",
      label: item.title,
      detail: [item.authors, item.venue, item.year].filter(Boolean).join(" · "),
      href: item.url,
    })),
  ];
}

/** The platform's search box — the Home page's hero and the top of `/search`.
 * One debounced call to `/api/search`, results grouped in a dropdown, and
 * Enter on the query itself falling through to the full `/search` page for
 * anything the dropdown's per-section cap cut off.
 *
 * `size="lg"` is the hero's version: the same box a size step up (about 25%
 * taller), so it reads as the page's first thing to do. `initialQuery`
 * pre-fills the box, which is how `/search` shows the query it is displaying
 * results for. */
export default function SearchBar({
  size = "md",
  initialQuery = "",
  autoFocus = false,
}: {
  size?: "md" | "lg";
  initialQuery?: string;
  autoFocus?: boolean;
}) {
  const navigate = useNavigate();
  const [query, setQuery] = useState(initialQuery);
  const large = size === "lg";
  const [results, setResults] = useState<SearchResults | null>(null);
  const [loading, setLoading] = useState(false);
  // Closed on mount even with an `initialQuery`: on `/search` the results are
  // already the page, and a dropdown repeating them would only cover them.
  const [open, setOpen] = useState(false);
  // Which row Enter would follow: -1 is "none", and submits the query instead.
  const [active, setActive] = useState(-1);
  const container = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);

  const trimmed = query.trim();

  useEffect(() => {
    if (!trimmed) {
      setResults(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    let cancelled = false;
    const timer = setTimeout(() => {
      fetchSearch(trimmed)
        .then((value) => {
          // `cancelled` covers the in-flight case the debounce cannot: a
          // slow response for "cov" must not overwrite a fast one for "covid".
          if (!cancelled) {
            setResults(value);
            setActive(-1);
          }
        })
        .catch(() => !cancelled && setResults(null))
        .finally(() => !cancelled && setLoading(false));
    }, DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [trimmed]);

  // Clicking anywhere else closes the dropdown but keeps the query, so coming
  // back to the box does not mean retyping.
  useEffect(() => {
    const onPointerDown = (event: MouseEvent) => {
      if (!container.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, []);

  // ⌘K / Ctrl-K from anywhere on a page that has this box. Unadvertised — a
  // shortcut for people who already expect it, not the documented way in.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "k" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        input.current?.focus();
        input.current?.select();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  const hits = results ? flatten(results) : [];

  const go = (hit: Hit) => {
    setOpen(false);
    input.current?.blur();
    if (hit.to) navigate(hit.to);
    else if (hit.href) window.open(hit.href, "_blank", "noopener,noreferrer");
  };

  const submit = () => {
    if (!trimmed) return;
    setOpen(false);
    input.current?.blur();
    navigate(`/search?q=${encodeURIComponent(trimmed)}`);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      setOpen(false);
      input.current?.blur();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (hits.length === 0) return;
      event.preventDefault();
      setOpen(true);
      const step = event.key === "ArrowDown" ? 1 : -1;
      // Wraps through -1, which is the query row: holding ArrowUp from the top
      // gets you back to "search everything" rather than trapping you.
      setActive((current) => {
        const next = current + step;
        if (next >= hits.length) return -1;
        if (next < -1) return hits.length - 1;
        return next;
      });
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      if (active >= 0 && hits[active]) go(hits[active]);
      else submit();
    }
  };

  // Headings are drawn from the flattened list rather than the grouped one so
  // a section whose hits were all filtered out cannot leave a stray heading.
  let lastGroup = "";

  return (
    <div ref={container} className={`relative w-full ${large ? "max-w-2xl mx-auto" : "max-w-2xl"}`}>
      <div className="flex items-center gap-1">
        <label
          className={`input input-bordered flex items-center grow ${
            large ? "gap-2 shadow" : "input-sm gap-2"
          }`}
        >
          <Search className={`${large ? "h-5 w-5" : "h-4 w-4"} opacity-60`} aria-hidden />
          <input
            ref={input}
            type="search"
            className="grow"
            autoFocus={autoFocus}
            placeholder={
              large
                ? "Search diseases, metrics, dashboards, papers…"
                : "Search topics, dashboards, data…"
            }
            aria-label="Search the platform"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={onKeyDown}
          />
          {loading && (
            <span className={`loading loading-spinner ${large ? "loading-sm" : "loading-xs"}`} />
          )}
        </label>
        {/* The pointer's version of pressing Enter — same `submit`, so the
            button and the key cannot drift apart. Outside the `<label>`: a
            click inside one is forwarded to the input it labels, which would
            steal this one. */}
        <button
          type="button"
          className={`btn btn-primary ${large ? "btn-square shadow" : "btn-sm btn-square"}`}
          aria-label="Search"
          title="Search"
          disabled={!trimmed}
          onClick={submit}
        >
          <CornerDownLeft className={large ? "h-5 w-5" : "h-4 w-4"} />
        </button>
      </div>

      {open && trimmed.length > 0 && (
        <div className="absolute left-0 right-0 top-full mt-2 max-h-[60vh] overflow-y-auto rounded-box border border-base-300 bg-base-100 text-left shadow-xl z-50">
          <ul className="menu w-full p-2">
            {hits.map((hit, index) => {
              const heading = hit.group !== lastGroup ? hit.group : null;
              lastGroup = hit.group;
              return (
                <li key={hit.key}>
                  {heading && (
                    <h2 className="menu-title px-3 pt-2 pb-1 text-xs uppercase tracking-wide">
                      {heading}
                    </h2>
                  )}
                  <button
                    type="button"
                    className={`flex flex-col items-start gap-0 text-left ${
                      index === active ? "menu-active" : ""
                    }`}
                    // Mouse and keyboard share one highlight, so hovering
                    // then pressing Enter follows what is under the cursor.
                    onMouseEnter={() => setActive(index)}
                    onClick={() => go(hit)}
                  >
                    <span className="font-medium truncate w-full">{hit.label}</span>
                    {hit.detail && (
                      <span className="text-xs opacity-60 line-clamp-1 w-full">{hit.detail}</span>
                    )}
                  </button>
                </li>
              );
            })}

            {!loading && hits.length === 0 && (
              <li className="px-3 py-2 text-sm opacity-60">No matches.</li>
            )}

            <li>
              <button
                type="button"
                className={`justify-between ${active === -1 ? "menu-active" : ""}`}
                onMouseEnter={() => setActive(-1)}
                onClick={submit}
              >
                <span>
                  Search everything for <span className="font-medium">{trimmed}</span>
                </span>
                <kbd className="kbd kbd-xs">↵</kbd>
              </button>
            </li>
          </ul>
        </div>
      )}
    </div>
  );
}
