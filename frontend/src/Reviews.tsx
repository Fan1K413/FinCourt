import { useEffect, useRef, useState } from "react";

import { decisions, formatDecimal, metrics, request, sourceLabels, units, type Decision, type Source } from "./api";
import type { Location } from "./Library";

const roles: Record<string, string> = { SUPPORT: "支持", REFUTE: "反证", CONTEXT: "背景", BOTH: "支持与反证" };
const operations: Record<string, string> = {
  eq: "=", gt: ">", ge: "≥", lt: "<", le: "≤", mul: "×", div: "÷",
  add: "+", sub: "−", and: "且", or: "或", not: "取反",
};

interface Check {
  result: boolean;
  rule: string;
  condition?: string;
  claim_id?: string;
  original?: { value: string; unit: string; currency: string; audit_status: string };
  steps: {
    op: string;
    inputs: (Quantity | boolean)[];
    result: Quantity | boolean;
  }[];
}
interface Quantity { value: string; dimension: string; currency: string | null }
interface Citation { fact_id: string; title: string; document_id: string; parse_version: string; page: number; start: number; end: number; text: string; role: string }
interface Review {
  id: string;
  mode: "fixture" | "live";
  status: string;
  purpose: string;
  created_at: string;
  snapshot_id: string;
  version_status: "CURRENT" | "SUPERSEDED" | null;
  tool_calls?: { id: string; tool: string; called_at: string; outcome: { fact_ids: string[] } }[];
  error?: { code: string; message: string };
  manifest: { cutoff: { date?: string; at?: string }; knowledge_release: { id: string };
              documents: { id: string; post_collected: boolean }[] };
  result?: {
    decision: Decision;
    claim_text: string;
    claim_version: number;
    checks: Check[];
    evidence: Citation[];
    missing: string[];
    execution_config: string;
    change: { previous_run_id: string; previous_decision: Decision; added_facts: string[]; removed_facts: string[] } | null;
  };
}
interface Claim {
  id: string;
  subject: string;
  source_kind: Source;
  timing: "FIXED" | "ROLLING";
  definition: { text: string; fixed_cutoff?: { date?: string; at?: string } };
}
interface ClaimDetail extends Claim { reviews: Review[] }

function shown(value: Quantity | boolean): string {
  return typeof value === "boolean" ? (value ? "成立" : "不成立")
    : formatDecimal(value.value) + (value.currency ? " " + (value.currency === "CNY" ? "元" : value.currency) : "");
}

function cutoff(item: Review) {
  return item.manifest.cutoff.date ?? item.manifest.cutoff.at ?? "—";
}

function Result({ run, open, pick }: { run: Review; open: (place: Location) => void; pick: (id: string) => void }) {
  if (!run.result) {
    return run.error
      ? <p className="error" role="alert">{run.error.message}</p>
      : <p className="sheet-empty" role="status">{run.status === "CANCELLED" ? "已取消" : "审查中…"}</p>;
  }
  const result = run.result;
  const change = result.change;
  return <div className="result">
    {change && change.previous_decision !== result.decision && <section className="result-section">
      <h3>前后变化</h3>
      <div className="outcome-comparison">
        <button className="link-button" onClick={() => pick(change.previous_run_id)}>{decisions[change.previous_decision]}</button>
        <span className="muted" aria-hidden="true">→</span>
        <span className="decision" data-decision={result.decision}>{decisions[result.decision]}</span>
      </div>
    </section>}
    {result.checks.length > 0 && <div className="result-section">
      <h3>计算</h3>
      <ul className="details-list calculations">{result.checks.map((check, i) => <li key={i}>
        {check.original && <p className="meta">原披露 {formatDecimal(check.original.value)} {units[check.original.unit] ?? check.original.unit} · {check.original.audit_status === "AUDITED" ? "已审计" : check.original.audit_status === "UNAUDITED" ? "未经审计" : "审计状态未知"}</p>}
        {check.steps.map((step, j) => <div className="equation" key={j}>
          <span className="formula">
            {step.inputs.length === 1 ? (operations[step.op] + " " + shown(step.inputs[0])) :
              step.inputs.map(shown).join(" " + (operations[step.op] ?? step.op) + " ")}
          </span>
          <strong>{shown(step.result)}</strong>
        </div>)}
        {check.claim_id && <p>{check.claim_id} · {check.result ? "前提成立" : "前提未成立"}</p>}
        {check.condition && <p className="meta">{check.condition}</p>}
      </li>)}</ul>
    </div>}
    {result.evidence.length > 0 && <div className="result-section">
      <h3>原文</h3>
      <ul className="details-list excerpts">{result.evidence.map(item => <li key={item.fact_id}>
        <blockquote>{item.text}</blockquote>
        <button className="citation" data-citation-id={item.fact_id} onClick={() => open({
          document: item.document_id, version: item.parse_version, page: item.page, start: item.start, end: item.end,
        })}>
          <span>{item.title}</span><span className="citation-page">{roles[item.role]} · 第 {item.page} 页 <span aria-hidden="true">↗</span></span>
        </button>
      </li>)}</ul>
    </div>}
    {result.missing.length > 0 && <div className="result-section">
      <h3>还缺什么</h3><ul>{result.missing.map(item => <li key={item}>{item}</li>)}</ul>
    </div>}
    <details className="result-section details"><summary>审查详情</summary>
      <p className="meta hash">运行 {run.id} · {run.mode} · {run.version_status === "CURRENT" ? "当前版本" : "历史版本"}</p>
      <p className="meta hash">快照 {run.snapshot_id} · {run.manifest.documents.length} 份资料</p>
      <p className="meta">{run.manifest.knowledge_release.id} · {result.execution_config} · 观点 v{result.claim_version}</p>
      {run.manifest.documents.some(doc => doc.post_collected) && <p className="meta">含事后补采资料</p>}
      {change && (change.added_facts.length > 0 || change.removed_facts.length > 0) &&
        <p className="meta hash">新证据 {change.added_facts.join("、") || "无"} · 移出 {change.removed_facts.join("、") || "无"}</p>}
      {run.tool_calls?.length ? <ul>{run.tool_calls.map(call => <li key={call.id}>
        {call.tool} · {new Date(call.called_at).toLocaleString("zh-CN")} · {call.outcome.fact_ids.length} 项证据
      </li>)}</ul> : null}
    </details>
  </div>;
}

