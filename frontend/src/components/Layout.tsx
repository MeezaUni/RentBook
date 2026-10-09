import {
  Banknote, Building2, FileBarChart, HandCoins, Landmark, LayoutDashboard, LogOut, Menu, NotebookTabs, Settings,
  UserRound, Users, Wrench, X,
} from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router";
import { useAuth } from "../lib/auth";
import { cx } from "./ui";

const NAV = [
  { to: "/", label: "Dashboard", Icon: LayoutDashboard, end: true },
  { to: "/buildings", label: "Buildings", Icon: Building2 },
  { to: "/rent", label: "Rent Ledger", Icon: NotebookTabs },
  { to: "/tenants", label: "Tenants", Icon: Users },
  { to: "/bank", label: "Bank Reconciliation", Icon: Landmark },
  { to: "/cash", label: "Cashbook", Icon: Banknote },
  { to: "/maintenance", label: "Maintenance", Icon: Wrench },
  { to: "/salary", label: "Caretaker Salary", Icon: HandCoins },
  { to: "/reports", label: "Reports", Icon: FileBarChart },
  { to: "/settings", label: "Settings", Icon: Settings },
];

function NavItems({ onNavigate }: { onNavigate?: () => void }) {
  const { search } = useLocation();
  // Keep the selected month when moving between pages.
  const month = new URLSearchParams(search).get("month");
  return (
    <nav className="flex flex-col gap-0.5" aria-label="Main">
      {NAV.map(({ to, label, Icon, end }) => (
        <NavLink
          key={to}
          to={month && to !== "/settings" && to !== "/tenants" ? `${to}?month=${month}` : to}
          end={end}
          onClick={onNavigate}
          className={({ isActive }) =>
            cx("flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium",
              isActive ? "bg-teal-50 text-teal-900" : "text-stone-700 hover:bg-stone-100")}
        >
          <Icon className="size-4" aria-hidden />
          {label}
        </NavLink>
      ))}
    </nav>
  );
}

export function Layout() {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  return (
    <div className="min-h-dvh lg:flex">
      <aside className="no-print hidden w-60 shrink-0 flex-col border-r border-line bg-surface px-3 py-4 lg:flex lg:sticky lg:top-0 lg:h-dvh">
        <Brand />
        <div className="mt-4 flex-1 overflow-y-auto">
          <NavItems />
        </div>
        <UserBox name={user?.name} onLogout={logout} />
      </aside>

      <header className="no-print sticky top-0 z-40 flex items-center justify-between border-b border-line bg-surface px-4 py-2 lg:hidden">
        <Brand />
        <button className="rounded-lg p-2 hover:bg-stone-100" onClick={() => setOpen(true)} aria-label="Open menu">
          <Menu className="size-5" />
        </button>
      </header>
      {open && (
        <div className="fixed inset-0 z-50 bg-black/30 lg:hidden" onClick={() => setOpen(false)}>
          <div className="absolute right-0 top-0 flex h-full w-72 flex-col bg-surface p-3 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="mb-3 flex items-center justify-between">
              <Brand />
              <button className="rounded-lg p-2 hover:bg-stone-100" onClick={() => setOpen(false)} aria-label="Close menu">
                <X className="size-5" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto">
              <NavItems onNavigate={() => setOpen(false)} />
            </div>
            <UserBox name={user?.name} onLogout={logout} />
          </div>
        </div>
      )}

      <main className="mx-auto w-full min-w-0 max-w-6xl flex-1 px-4 py-5 sm:px-6 lg:py-8">
        <Outlet />
      </main>
    </div>
  );
}

function Brand() {
  return (
    <div className="flex items-center gap-2 px-2">
      <span className="flex gap-0.5" aria-hidden>
        <span className="h-5 w-2.5 rounded-sm bg-pink-accent" />
        <span className="h-6 w-2.5 rounded-sm bg-blue-accent" />
      </span>
      <span className="text-lg font-semibold tracking-tight">Rentbook</span>
    </div>
  );
}

function UserBox({ name, onLogout }: { name?: string; onLogout: () => void }) {
  return (
    <div className="mt-3 flex items-center justify-between border-t border-line px-2 pt-3 text-sm">
      <span className="flex items-center gap-2 text-stone-700">
        <UserRound className="size-4" aria-hidden /> {name}
      </span>
      <button onClick={onLogout} className="flex items-center gap-1 rounded-md px-2 py-1 text-stone-600 hover:bg-stone-100">
        <LogOut className="size-4" aria-hidden /> Log out
      </button>
    </div>
  );
}
