import { useEffect, useRef, useState } from "react";

import { disclosures, request, sourceLabels, type Disclosure, type PageText, type Source } from "./api";

export interface Location {
  document: string;
  version: string;
  page: number;
  start?: number;
  end?: number;
}

export function Library({ documents, location, visible, onImport }: {
  documents: Disclosure[];
  location: Location | null;
  visible: boolean;
  onImport: () => void;
}) {
  const [source, setSource] = useState<Source | "ALL">("ALL");
  const [selected, setSelected] = useState<Location | null>(location);
  const [loadedPage, setText] = useState<PageText | null>(null);
  const [error, setError] = useState("");
  const [upload, setUpload] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadState, setUploadState] = useState("");
  const highlightRef = useRef<HTMLElement>(null);
  const lastCitation = useRef<Location | null>(null);
  const file = documents.find(item => item.id === selected?.document);
  const parsed = file?.parses.find(item => item.version === selected?.version);
  const items = documents.filter(item => source === "ALL" || item.source_kind === source);
  const first = items[0];
  const text = loadedPage && selected && loadedPage.document_id === selected.document
    && loadedPage.parse_version === selected.version && loadedPage.number === selected.page ? loadedPage : null;

  useEffect(() => {
    if (location && lastCitation.current !== location) {
      lastCitation.current = location;
      setSelected(location);
      setSource("ALL");
    } else if (visible && !selected && first) {
      choose(first);
    }
  }, [location, visible, selected, first]);

  useEffect(() => {
    if (!visible || !selected) return;
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
  }, [selected, visible]);

  useEffect(() => {
    const mark = highlightRef.current;
    if (!visible || !mark || !text) return;
    const frame = requestAnimationFrame(() => {
      const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      mark.scrollIntoView({ block: "nearest", behavior: reduced ? "auto" : "smooth" });
    });
    return () => cancelAnimationFrame(frame);
  }, [text, visible]);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (uploading) return;
    const form = new FormData(event.currentTarget);
    const body = new FormData();
    body.set("file", form.get("file")!);
    body.set("metadata", advanced ? String(form.get("metadata")) : JSON.stringify({
      id: form.get("id"), title: form.get("title"), subject: form.get("subject"),
      source_kind: form.get("source"), disclosure_type: form.get("type"),
      published_date: form.get("date"), source_url: form.get("url") || null,
    }));
    setUploading(true);
    setUploadState("正在导入…");
    try {
      const response = await fetch("/api/documents", { method: "POST", body });
      const item = await response.json();
      if (!response.ok) throw new Error(item.detail?.message ?? "导入失败");
      setUploadState("已导入 " + item.title);
      setSource("ALL");
      setSelected({ document: item.id, version: item.parses[0].version, page: 1 });
      onImport();
    } catch (problem) {
      setUploadState((problem as Error).message);
    } finally {
      setUploading(false);
    }
  }

  function choose(item: Disclosure) {
    setSelected({ document: item.id, version: item.parses.at(-1)!.version, page: 1 });
  }

  const highlight = selected?.start !== undefined && selected.end !== undefined;
  const chars = text && highlight ? Array.from(text.text) : [];

  return (
    <>
      <div className="section-title">
        <h1>资料库</h1>
        <button className={upload ? "secondary" : "section-action"} onClick={() => setUpload(!upload)} aria-expanded={upload}>
          {!upload && <span aria-hidden="true">＋</span>} {upload ? "取消" : "导入 PDF"}
        </button>
      </div>
      {upload && <form className="sheet entry-form" onSubmit={submit}>
        <div className="form-header">
          <label>PDF 文件<input type="file" name="file" accept="application/pdf,.pdf" required /></label>
          <button type="button" className="quiet" onClick={() => setAdvanced(value => !value)}>{advanced ? "切换到表单" : "使用 JSON"}</button>
        </div>
        <fieldset className="form-grid" hidden={advanced} disabled={advanced}>
          <label>公告名称<input name="title" maxLength={240} required /></label>
          <label>资料编号<input name="id" maxLength={96} pattern="[A-Za-z0-9][A-Za-z0-9_.\-]*" required /></label>
          <label>公司代码<input name="subject" maxLength={96} pattern="[A-Za-z0-9][A-Za-z0-9_.\-]*" required /></label>
          <label>披露日期<input name="date" type="date" required /></label>
          <label>资料来源<select name="source"><option value="REAL">真实公告</option><option value="SIMULATED">模拟资料</option></select></label>
          <label>公告类型<select name="type">{Object.entries(disclosures).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label>来源链接<input name="url" type="url" /></label>
        </fieldset>
        <fieldset hidden={!advanced} disabled={!advanced}>
          <label>资料 JSON<textarea name="metadata" rows={6} required
            placeholder={'{"id":"doc-1","title":"公告名称","subject":"688287","source_kind":"REAL","disclosure_type":"RESULTS","published_date":"2025-02-28"}'} /></label>
        </fieldset>
        <div className="actions"><button type="submit" disabled={uploading}>导入</button><span className="meta" role="status">{uploadState}</span></div>
      </form>}
      <div className="split">
        <section className="rail">
          <div className="rail-heading"><h2>目录</h2>
            <select aria-label="资料来源" value={source} onChange={e => {
              const next = e.target.value as typeof source;
              setSource(next);
              if (next !== "ALL" && file?.source_kind !== next) setSelected(null);
            }}>
              <option value="ALL">全部来源</option><option value="REAL">真实公告</option><option value="SIMULATED">模拟资料</option>
            </select>
          </div>
          {items.length === 0 && <p className="empty">暂无资料</p>}
          <ul className="record-list">
            {items.map(item => <li key={item.id}>
              <button className="record" aria-pressed={selected?.document === item.id} onClick={() => choose(item)}>
                <span className="record-title">{item.title}</span>
                <span className="meta">{sourceLabels[item.source_kind]} · {item.published_date} · {disclosures[item.disclosure_type]}</span>
              </button>
            </li>)}
          </ul>
        </section>
        <section className="sheet library-sheet">
          {file && selected && parsed ? <div key={file.id} className="sheet-content">
            <header className="sheet-heading">
              <p className="sheet-meta">{sourceLabels[file.source_kind]} {file.source_kind === "REAL" && <span>{file.subject}</span>} <time dateTime={file.published_date}>{file.published_date}</time></p>
              <h2>{file.title}</h2>
              <div className="actions">
                <a href={"/api/documents/" + encodeURIComponent(file.id) + "/file#page=" + selected.page} target="_blank" rel="noreferrer">原文件 <span aria-hidden="true">↗</span></a>
                {file.source_url && <a href={file.source_url} target="_blank" rel="noreferrer">来源 <span aria-hidden="true">↗</span></a>}
              </div>
            </header>
            <div className="page-bar">
              <button className="quiet" disabled={selected.page <= 1} onClick={() => setSelected({ document: file.id, version: parsed.version, page: selected.page - 1 })}><span aria-hidden="true">← </span>上一页</button>
              <span>第 {selected.page} / {parsed.page_count} 页</span>
              <button className="quiet" disabled={selected.page >= parsed.page_count} onClick={() => setSelected({ document: file.id, version: parsed.version, page: selected.page + 1 })}>下一页<span aria-hidden="true"> →</span></button>
            </div>
            {error ? <p className="error" role="alert">{error}</p> : text ?
              <pre key={text.number} className="page-text">{highlight ? <>{chars.slice(0, selected.start).join("")}<mark ref={highlightRef}>{chars.slice(selected.start, selected.end).join("")}</mark>{chars.slice(selected.end).join("")}</> : text.text}</pre>
              : <p className="sheet-empty" role="status">读取中…</p>}
            <details className="result-section details">
              <summary>资料详情</summary>
              <p className="meta">公司代码 {file.subject}</p>
              <p className="meta">{file.published_at ?? file.published_date} {file.time_precision === "DATE_ONLY" ? "（仅日期）" : "（UTC）"}</p>
              <p className="meta">采集时间 {new Date(file.ingested_at).toLocaleString("zh-CN")}</p>
              <p className="meta">解析版本 {parsed.version}</p>
              <p className="meta hash">SHA-256 {file.sha256}</p>
            </details>
          </div> : <p className="sheet-empty">暂无原文</p>}
        </section>
      </div>
    </>
  );
}
