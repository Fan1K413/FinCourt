import { StrictMode, useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

import { Library, type Location } from "./Library";
import { Impact } from "./Impact";
import { Knowledge } from "./Knowledge";
import { Reviews } from "./Reviews";
import { request, type Disclosure } from "./api";
import "./styles.css";

const tabs = [
  { id: "reviews", label: "观点", title: "观点审查" },
  { id: "library", label: "资料", title: "资料库" },
  { id: "impact", label: "变化", title: "变化影响" },
  { id: "knowledge", label: "版本", title: "知识版本" },
] as const;
type Tab = (typeof tabs)[number]["id"];

function App() {
  const [documents, setDocuments] = useState<Disclosure[]>([]);
  const [error, setError] = useState("");
  const [mode, setMode] = useState("");
  const [tab, setTab] = useState<Tab>("reviews");
  const [revision, setRevision] = useState(0);
  const [target, setTarget] = useState<{ claim: string; run: string } | null>(null);
  const [location, setLocation] = useState<Location | null>(null);
  const refresh = useCallback(() => {
    request<Disclosure[]>("/api/documents").then(
      items => { setDocuments(items); setError(""); },
      problem => setError(problem.message),
    );
  }, []);

  useEffect(refresh, [refresh]);
  useEffect(() => {
    request<{ mode: string }>("/health").then(info => setMode(info.mode), problem => setError(problem.message));
  }, []);

  function openCitation(place: Location) {
    setLocation(place);
    navigate("library");
  }

  function navigate(next: Tab) {
    setTab(next);
    window.scrollTo(0, 0);
  }

  function updateData() {
    refresh();
    setRevision(value => value + 1);
  }

  return (
    <>
      <header className="top">
        <div className="top-inner">
          <button type="button" className="brand" onClick={() => navigate("reviews")}>
            <span className="brand-mark" aria-hidden="true">明</span>
            <span>FinCourt</span>
          </button>
          <nav className="navigation" aria-label="工作台">
            {tabs.map(item => (
              <button key={item.id} type="button" aria-label={item.title} aria-controls={item.id}
                aria-current={tab === item.id ? "page" : undefined} onClick={() => navigate(item.id)}>
                {item.label}
              </button>
            ))}
          </nav>
          {mode && <span className="mode" data-mode={mode} title={mode === "fixture" ? "离线模式" : "实时模式"}>{mode}</span>}
        </div>
      </header>
      <main>
        {error && <p className="error" role="alert">{error}</p>}
        <div className="workspace-view" id="reviews" hidden={tab !== "reviews"}><Reviews mode={mode} target={target} revision={revision} openCitation={openCitation} /></div>
        <div className="workspace-view" id="library" hidden={tab !== "library"}><Library documents={documents} location={location} visible={tab === "library"} onImport={updateData} /></div>
        <div className="workspace-view" id="impact" hidden={tab !== "impact"}><Impact mode={mode} revision={revision} onImport={updateData}
          onReview={(claim, run) => { setTarget({ claim, run }); navigate("reviews"); }} /></div>
        <div className="workspace-view" id="knowledge" hidden={tab !== "knowledge"}><Knowledge visible={tab === "knowledge"} openCitation={openCitation} /></div>
      </main>
    </>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