export function Reviews({ mode, revision, target, openCitation }: {
  mode: string; revision: number; target: { claim: string; run: string } | null;
  openCitation: (place: Location) => void;
}) {
  const [claims, setClaims] = useState<Claim[]>([]);
  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState<ClaimDetail | null>(null);
  const [filter, setFilter] = useState<Source | "ALL">("ALL");
  const [runId, setRunId] = useState("");
  const [storedRun, setStoredRun] = useState<Review | null>(null);
  const [date, setDate] = useState("2025-04-13");
  const [create, setCreate] = useState(false);
  const [timing, setTiming] = useState("ROLLING");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const claimList = useRef<HTMLUListElement>(null);
  const active = detail?.id === selected ? detail : null;
  const history = active?.reviews ?? [];
  const compactRun = history.find(item => item.id === runId) ?? history[0];
  const savedId = compactRun?.id;
  const run = storedRun?.id === savedId ? storedRun : compactRun;
  const shownClaims = claims.filter(item => filter === "ALL" || item.source_kind === filter);

  useEffect(() => {
    const list = claimList.current;
    const item = list?.querySelector('[aria-pressed="true"]');
    if (!list || !item || list.scrollWidth <= list.clientWidth) return;
    const viewport = list.getBoundingClientRect();
    const itemBounds = item.getBoundingClientRect();
    const offset = itemBounds.left < viewport.left
      ? itemBounds.left - viewport.left : Math.max(0, itemBounds.right - viewport.right);
    if (offset) list.scrollLeft += offset;
  }, [selected, filter]);

  useEffect(() => {
    let alive = true;
    request<Claim[]>("/api/claims").then(items => {
      if (!alive) return;
      setClaims(items);
      setSelected(current => current || items.find(item => item.id === "sample-c5")?.id || items[0]?.id || "");
    }, problem => { if (alive) setError(problem.message); });
    return () => { alive = false; };
  }, [revision]);

  useEffect(() => {
    if (!selected) return;
    let alive = true;
    request<ClaimDetail>("/api/claims/" + selected).then(
      item => { if (alive) setDetail(item); },
      problem => { if (alive) setError(problem.message); },
    );
    return () => { alive = false; };
  }, [selected, revision]);

  useEffect(() => {
    if (target) { setSelected(target.claim); setRunId(target.run); }
  }, [target]);

  useEffect(() => {
    if (!savedId) return;
    let alive = true;
    request<Review>("/api/reviews/" + savedId).then(
      item => { if (alive) setStoredRun(item); },
      problem => { if (alive) setError(problem.message); },
    );
    return () => { alive = false; };
  }, [savedId, revision]);

  async function execute(purpose = "REVIEW") {
    if (!active) return;
    setBusy(true); setError("");
    try {
      const next = await request<Review>("/api/reviews", {
        claim_id: active.id, execution_mode: mode === "live" ? "live" : "fixture", purpose,
        ...(active.timing === "ROLLING" ? { cutoff: { date } } : {}),
      });
      setRunId(next.id);
      const updated = await request<ClaimDetail>("/api/claims/" + active.id);
      setDetail(updated);
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function addClaim(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true); setError("");
    try {
      const added = await request<Claim>("/api/claims", {
        text: form.get("text"), subject: form.get("subject"), source_kind: form.get("source"),
        timing, kind: "AS_OF_METRIC",
        ...(timing === "FIXED" ? { fixed_cutoff: { date: form.get("fixed") } } : {}),
        check: {
          metric: form.get("metric"), scope: form.get("scope"), operator: form.get("operator"),
          period: { start: form.get("start"), end: form.get("end") },
          threshold: { value: form.get("value"), unit: form.get("unit"), currency: "CNY" },
        },
      });
      setClaims([...claims, added]); setFilter("ALL"); setSelected(added.id); setRunId(""); setCreate(false);
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return <>
    <div className="section-title">
      <h1>观点审查</h1>
      <button className={create ? "secondary" : "section-action"} aria-expanded={create} onClick={() => setCreate(!create)}>
        {!create && <span aria-hidden="true">＋</span>} {create ? "取消" : "新建观点"}
      </button>
    </div>
    {create && <form className="sheet entry-form" onSubmit={addClaim}>
      <label>观点<textarea name="text" rows={2} minLength={3} maxLength={1000} required /></label>
      <div className="form-grid">
        <label>公司代码<input name="subject" placeholder="688287" required /></label>
        <label>资料来源<select name="source"><option value="REAL">真实公告</option><option value="SIMULATED">模拟资料</option></select></label>
        <label>指标<select name="metric">{Object.entries(metrics).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <label>合并范围<select name="scope"><option value="CONSOLIDATED">合并</option><option value="PARENT">母公司</option></select></label>
        <label>起始日期<input type="date" name="start" required /></label><label>结束日期<input type="date" name="end" required /></label>
        <label>比较<select name="operator">{["gt", "ge", "eq", "lt", "le"].map(op => <option key={op} value={op}>{operations[op]}</option>)}</select></label>
        <label>数值<input name="value" inputMode="decimal" required /></label>
        <label>单位<select name="unit">{Object.entries(units).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <label>日期方式<select value={timing} onChange={event => setTiming(event.target.value)}><option value="ROLLING">滚动</option><option value="FIXED">固定</option></select></label>
        {timing === "FIXED" && <label>截止日期<input type="date" name="fixed" required /></label>}
      </div>
      <div className="actions"><button type="submit" disabled={busy}>保存观点</button></div>
    </form>}
    {error && <p className="error" role="alert">{error}</p>}
    <div className="split">
      <section className="rail">
        <div className="rail-heading"><h2>观点列表</h2>
          <select aria-label="观点来源" value={filter} onChange={event => setFilter(event.target.value as typeof filter)}>
            <option value="ALL">全部来源</option><option value="REAL">真实公告</option><option value="SIMULATED">模拟资料</option>
          </select>
        </div>
        {!shownClaims.length && <p className="empty">暂无观点</p>}
        <ul className="record-list" ref={claimList}>
          {shownClaims.map(item => (
            <li key={item.id}><button className="record" disabled={busy} aria-pressed={item.id === selected} onClick={() => { setSelected(item.id); setRunId(""); }}>
              <span className="record-title">{item.definition.text}</span>
              <span className="meta">{sourceLabels[item.source_kind]} · {item.timing === "FIXED" ? "固定" : "滚动"}</span>
            </button></li>
          ))}
        </ul>
      </section>
      <section className="sheet review-sheet" aria-busy={busy}>
        {active ? <div key={active.id} className="sheet-content">
          <header className="sheet-heading">
            <p className="sheet-meta">{sourceLabels[active.source_kind]} {active.source_kind === "REAL" && <span>{active.subject}</span>}</p>
            <h2>{run?.result?.claim_text ?? active.definition.text}</h2>
            {run?.result && <div key={run.id} className="verdict">
              <strong className="decision" data-decision={run.result.decision}>{decisions[run.result.decision]}</strong>
              <time>{cutoff(run)}</time>
            </div>}
          </header>
          <div className="review-controls">
            {active.timing === "ROLLING"
              ? <label>截止日期<input type="date" value={date} onChange={event => setDate(event.target.value)} /></label>
              : <p className="meta">固定日期 {active.definition.fixed_cutoff?.date ?? active.definition.fixed_cutoff?.at}</p>}
            <button className="secondary" onClick={() => execute()} disabled={busy || !mode || (active.timing === "ROLLING" && !date)}>{busy ? "审查中…" : "运行审查"}</button>
            <button className="quiet" onClick={() => execute("PUBLIC_REPLAY")}
              disabled={busy || !mode || (active.timing === "ROLLING" && !date)}>按历史重跑</button>
          </div>
          {run ? <Result key={run.id} run={run} open={openCitation} pick={setRunId} /> : <p className="sheet-empty">暂无审查结果</p>}
          {history.length > 0 && <div className="history">
            <h3>历史记录</h3>
            <ul className="timeline">{history.map(item => <li key={item.id}>
              <button className="timeline-item" data-review-id={item.id} aria-pressed={run?.id === item.id} onClick={() => setRunId(item.id)}>
                <span>{cutoff(item)} <span className="meta">{item.purpose === "PUBLIC_REPLAY" ? "历史重跑" : item.purpose === "CHANGE_REVIEW" ? "更正重审" : ""}</span></span>
                <span className="decision" data-decision={item.result?.decision}>{item.result ? decisions[item.result.decision] : item.error ? "运行失败" : item.status === "CANCELLED" ? "已取消" : "审查中"}</span>
              </button>
            </li>)}</ul>
          </div>}
        </div> : <p className="sheet-empty">{selected ? "读取中…" : "暂无审查结果"}</p>}
      </section>
    </div>
  </>;
}
