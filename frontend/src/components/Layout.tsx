import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { clearToken } from "../api/client";

const NAV_ITEMS = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/jobs", label: "Jobs" },
  { to: "/profile", label: "Profile" },
  { to: "/settings", label: "Settings" },
  { to: "/sources", label: "Sources" },
];

export function Layout() {
  const navigate = useNavigate();

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <nav className="mx-auto flex max-w-4xl items-center gap-1 px-4 py-3">
          <span className="mr-4 font-semibold">Job Intelligence Platform</span>
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `rounded px-3 py-1.5 text-sm ${
                  isActive ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
          <button
            onClick={() => {
              clearToken();
              navigate("/login");
            }}
            className="ml-auto rounded px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100"
          >
            Log out
          </button>
        </nav>
      </header>
      <main className="mx-auto max-w-4xl px-4 py-8">
        <Outlet />
      </main>
    </div>
  );
}
