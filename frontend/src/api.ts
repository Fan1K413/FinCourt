export async function request<T>(path: string, data?: unknown): Promise<T> {
  const result = await fetch(path, data === undefined ? undefined : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!result.ok) {
    const body = await result.json().catch(() => null);
    const detail = body?.detail;
    const message = typeof detail === "string" ? detail
      : detail?.message ?? (Array.isArray(detail) ? detail[0]?.msg : null);
    throw new Error(message ?? ("请求失败 (" + result.status + ")"));
  }
  return result.json() as Promise<T>;
}

export type Source = "REAL" | "SIMULATED";
export const sourceLabels: Record<Source, string> = { REAL: "真实公告", SIMULATED: "模拟资料" };

export type Decision = "SUPPORTED" | "REFUTED" | "DISPUTED" | "INSUFFICIENT";
export const decisions: Record<Decision, string> = {
  SUPPORTED: "有证据支持",
  REFUTED: "被证据反驳",
  DISPUTED: "存在争议",
  INSUFFICIENT: "证据不足",
};

export const units: Record<string, string> = { yuan: "元", wan_yuan: "万元", yi_yuan: "亿元" };

export function formatDecimal(value: string) {
  const [whole, fraction] = value.split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return fraction === undefined ? grouped : grouped + "." + fraction;
}

export interface Disclosure {
  id: string;
  title: string;
  subject: string;
  source_kind: Source;
  source_url: string | null;
  disclosure_type: string;
  published_date: string;
  published_at: string | null;
  ingested_at: string;
  time_precision: "DATE_ONLY" | "TIMESTAMP";
  sha256: string;
  media_type: string;
  parses: { version: string; page_count: number }[];
}

export interface PageText {
  document_id: string;
  parse_version: string;
  number: number;
  text: string;
}

export const disclosures: Record<string, string> = {
  RESULTS: "业绩快报", CORRECTION: "更正公告", UPDATE: "业务进展",
  AUDIT: "审计资料", COMMENTARY: "转述", AGREEMENT: "协议",
};

export const metrics: Record<string, string> = {
  operating_revenue: "营业总收入", operating_profit: "营业利润", total_assets: "总资产",
};
