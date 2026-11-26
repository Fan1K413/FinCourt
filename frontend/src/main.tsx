import { StrictMode, useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

import { Library, type Location } from "./Library";
import { Impact } from "./Impact";
import { Knowledge } from "./Knowledge";
import { Reviews } from "./Reviews";
import { request, type Disclosure } from "./api";
import "./styles.css";

function App() {
  const [documents, setDocuments] = useState<Disclosure[]>([]);
  const [error, setError] = useState("");
  const [mode, setMode] = useState("");
  const [tab, setTab] = useState<"reviews" | "library" | "impact" | "knowledge">("reviews");
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
    setTab("library");
  }

  function updateData() {
    refresh();
    setRevision(value => value + 1);
  }

  return (
    <>
      <header className="top"><div className="top-inner">
        <div className="brand"><span className="brand-mark" aria-hidden="true">证</span><strong>明证 <span>FinCourt</span></strong></div>
        <nav aria-label="工作台">
          <button aria-current={tab === "reviews" ? "page" : undefined} onClick={() => setTab("reviews")}>观点审查</button>
          <button aria-current={tab === "library" ? "page" : undefined} onClick={() => { setLocation(null); setTab("library"); }}>资料库</button>
          <button aria-current={tab === "impact" ? "page" : undefined} onClick={() => setTab("impact")}>变化影响</button>
          <button aria-current={tab === "knowledge" ? "page" : undefined} onClick={() => setTab("knowledge")}>知识版本</button>
        </nav>
        <span className="mode">{mode === "fixture" ? "fixture · 离线模式" : mode}</span>
      </div></header>
      <main>
        {error && <p className="error" role="alert">{error}</p>}
        <div hidden={tab !== "reviews"}><Reviews mode={mode} target={target} revision={revision} openCitation={openCitation} /></div>
        <div hidden={tab !== "library"}><Library documents={documents} location={location} onImport={updateData} /></div>
        <div hidden={tab !== "impact"}><Impact mode={mode} revision={revision} onImport={updateData}
          onReview={(claim, run) => { setTarget({ claim, run }); setTab("reviews"); }} /></div>
        <div hidden={tab !== "knowledge"}><Knowledge visible={tab === "knowledge"} openCitation={openCitation} /></div>
      </main>
    </>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
