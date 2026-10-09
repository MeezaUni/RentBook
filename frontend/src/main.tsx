import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router";
import { Layout } from "./components/Layout";
import { Loading } from "./components/ui";
import "./index.css";
import { AuthProvider, useAuth } from "./lib/auth";
import { BankPage } from "./pages/Bank";
import { BuildingPage, BuildingsPage } from "./pages/Buildings";
import { CashbookPage } from "./pages/Cashbook";
import { DashboardPage } from "./pages/Dashboard";
import { FlatPage } from "./pages/Flat";
import { Login } from "./pages/Login";
import { MaintenancePage } from "./pages/Maintenance";
import { ReportsPage } from "./pages/Reports";
import { RentLedgerPage } from "./pages/RentLedger";
import { SalaryPage } from "./pages/Salary";
import { SettingsPage } from "./pages/Settings";
import { TenantsPage } from "./pages/Tenants";

function Gate() {
  const { user, checking } = useAuth();
  if (checking) return <Loading label="Checking session…" />;
  if (!user) return <Login />;
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<DashboardPage />} />
        <Route path="buildings" element={<BuildingsPage />} />
        <Route path="buildings/:code" element={<BuildingPage />} />
        <Route path="flats/:flatId" element={<FlatPage />} />
        <Route path="rent" element={<RentLedgerPage />} />
        <Route path="tenants" element={<TenantsPage />} />
        <Route path="bank" element={<BankPage />} />
        <Route path="cash" element={<CashbookPage />} />
        <Route path="maintenance" element={<MaintenancePage />} />
        <Route path="salary" element={<SalaryPage />} />
        <Route path="reports" element={<ReportsPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<p className="text-muted">Page not found.</p>} />
      </Route>
    </Routes>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <Gate />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
