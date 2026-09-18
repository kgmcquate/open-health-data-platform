import { lazy, Suspense } from "react";
import { Route, Routes } from "react-router-dom";
import Navbar from "./components/Navbar";
import Footer from "./components/Footer";
import Home from "./pages/Home";
import News from "./pages/News";
import DataSources from "./pages/DataSources";

// Heavy dependencies (assistant-ui, vega-embed) stay out of the main bundle.
const Plots = lazy(() => import("./pages/Plots"));
const Literature = lazy(() => import("./pages/Literature"));
const Chat = lazy(() => import("./pages/Chat"));

export default function App() {
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
            <Route path="/news" element={<News />} />
            <Route path="/data-sources" element={<DataSources />} />
            <Route path="/plots" element={<Plots />} />
            <Route path="/literature" element={<Literature />} />
            <Route path="/chat" element={<Chat />} />
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
      <Footer />
    </div>
  );
}
