import { useCallback, useEffect, useState } from "react";

import { formatDecimal, request } from "./api";
import type { Location } from "./Library";

interface Span { document_id: string; parse_version: string; page: number; start: number; end: number; text: string }
interface Agreement {
  amount: { value: string; currency: string };
  conditions: ("SIGN_AND_SEAL" | "FORMAL_CONTRACT")[];
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
    <div className="agreement-facts">
      <dl className="amount-slip"><dt>备忘录上限</dt><dd>{formatDecimal(record.amount.value)} {record.amount.currency === "CNY" ? "元" : record.amount.currency}</dd></dl>
      <dl className="amount-slip"><dt>协议状态</dt><dd>尚未生效</dd></dl>
      <dl className="amount-slip"><dt>生效条件</dt><dd>{record.conditions.map(condition => condition === "SIGN_AND_SEAL" ? "签字盖章" : "签署正式合同").join("、")}</dd></dl>
      <dl className="amount-slip"><dt>已确认收入</dt><dd>{record.recognized_revenue ?? "未知"} <span className="meta">净额法</span></dd></dl>
    </div>
    <ul className="details-list excerpts">{record.citations.map(item => <li key={item.page + ":" + item.start}>
      <blockquote>{item.text}</blockquote>
      <button className="citation" onClick={() => open({ document: item.document_id, version: item.parse_version,
        page: item.page, start: item.start, end: item.end })}>
        <span>公告原文</span><span className="citation-page">第 {item.page} 页 <span aria-hidden="true">↗</span></span>
      </button>
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
  const currentRelease = view?.releases.find(release => release.id === view.active_release);
  const canRollback = (view?.releases.length ?? 0) > 1;
  const needsToken = canRollback || item?.status === "READY_FOR_REVIEW" || item?.status === "APPROVED";
  return <>
    <div className="section-title"><h1>知识版本</h1>
      <button className="secondary" onClick={() => refresh().catch(problem => setError(problem.message))}>刷新</button>
    </div>
    {error && <p className="error" role="alert">{error}</p>}
    <div className="split">
      <section className="rail">
        <div className="rail-heading"><h2>补丁</h2></div>
        {view && !view.patches.length && <p className="empty">暂无补丁</p>}
        <ul className="record-list">{view?.patches.map(item => <li key={item.id}>
          <button className="record" aria-pressed={item.id === selection} onClick={() => setSelection(item.id)}>
            <span className="record-title">{item.payload.operations[0].id === "conditional_agreement" ? "附条件协议" : item.payload.operations[0].id}</span>
            <span className="meta">{labels[item.status] ?? item.status}</span>
          </button>
        </li>)}</ul>
      </section>
      <section className="sheet" aria-busy={busy}>
        <header className="sheet-heading">
          <p className="sheet-meta">当前版本 {view && <span>资料修订 {view.data_revision}</span>}</p>
          <h2 className="hash">{currentRelease?.manifest.ontology ?? "读取中…"}</h2>
          {view?.pending_documents.length ? <p className="meta" data-pending-documents={view.pending_documents.length}>待处理协议 {view.pending_documents.length} 份</p> : null}
        </header>
        {needsToken && <div className="result-section version-actions">
          <label>维护者口令<input type="password" autoComplete="off" value={token} onChange={event => setToken(event.target.value)} /></label>
          {canRollback && <div className="actions">
            <select aria-label="回滚版本" value={rollback} onChange={event => setRollback(event.target.value)}>
              <option value="">选择版本</option>
              {view?.releases.filter(item => item.id !== view.active_release).map(item => <option key={item.id} value={item.id}>{item.manifest.ontology}</option>)}
            </select>
            <button className="secondary" disabled={!token || busy || !rollback}
              onClick={() => apply("/api/knowledge-releases/" + rollback + "/rollback", true)}>回滚</button>
          </div>}
        </div>}
        {view?.projections.map(record => <div className="result-section" key={record.document_id} data-projection-document={record.document_id}>
          <h3>当前协议</h3><AgreementRecord record={record.value} open={openCitation} />
        </div>)}
        {item && patchPath && <div className="result-section" data-patch-status={item.status}>
          <div className="comparison-heading"><h3>补丁预览</h3><span className="decision">{labels[item.status] ?? item.status}</span></div>
          {item.validation && <>
            <div className="amount-pair">
              <dl className="amount-slip"><dt>原版本</dt><dd>{item.validation.before.concept_present ? "已有协议字段" : "无协议字段"}</dd></dl>
              <span className="change-arrow" aria-hidden="true">→</span>
              <dl className="amount-slip changed"><dt>候选版本</dt><dd>{item.validation.after ? "附条件协议" : "未通过"}</dd></dl>
            </div>
            {item.validation.after && item.published_release !== view?.active_release &&
              <AgreementRecord record={item.validation.after} open={openCitation} />}
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
          <details className="details inline-details"><summary>补丁详情</summary>
            <p>{item.payload.gap}</p><p>{item.payload.migration}</p>
            {item.validation && <>
              <p className="meta">资料修订 {item.validation.data_revision} · 未迁移 {item.validation.unmapped.length} 份</p>
              <ul>{item.validation.checks.map(check => <li key={check.id}>{check.id} · {check.passed ? "通过" : "失败"}</li>)}</ul>
            </>}
          </details>
        </div>}
        {view?.history.length ? <div className="result-section">
          <h3>发布记录</h3>
          <ul className="details-list publication-history">{view.history.map(change => <li key={change.id}>
            <strong>{change.action === "PUBLISH" ? "发布" : "回滚"}</strong>
            <time className="meta" dateTime={change.applied_at}>{new Date(change.applied_at).toLocaleString("zh-CN")}</time>
            <span className="meta">{change.previous_release.slice(0, 12)} → {change.release_id.slice(0, 12)}</span>
          </li>)}</ul>
        </div> : null}
      </section>
    </div>
  </>;
}
