import { lazy, Suspense } from "react";
import { Route, Routes, useLocation } from "react-router-dom";
import Navbar from "./components/Navbar";
import Footer from "./components/Footer";
import Home from "./pages/Home";
import Topics from "./pages/Topics";

// Heavy dependencies (assistant-ui, vega-embed) stay out of the main bundle.
const TopicDetail = lazy(() => import("./pages/TopicDetail"));
const Dashboards = lazy(() => import("./pages/Dashboards"));
const DashboardDetail = lazy(() => import("./pages/DashboardDetail"));
const DashboardBuilder = lazy(() => import("./pages/DashboardBuilder"));
const Chat = lazy(() => import("./pages/Chat"));
const Privacy = lazy(() => import("./pages/Privacy"));
const Terms = lazy(() => import("./pages/Terms"));
const Support = lazy(() => import("./pages/Support"));
const Billing = lazy(() => import("./pages/Billing"));
const Search = lazy(() => import("./pages/Search"));
const Admin = lazy(() => import("./pages/Admin"));

export default function App() {
  const location = useLocation();
  const isChat = location.pathname === "/chat";

  return (
    <div className="min-h-screen flex flex-col bg-base-100 text-base-content">
      <Navbar />
      <main className="flex-1">
        <Suspense
          fallback={
            <div className="flex justify-center py-24">
              <span className="loading loading-spinner loading-lg" />
            </div>
          }
        >
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/topics" element={<Topics />} />
            <Route path="/topics/:name" element={<TopicDetail />} />
            <Route path="/dashboards" element={<Dashboards />} />
            {/* Listed before `:name` to read in the intended order — router
             * ranking already prefers the static segment over the dynamic one,
             * so "new" is the builder and never a dashboard to look up. */}
            <Route path="/dashboards/new" element={<DashboardBuilder />} />
            <Route path="/dashboards/:name" element={<DashboardDetail />} />
            <Route path="/chat" element={<Chat />} />
            <Route path="/search" element={<Search />} />
            <Route path="/privacy" element={<Privacy />} />
            <Route path="/terms" element={<Terms />} />
            <Route path="/support" element={<Support />} />
            <Route path="/billing" element={<Billing />} />
            <Route path="/admin" element={<Admin />} />
            <Route
              path="*"
              element={
                <div className="hero py-24">
                  <div className="hero-content text-center">
                    <div>
                      <h1 className="text-5xl font-bold">404</h1>
                      <p className="py-4 opacity-70">That page is off the chart.</p>
                    </div>
                  </div>
                </div>
              }
            />
          </Routes>
        </Suspense>
      </main>
      {!isChat && <Footer />}
    </div>
  );
}
