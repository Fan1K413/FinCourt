import { useEffect, useState } from "react";

import { disclosures, request, sourceLabels, type Disclosure, type PageText, type Source } from "./api";

export interface Location {
  document: string;
  version: string;
  page: number;
  start?: number;
  end?: number;
}

export function Library({ documents, location, onImport }: {
  documents: Disclosure[];
  location: Location | null;
  onImport: () => void;
}) {
  const [source, setSource] = useState<Source | "ALL">("ALL");
  const [selected, setSelected] = useState<Location | null>(location);
  const [text, setText] = useState<PageText | null>(null);
  const [error, setError] = useState("");
  const [upload, setUpload] = useState(false);
  const [uploadState, setUploadState] = useState("");
  const file = documents.find(item => item.id === selected?.document);
  const parsed = file?.parses.find(item => item.version === selected?.version);
  const items = documents.filter(item => source === "ALL" || item.source_kind === source);

  useEffect(() => {
    setSelected(location);
    if (location) setSource("ALL");
  }, [location]);

  useEffect(() => {
    if (!selected) return;
    let active = true;
    const path = "/api/documents/" + encodeURIComponent(selected.document)
      + "/pages/" + selected.page + "?version=" + encodeURIComponent(selected.version);
    setText(null);
    setError("");
    request<PageText>(path).then(
      page => { if (active) setText(page); },
      problem => { if (active) setError(problem.message); },
    );
    return () => { active = false; };
  }, [selected]);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setUploadState("正在导入…");
    try {
      const response = await fetch("/api/documents", { method: "POST", body: new FormData(event.currentTarget) });
      const item = await response.json();
      if (!response.ok) throw new Error(item.detail?.message ?? "请检查 PDF 和元数据");
      setUploadState("已导入 " + item.title);
      setSource("ALL");
      setSelected({ document: item.id, version: item.parses[0].version, page: 1 });
      onImport();
    } catch (problem) {
      setUploadState((problem as Error).message);
    }
  }

  function choose(item: Disclosure) {
    setSelected({ document: item.id, version: item.parses.at(-1)!.version, page: 1 });
  }

  const chars = text ? Array.from(text.text) : [];
  const highlight = selected?.start !== undefined && selected.end !== undefined;

  return (
    <>
      <div className="section-title">
        <div><h1>资料库</h1><p>查看公开披露信息、原文件和解析页。</p></div>
        <button className="secondary" onClick={() => setUpload(!upload)} aria-expanded={upload}>
          {upload ? "收起导入" : "导入 PDF"}
        </button>
      </div>
      {upload && <form className="panel upload" onSubmit={submit}>
        <label>原始 PDF<input type="file" name="file" accept="application/pdf,.pdf" required /></label>
        <label>披露元数据（JSON）
          <textarea name="metadata" rows={6} required placeholder={'{"id":"doc-1","title":"公告名称","subject":"688287","source_kind":"REAL","disclosure_type":"RESULTS","published_date":"2025-02-28"}'} />
        </label>
        <div className="actions"><button type="submit">校验并导入</button><span role="status">{uploadState}</span></div>
      </form>}
      <div className="split">
        <section className="panel">
          <div className="panel-heading"><h2>披露资料</h2>
            <select aria-label="资料来源" value={source} onChange={e => setSource(e.target.value as typeof source)}>
              <option value="ALL">全部来源</option><option value="REAL">真实公告</option><option value="SIMULATED">模拟资料</option>
            </select>
          </div>
          {items.length === 0 && <p className="empty">暂无资料。</p>}
          <ul className="record-list">
            {items.map(item => <li key={item.id}>
              <button className="record" aria-pressed={selected?.document === item.id} onClick={() => choose(item)}>
                <span className="record-title">{item.title}</span>
                <span className="meta">{sourceLabels[item.source_kind]} · {item.published_date} · {disclosures[item.disclosure_type]}</span>
              </button>
            </li>)}
          </ul>
        </section>
        <section className="panel">
          {file && selected && parsed ? <>
            <div className="panel-heading"><h2>{file.title}</h2></div>
            <div className="paper-meta">
              <p>{file.subject} · {sourceLabels[file.source_kind]} · {file.published_at ?? file.published_date}
                {file.time_precision === "DATE_ONLY" ? "（日期精度）" : "（UTC）"}</p>
              <p>采集时间 {new Date(file.ingested_at).toLocaleString("zh-CN")}</p>
              <p className="hash">SHA-256 {file.sha256}</p>
              <div className="actions">
                <a href={"/api/documents/" + encodeURIComponent(file.id) + "/file#page=" + selected.page} target="_blank" rel="noreferrer">查看原文件</a>
                {file.source_url && <a href={file.source_url} target="_blank" rel="noreferrer">来源公告</a>}
              </div>
            </div>
            <div className="page-bar">
              <button className="secondary" disabled={selected.page <= 1} onClick={() => setSelected({ document: file.id, version: parsed.version, page: selected.page - 1 })}>上一页</button>
              <span>第 {selected.page} / {parsed.page_count} 页</span>
              <button className="secondary" disabled={selected.page >= parsed.page_count} onClick={() => setSelected({ document: file.id, version: parsed.version, page: selected.page + 1 })}>下一页</button>
            </div>
            <p className="parse-version">解析版本 {parsed.version}</p>
            {error ? <p className="error" role="alert">{error}</p> : text ?
              <pre className="page-text">{highlight ? <>{chars.slice(0, selected.start).join("")}<mark>{chars.slice(selected.start, selected.end).join("")}</mark>{chars.slice(selected.end).join("")}</> : text.text}</pre>
              : <p className="empty">正在读取该页…</p>}
          </> : <p className="empty">选择一份披露资料查看原文。</p>}
        </section>
      </div>
    </>
  );
}
