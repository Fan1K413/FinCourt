import { useEffect, useState } from "react";

import { metrics, request, sourceLabels, type Source } from "./api";
import type { Location } from "./Library";

type Decision = "SUPPORTED" | "REFUTED" | "DISPUTED" | "INSUFFICIENT";
const decisions: Record<Decision, string> = {
  SUPPORTED: "有证据支持", REFUTED: "被适用证据反驳",
  DISPUTED: "存在争议", INSUFFICIENT: "证据不足",
};
const roles: Record<string, string> = { SUPPORT: "支持", REFUTE: "反证", CONTEXT: "背景", BOTH: "支持与反证" };
const operations: Record<string, string> = {
  eq: "=", gt: ">", ge: "≥", lt: "<", le: "≤", mul: "×", div: "÷",
  add: "+", sub: "−", and: "且", or: "或", not: "取反",
};
const units: Record<string, string> = { wan_yuan: "万元", yi_yuan: "亿元", yuan: "元" };

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
    : value.value + (value.currency ? " " + value.currency : "");
}

function cutoff(item: Review) {
  return item.manifest.cutoff.date ?? item.manifest.cutoff.at ?? "—";
}

function Result({ run, open, pick }: { run: Review; open: (place: Location) => void; pick: (id: string) => void }) {
  if (!run.result) return <p className="error" role="alert">{run.error?.message ?? run.status} · {run.id}</p>;
  const result = run.result;
  return <div className="result">
    <div className={"verdict " + result.decision.toLowerCase()}>
      <div><strong>{decisions[result.decision]}</strong><p>{result.claim_text}</p></div>
      <span>{cutoff(run)} · {run.mode}</span>
    </div>
    {result.change && <div className="result-section">
      <h3>与上次审查比较</h3>
      <p><button className="link-button" onClick={() => pick(result.change!.previous_run_id)}>{decisions[result.change.previous_decision]}</button> → {decisions[result.decision]}</p>
      {(result.change.added_facts.length > 0 || result.change.removed_facts.length > 0) &&
        <p className="meta">新增 {result.change.added_facts.join("、") || "无"} · 移出 {result.change.removed_facts.join("、") || "无"}</p>}
    </div>}
    {result.checks.length > 0 && <div className="result-section">
      <h3>程序校验</h3>
      <ul className="details-list">{result.checks.map((check, i) => <li key={i}>
        {check.original && <p className="meta">原披露 {check.original.value} {units[check.original.unit] ?? check.original.unit} · {check.original.audit_status === "AUDITED" ? "已审计" : check.original.audit_status === "UNAUDITED" ? "未经审计" : "审计状态未确认"}</p>}
        {check.steps.map((step, j) => <p className="formula" key={j}>
          {step.inputs.length === 1 ? (operations[step.op] + " " + shown(step.inputs[0])) :
            step.inputs.map(shown).join(" " + (operations[step.op] ?? step.op) + " ")}
          {" → " + shown(step.result)}
        </p>)}
        {check.claim_id && <p>{check.claim_id} · {check.result ? "前提成立" : "前提未成立"}</p>}
        {check.condition && <p className="meta">{check.condition}</p>}
      </li>)}</ul>
    </div>}
    <div className="result-section">
      <h3>可定位证据</h3>
      {result.evidence.length ? <ul className="details-list">{result.evidence.map(item => <li key={item.fact_id}>
        <button className="link-button" onClick={() => open({
          document: item.document_id, version: item.parse_version, page: item.page, start: item.start, end: item.end,
        })}>{roles[item.role]} · {item.title} · 第 {item.page} 页</button>
        <blockquote>{item.text}</blockquote>
      </li>)}</ul> : <p className="meta">本次运行没有可引用证据。</p>}
    </div>
    {result.missing.length > 0 && <div className="result-section">
      <h3>待补充资料</h3><ul>{result.missing.map(item => <li key={item}>{item}</li>)}</ul>
    </div>}
    <details className="result-section"><summary>快照与版本</summary>
      <p className="meta hash">运行 {run.id} · {run.version_status === "CURRENT" ? "当前版本" : "被同上下文结果替代"}</p>
      <p className="meta hash">快照 {run.snapshot_id} · {run.manifest.documents.length} 份资料</p>
      <p className="meta">{run.manifest.knowledge_release.id} · {result.execution_config} · 观点 v{result.claim_version}</p>
      {run.manifest.documents.some(doc => doc.post_collected) && <p className="meta">资料包含事后补采的公开文件。</p>}
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
  const active = detail?.id === selected ? detail : null;
  const history = active?.reviews ?? [];
  const compactRun = history.find(item => item.id === runId) ?? history[0];
  const savedId = compactRun?.id;
  const run = storedRun?.id === savedId ? storedRun : compactRun;

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
      <div><h1>观点审查</h1><p>核对披露时点、口径和计算依据。</p></div>
      <button className="secondary" aria-expanded={create} onClick={() => setCreate(!create)}>{create ? "收起表单" : "新建观点"}</button>
    </div>
    {create && <form className="panel upload" onSubmit={addClaim}>
      <label>观点表述<input name="text" minLength={3} maxLength={1000} required /></label>
      <div className="form-grid">
        <label>主体代码<input name="subject" placeholder="688287" required /></label>
        <label>资料来源<select name="source"><option value="REAL">真实公告</option><option value="SIMULATED">模拟资料</option></select></label>
        <label>指标<select name="metric">{Object.entries(metrics).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <label>合并范围<select name="scope"><option value="CONSOLIDATED">合并</option><option value="PARENT">母公司</option></select></label>
        <label>报告期起<input type="date" name="start" required /></label><label>报告期止<input type="date" name="end" required /></label>
        <label>比较符<select name="operator">{["gt", "ge", "eq", "lt", "le"].map(op => <option key={op} value={op}>{operations[op]}</option>)}</select></label>
        <label>阈值<input name="value" inputMode="decimal" required /></label>
        <label>单位<select name="unit">{Object.entries(units).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <label>时点模式<select value={timing} onChange={event => setTiming(event.target.value)}><option value="ROLLING">滚动</option><option value="FIXED">固定</option></select></label>
        {timing === "FIXED" && <label>截止日期<input type="date" name="fixed" required /></label>}
      </div>
      <div className="actions"><button type="submit" disabled={busy}>创建观点</button></div>
    </form>}
    {error && <p className="error" role="alert">{error}</p>}
    <div className="split">
      <section className="panel">
        <div className="panel-heading"><h2>研究观点</h2>
          <select aria-label="观点来源" value={filter} onChange={event => setFilter(event.target.value as typeof filter)}>
            <option value="ALL">全部来源</option><option value="REAL">真实公告</option><option value="SIMULATED">模拟资料</option>
          </select>
        </div>
        {!claims.length && <p className="empty">暂无演示观点。</p>}
        <ul className="record-list">
          {claims.filter(item => filter === "ALL" || item.source_kind === filter).map(item => (
            <li key={item.id}><button className="record" disabled={busy} aria-pressed={item.id === selected} onClick={() => { setSelected(item.id); setRunId(""); }}>
              <span>{item.definition.text}</span><span className="meta">{sourceLabels[item.source_kind]} · {item.timing === "FIXED" ? "固定" : "滚动"}</span>
            </button></li>
          ))}
        </ul>
      </section>
      <section className="panel">
        {active ? <>
          <div className="panel-heading"><h2>{active.definition.text}</h2></div>
          <div className="review-controls">
            {active.timing === "ROLLING"
              ? <label>截止日期<input type="date" value={date} onChange={event => setDate(event.target.value)} /></label>
              : <p className="meta">固定截止 {active.definition.fixed_cutoff?.date ?? active.definition.fixed_cutoff?.at}</p>}
            <button onClick={() => execute()} disabled={busy || !mode || (active.timing === "ROLLING" && !date)}>{busy ? "处理中…" : "运行审查"}</button>
            <button className="secondary" onClick={() => execute("PUBLIC_REPLAY")}
              disabled={busy || !mode || (active.timing === "ROLLING" && !date)}>按历史资料重跑</button>
          </div>
          {run ? <Result run={run} open={openCitation} pick={setRunId} /> : <p className="empty">运行后可查看计算、证据和快照。</p>}
          {history.length > 0 && <div className="history">
            <div className="panel-heading"><h2>历史记录</h2></div>
            <ul className="record-list">{history.map(item => <li key={item.id}>
              <button className="record" aria-pressed={run?.id === item.id} onClick={() => setRunId(item.id)}>
                <span>{cutoff(item)} · {item.result ? decisions[item.result.decision] : "运行失败"}</span>
                <span className="meta">{item.mode} · {new Date(item.created_at).toLocaleString("zh-CN")} {item.purpose === "PUBLIC_REPLAY" ? "· 历史资料重跑" : item.purpose === "CHANGE_REVIEW" ? "· 更正重审" : ""}</span>
              </button>
            </li>)}</ul>
          </div>}
        </> : <p className="empty">选择一个观点以查看审查。</p>}
      </section>
    </div>
  </>;
}
