import { Routes, Route, Navigate, useLocation } from "react-router-dom";
import { Navbar } from "./Navbar.jsx";
import { useAuth } from "./AuthContext.jsx";
import { HomePage } from "./pages/HomePage.jsx";
import { LoginPage } from "./pages/LoginPage.jsx";
import { RegisterPage } from "./pages/RegisterPage.jsx";
import { VerifyPage } from "./pages/VerifyPage.jsx";
import { ResetPage } from "./pages/ResetPage.jsx";
import { DashboardPage } from "./pages/DashboardPage.jsx";
import { AdminPage } from "./pages/AdminPage.jsx";
import { ParserPage } from "./pages/ParserPage.jsx";
import { LandingPage } from "./pages/LandingPage.jsx";
import { LiveLabPage } from "./pages/LiveLabPage.jsx";
import { LiveSearchLabPage } from "./pages/LiveSearchLabPage.jsx";
import { SupportWidget } from "./components/SupportWidget.jsx";
import { SupportPage } from "./pages/SupportPage.jsx";
import { WorkspaceLayout } from "./components/WorkspaceLayout.jsx";
import { DownloadHistoryPage } from "./pages/DownloadHistoryPage.jsx";
import { LOCAL_MODE } from "./localMode.js";

function HomeOrLanding() {
  const { me, loading } = useAuth();
  if (LOCAL_MODE) return <HomePage />;
  if (loading) return null;
  return me ? <LiveSearchLabPage /> : <LandingPage />;
}

function AppNavbar() {
  const location = useLocation();
  const workspacePaths = ["/workspace", "/dashboard", "/analysis", "/downloads", "/reviews", "/social", "/support", "/admin", "/live-lab"];
  if (workspacePaths.includes(location.pathname)) return null;
  return <Navbar />;
}

function AppSupport() {
  const { me, loading } = useAuth();
  const location = useLocation();
  const workspacePaths = ["/workspace", "/dashboard", "/analysis", "/downloads", "/reviews", "/social", "/support", "/admin", "/live-lab"];
  if (!loading && me && workspacePaths.includes(location.pathname)) return null;
  return LOCAL_MODE ? null : <SupportWidget />;
}

export function App() {
  return (
    <div className="dm-app">
      <AppNavbar />
      <Routes>
        <Route path="/" element={<HomeOrLanding />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/verify" element={<VerifyPage />} />
        <Route path="/reset" element={<ResetPage />} />
        <Route path="/catalog" element={<HomePage />} />
        <Route path="/workspace" element={<LiveSearchLabPage />} />
        <Route element={<WorkspaceLayout />}>
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/analysis" element={<DownloadHistoryPage analysisOnly />} />
          <Route path="/downloads" element={<DownloadHistoryPage />} />
          <Route path="/reviews" element={<Navigate to="/analysis" replace />} />
          <Route path="/social" element={<Navigate to="/analysis" replace />} />
          <Route path="/support" element={<SupportPage />} />
          <Route path="/live-lab" element={<LiveLabPage />} />
          <Route path="/admin" element={<AdminPage />} />
        </Route>
        <Route path="/parser" element={<ParserPage />} />
        <Route path="/live-search-lab" element={<Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <AppSupport />
    </div>
  );
}
