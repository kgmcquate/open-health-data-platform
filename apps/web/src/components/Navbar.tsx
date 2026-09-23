import { NavLink, Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { THEMES, applyTheme, storedTheme } from "../theme";
import { useState } from "react";
import GearIcon from "./icons/GearIcon";
import LogoMark from "./icons/LogoMark";
import SearchBar from "./SearchBar";

const NAV_ITEMS = [
  { to: "/", label: "Home", end: true },
  { to: "/topics", label: "Topics" },
  { to: "/dashboards", label: "Dashboards" },
  { to: "/chat", label: "Chat" },
];

export default function Navbar() {
  const { user, signIn, signOut } = useAuth();
  const [theme, setTheme] = useState(storedTheme() ?? "ohdp");

  return (
    <div className="navbar bg-base-100/90 backdrop-blur border-b border-base-300 sticky top-0 z-40 px-4">
      <div className="flex-1 min-w-0 flex items-center gap-4">
        <Link to="/" className="flex items-center gap-2 font-bold text-lg shrink-0">
          <LogoMark className="h-12 w-12" />
          <span className="hidden sm:inline whitespace-nowrap">
            Open Health <span className="text-primary">Data Platform</span>
          </span>
        </Link>
        {/* On every width: it is the only search input anywhere now that the
            /search page has none of its own. The wordmark gives up its text
            below `sm` to make the room. */}
        <div className="flex grow min-w-0">
          <SearchBar />
        </div>
      </div>

      <nav className="hidden lg:flex">
        <ul className="menu menu-horizontal px-1 gap-1">
          {NAV_ITEMS.map((item) => (
            <li key={item.to}>
              <NavLink
                to={item.to}
                end={item.end}
                className={({ isActive }) => (isActive ? "menu-active" : "")}
              >
                {item.label}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>

      <div className="flex-none gap-2">
        {user === undefined ? (
          <span className="loading loading-spinner loading-sm" />
        ) : user === null ? (
          <button className="btn btn-primary btn-sm" onClick={signIn}>
            Sign in
          </button>
        ) : null}

        <div className="dropdown dropdown-end lg:hidden">
          <button tabIndex={0} className="btn btn-ghost btn-sm" aria-label="Menu">
            ☰
          </button>
          <ul className="dropdown-content menu bg-base-100 rounded-box z-50 w-52 p-2 shadow border border-base-300">
            {NAV_ITEMS.map((item) => (
              <li key={item.to}>
                <NavLink to={item.to} end={item.end}>
                  {item.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </div>

        {/* The signed-in identity lives in here rather than in the bar itself:
            an email is far wider than the gear, and it is only needed on the
            way to signing out. */}
        <div className="dropdown dropdown-end">
          <button tabIndex={0} className="btn btn-ghost btn-sm btn-circle" aria-label="Settings">
            <GearIcon className="h-5 w-5" />
          </button>
          <div className="dropdown-content menu bg-base-100 rounded-box z-50 w-64 p-4 shadow border border-base-300 gap-2">
            {user && (
              <div className="flex flex-col gap-1 pb-1">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="font-medium truncate">{user.name || user.email}</span>
                  {user.tier === "paid" && <span className="badge badge-accent badge-sm">pro</span>}
                  {/* So it is never a surprise that this session can delete things. */}
                  {user.is_admin && <span className="badge badge-outline badge-sm">admin</span>}
                </div>
                {user.name && (
                  <span className="text-xs opacity-70 truncate">{user.email}</span>
                )}
              </div>
            )}

            <label className="label" htmlFor="theme-select">
              <span className="label-text">Appearance</span>
            </label>
            <select
              id="theme-select"
              className="select select-bordered select-sm"
              value={theme}
              aria-label="Theme"
              onChange={(e) => {
                setTheme(e.target.value as typeof theme);
                applyTheme(e.target.value as typeof theme);
              }}
            >
              {THEMES.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
                </option>
              ))}
            </select>

            {user && (
              <>
                <div className="divider my-1" />
                <button className="btn btn-ghost btn-sm justify-start" onClick={() => void signOut()}>
                  Sign out
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
