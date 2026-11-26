import { useCallback, useEffect, useState } from "react";

import { request } from "./api";
import type { Location } from "./Library";

interface Span { document_id: string; parse_version: string; page: number; start: number; end: number; text: string }
interface Agreement {
  amount: { value: string; currency: string };
  recognized_revenue: string | null;
  citations: Span[];
}
interface Patch {
  id: string;
  status: string;
  base_release: string;
  published_release: string | null;
  payload: { gap: string; scope: string; migration: string; operations: { id: string; definition: string }[] };
  validation: {
    passed: boolean; data_revision: number; before: { concept_present: boolean };
    after: Agreement | null; checks: { id: string; passed: boolean }[];
    new_case_error: { message: string } | null; unmapped: string[];
  } | null;
}
interface KnowledgeView {
  active_release: string;
  data_revision: number;
  releases: { id: string; manifest: { ontology: string } }[];
  patches: Patch[];
  projections: { document_id: string; value: Agreement }[];
  pending_documents: string[];
  history: { id: string; action: string; release_id: string; previous_release: string; applied_at: string }[];
}
const labels: Record<string, string> = {
  DRAFT: "草稿", READY_FOR_REVIEW: "待审批", VALIDATION_FAILED: "验证失败",
  APPROVED: "已批准", REJECTED: "已拒绝", PUBLISHED: "已发布",
};

function AgreementRecord({ record, open }: { record: Agreement; open: (target: Location) => void }) {
  return <>
    <p>备忘录最高金额 {record.amount.value} {record.amount.currency} · 尚未生效</p>
    <p>先决条件包括签字盖章、签署正式合同。公告披露净额法口径，已确认收入金额暂无依据。</p>
    <ul className="details-list">{record.citations.map(item => <li key={item.page + ":" + item.start}>
      <button className="link-button" onClick={() => open({ document: item.document_id, version: item.parse_version,
        page: item.page, start: item.start, end: item.end })}>第 {item.page} 页</button>
      <blockquote>{item.text}</blockquote>
    </li>)}</ul>
  </>;
}

export function Knowledge({ visible, openCitation }: { visible: boolean; openCitation: (target: Location) => void }) {
  const [view, setView] = useState<KnowledgeView | null>(null);
  const [selection, setSelection] = useState("");
  const [token, setToken] = useState("");
  const [rollback, setRollback] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const response = await request<KnowledgeView>("/api/knowledge-releases");
    setView(response);
    setSelection(current => current || response.patches[0]?.id || "");
    setRollback(current => response.releases.some(item => item.id === current && item.id !== response.active_release)
      ? current : "");
  }, []);
  useEffect(() => {
    if (visible) refresh().catch(problem => setError(problem.message));
  }, [visible, refresh]);

  async function apply(path: string, authorized: boolean) {
    setBusy(true); setError("");
    try {
      if (authorized) {
        const response = await fetch(path, { method: "POST", headers: { Authorization: "Bearer " + token } });
        if (!response.ok) throw new Error((await response.json()).detail?.message ?? "操作失败");
      } else {
        await request(path, {});
      }
      await refresh();
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const item = view?.patches.find(patch => patch.id === selection);
  const patchPath = item && "/api/ontology-patches/" + item.id;
  return <>
    <div className="section-title"><div><h1>知识版本</h1><p>查看结构补丁、验证、发布和回滚记录。</p></div>
      <button className="secondary" onClick={() => refresh().catch(problem => setError(problem.message))}>刷新</button>
    </div>
    {error && <p className="error" role="alert">{error}</p>}
    <div className="split">
      <section className="panel">
        <div className="panel-heading"><h2>候选补丁</h2></div>
        {!view?.patches.length && <p className="empty">导入协议案例后可验证候选补丁。</p>}
        <ul className="record-list">{view?.patches.map(item => <li key={item.id}>
          <button className="record" aria-pressed={item.id === selection} onClick={() => setSelection(item.id)}>
            {item.payload.operations[0].definition}<span className="meta">{labels[item.status] ?? item.status}</span>
          </button>
        </li>)}</ul>
      </section>
      <section className="panel">
        <div className="panel-heading"><h2>当前版本</h2><span className="meta">资料修订 {view?.data_revision ?? "—"}</span></div>
        <p className="empty hash">{view?.active_release ?? "正在读取…"}</p>
        {view?.pending_documents.length ? <p className="empty">待处理协议 {view.pending_documents.length} 份</p> : null}
        {view?.projections.map(record => <div className="result-section" key={record.document_id}>
          <h3>当前协议投影</h3><AgreementRecord record={record.value} open={openCitation} />
        </div>)}
        {item && patchPath && <div className="result-section">
          <h3>{labels[item.status] ?? item.status}</h3>
          <p>{item.payload.gap}</p><p>{item.payload.migration}</p>
          {item.validation && <>
            <p>{item.validation.before.concept_present ? "基准版本已有此结构" : "基准版本缺少协议阶段和条件字段"}
              {" → "}{item.validation.after ? "候选版本可保存条件协议" : "候选投影未通过"}</p>
            {item.validation.after && <AgreementRecord record={item.validation.after} open={openCitation} />}
            <p className="meta">资料修订 {item.validation.data_revision} · 未自动迁移 {item.validation.unmapped.length} 份</p>
            <ul>{item.validation.checks.map(check => <li key={check.id}>{check.id} · {check.passed ? "通过" : "失败"}</li>)}</ul>
            {item.validation.new_case_error && <p className="error">{item.validation.new_case_error.message}</p>}
          </>}
          <div className="actions">
            {!["PUBLISHED", "REJECTED"].includes(item.status) &&
              <button className="secondary" disabled={busy} onClick={() => apply(patchPath + "/validate", false)}>验证补丁</button>}
            {item.status === "READY_FOR_REVIEW" &&
              <button disabled={busy || !token} onClick={() => apply(patchPath + "/approve", true)}>批准补丁</button>}
            {item.status === "APPROVED" &&
              <button disabled={busy || !token} onClick={() => apply(patchPath + "/publish", true)}>发布知识版本</button>}
          </div>
        </div>}
        <div className="result-section">
          <label>维护者口令<input type="password" autoComplete="off" value={token} onChange={event => setToken(event.target.value)} /></label>
          {view && view.releases.length > 1 && <div className="actions">
            <select aria-label="回滚版本" value={rollback} onChange={event => setRollback(event.target.value)}>
              <option value="">选择回滚版本</option>
              {view.releases.filter(item => item.id !== view.active_release).map(item => <option key={item.id} value={item.id}>{item.manifest.ontology}</option>)}
            </select>
            <button className="secondary" disabled={!token || busy || !rollback}
              onClick={() => apply("/api/knowledge-releases/" + rollback + "/rollback", true)}>回滚</button>
          </div>}
        </div>
        {view?.history.length ? <div className="result-section">
          <h3>发布记录</h3>
          <ul>{view.history.map(change => <li key={change.id}>
            {change.action === "PUBLISH" ? "发布" : "回滚"} · {change.previous_release.slice(0, 12)} → {change.release_id.slice(0, 12)} · {new Date(change.applied_at).toLocaleString("zh-CN")}
          </li>)}</ul>
        </div> : null}
      </section>
    </div>
  </>;
}
