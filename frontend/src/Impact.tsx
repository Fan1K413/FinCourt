import { useCallback, useEffect, useState } from "react";
import { decisions, formatDecimal, metrics, request, sourceLabels, units, type Decision, type Disclosure } from "./api";

interface EventSummary { id: string; title: string; document_id: string; created_at: string }
interface Fact { id: string; metric: string; value: string; unit: string; period_end: string }
interface ImpactEvent {
  id: string;
  document: Disclosure;
  changes: { before: Fact | null; after: Fact }[];
  fixed_claims_preserved: string[];
  requires_confirmation: boolean;
  claims: {
    claim_id: string;
    status: string;
    reasons: string[];
    review: { id: string; result: { claim_text: string; decision: Decision; change?: { previous_decision: Decision } } | null } | null;
  }[];
}

const reasons: Record<string, string> = {
  CORRECTED_FACT_DEPENDENCY: "涉及更正数据",
  CORRECTED_REVIEW_EVIDENCE: "曾引用原披露",
  MATCHING_SUBJECT_METRIC_PERIOD: "同一公司、指标与报告期",
  TRANSITIVE_CLAIM_DEPENDENCY: "关联观点",
};

export function Impact({ mode, revision, onImport, onReview }: {
  mode: string; revision: number; onImport: () => void; onReview: (claim: string, run: string) => void;
}) {
  const [events, setEvents] = useState<EventSummary[]>([]);
  const [selected, setSelected] = useState("");
  const [event, setEvent] = useState<ImpactEvent | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const items = await request<EventSummary[]>("/api/change-events");
    setEvents(items);
    setSelected(current => current || items[0]?.id || "");
  }, []);

  useEffect(() => {
    refresh().catch(problem => setError(problem.message));
  }, [refresh, revision]);

  useEffect(() => {
    if (!selected) return;
    let fresh = true;
    request<ImpactEvent>("/api/change-events/" + selected + "/impact").then(
      item => { if (fresh) setEvent(item); },
      problem => { if (fresh) setError(problem.message); },
    );
    return () => { fresh = false; };
  }, [selected]);

  async function simulate() {
    setBusy(true); setError("");
    try {
      const item = await request<ImpactEvent>("/api/demo/correction", {});
      setSelected(item.id);
      setEvent(item);
      await refresh();
      onImport();
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const current = event?.id === selected ? event : null;
  return <>
    <div className="section-title">
      <h1>变化影响</h1>
      {mode === "fixture" && <button className="section-action" onClick={simulate} disabled={busy}>
        <span aria-hidden="true">＋</span> {busy ? "重审中…" : "导入模拟更正"}
      </button>}
    </div>
    {error && <p className="error" role="alert">{error}</p>}
    <div className="split">
      <section className="rail">
        <div className="rail-heading"><h2>公告</h2></div>
        <ul className="record-list">{events.map(item => <li key={item.id}>
          <button className="record" aria-pressed={item.id === selected} onClick={() => setSelected(item.id)}>
            <span className="record-title">{item.title}</span>
          </button>
        </li>)}</ul>
      </section>
      <section className="sheet" aria-busy={busy}>
        {current ? <div key={current.id} className="sheet-content">
          <header className="sheet-heading">
            <p className="sheet-meta">{sourceLabels[current.document.source_kind]} <time>{current.document.published_date}</time></p>
            <h2>{current.document.title}</h2>
          </header>
          {current.requires_confirmation && <p className="error" role="status">部分更正待确认</p>}
          <div className="result-section">
            <h3>更正前后</h3>
            {current.changes.length ? <ul className="details-list">{current.changes.map(({ before, after }) => <li key={after.id + (before?.id ?? "")}>
              <div className="comparison-heading"><strong>{metrics[after.metric] ?? after.metric}</strong> <time className="meta">{after.period_end}</time></div>
              <div className="amount-pair">
                <dl className="amount-slip"><dt>原披露</dt><dd>{before ? formatDecimal(before.value) + " " + (units[before.unit] ?? before.unit) : "—"}</dd></dl>
                <span className="change-arrow" aria-hidden="true">→</span>
                <dl className="amount-slip changed"><dt>{before ? "更正后" : "新增"}</dt><dd>{formatDecimal(after.value)} {units[after.unit] ?? after.unit}</dd></dl>
              </div>
            </li>)}</ul> : <p className="meta">没有可比变更</p>}
          </div>
          <div className="result-section">
            <h3>受影响观点</h3>
            {current.claims.length ? <ul className="details-list affected-claims">{current.claims.map(item => {
              const review = item.review;
              const outcome = review?.result;
              const before = outcome?.change?.previous_decision;
              return <li key={item.claim_id}>
                {review ? <button className="link-button" data-impact-claim={item.claim_id} onClick={() => onReview(item.claim_id, review.id)}>
                  {outcome?.claim_text ?? item.claim_id} <span aria-hidden="true">↗</span>
                </button> : <strong>{item.claim_id}</strong>}
                <div className="outcome-comparison">
                  {before && <><span className="decision" data-decision={before}>{decisions[before]}</span><span className="muted" aria-hidden="true">→</span></>}
                  <span className="decision" data-decision={outcome?.decision}>{outcome ? decisions[outcome.decision] : item.status === "FAILED" ? "运行失败" : "待重审"}</span>
                </div>
                <p className="meta">{item.reasons.map(reason => reasons[reason]).filter(Boolean).join(" · ")}</p>
              </li>;
            })}</ul> : <p className="meta">无受影响观点</p>}
            {current.fixed_claims_preserved.length > 0 && <p className="meta">{current.fixed_claims_preserved.length} 条固定观点保留原日期</p>}
          </div>
        </div> : <p className="sheet-empty">{selected ? "读取中…" : "暂无变化"}</p>}
      </section>
    </div>
  </>;
}
