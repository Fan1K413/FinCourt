import { useCallback, useEffect, useState } from "react";
import { metrics, request, sourceLabels, type Disclosure } from "./api";

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
    review: { id: string; result: { claim_text: string; decision: string; change?: { previous_decision: string } } | null } | null;
  }[];
}

const unitLabels: Record<string, string> = { yuan: "元", wan_yuan: "万元", yi_yuan: "亿元" };
const reasons: Record<string, string> = {
  CORRECTED_FACT_DEPENDENCY: "依赖被更正的事实",
  CORRECTED_REVIEW_EVIDENCE: "历史审查曾引用被更正的事实",
  MATCHING_SUBJECT_METRIC_PERIOD: "主体、指标和报告期匹配",
  TRANSITIVE_CLAIM_DEPENDENCY: "依赖另一个受影响的观点",
};
const decisions: Record<string, string> = {
  SUPPORTED: "有证据支持", REFUTED: "被适用证据反驳", DISPUTED: "存在争议", INSUFFICIENT: "证据不足",
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
      <div><h1>变化影响</h1><p>查看披露变更的字段和自动重审结果。</p></div>
      {mode === "fixture" && <button onClick={simulate} disabled={busy}>{busy ? "正在重审…" : "导入模拟更正"}</button>}
    </div>
    {error && <p className="error" role="alert">{error}</p>}
    <div className="split">
      <section className="panel">
        <div className="panel-heading"><h2>公告事件</h2></div>
        {!events.length && <p className="empty">暂无已登记事件。</p>}
        <ul className="record-list">{events.map(item => <li key={item.id}>
          <button className="record" aria-pressed={item.id === selected} onClick={() => setSelected(item.id)}>
            {item.title}
          </button>
        </li>)}</ul>
      </section>
      <section className="panel">
        {current ? <>
          <div className="panel-heading"><h2>{current.document.title}</h2></div>
          <p className="empty">{sourceLabels[current.document.source_kind]} · {current.document.published_date}</p>
          {current.requires_confirmation && <p className="error">更正范围待确认，未匹配的字段暂不生效。</p>}
          <div className="result-section">
            <h3>披露差异</h3>
            {current.changes.length ? <ul className="details-list">{current.changes.map(({ before, after }) => <li key={after.id + (before?.id ?? "")}>
              <strong>{metrics[after.metric] ?? after.metric}</strong> <span className="meta">{after.period_end}</span>
              <p>{before ? before.value + " " + (unitLabels[before.unit] ?? before.unit) : "新增"} → {after.value} {unitLabels[after.unit] ?? after.unit}</p>
            </li>)}</ul> : <p className="meta">本次公告没有可核验的字段变更。</p>}
          </div>
          <div className="result-section">
            <h3>受影响观点</h3>
            {current.claims.length ? <ul className="details-list">{current.claims.map(item => <li key={item.claim_id}>
              {item.review ? <button className="link-button" onClick={() => onReview(item.claim_id, item.review!.id)}>
                {item.review.result?.claim_text ?? item.claim_id}
              </button> : <strong>{item.claim_id}</strong>}
              <p>{item.review?.result ? (item.review.result.change?.previous_decision ?
                decisions[item.review.result.change.previous_decision] + " → " : "") +
                decisions[item.review.result.decision] : item.status === "FAILED" ? "运行失败" : "待重审"}</p>
              <span className="meta">{item.reasons.map(reason => reasons[reason] ?? reason).join(" · ")}</span>
            </li>)}</ul> : <p className="meta">本次变更没有触发滚动观点重审。</p>}
            {current.fixed_claims_preserved.length > 0 && <p className="meta">有 {current.fixed_claims_preserved.length} 条相关固定观点保留原截止时点。</p>}
          </div>
        </> : <p className="empty">选择一条事件查看影响。</p>}
      </section>
    </div>
  </>;
}
