import { fetchAuthSession } from "aws-amplify/auth";
import type { Frequency } from "./catalog";

const API_URL = import.meta.env.VITE_API_URL.replace(/\/$/, "");

export type CellStatus = "ok" | "anomaly_fob_above_cif" | "anomaly_gap_above_threshold" | "anomaly_one_sided";

export interface Cell {
  partner: string;
  period: string;
  status: CellStatus;
  // A one-sided cell only has the fields of the country that declared the flow.
  fob_usd_t?: number;
  cif_usd_t?: number;
  fob_value_usd?: number;
  cif_value_usd?: number;
  gap?: number;
  fob_months?: number;
  cif_months?: number;
}

export interface Heatmap {
  exporter: string;
  hs4: string;
  freq: Frequency;
  partners: string[];
  periods: string[];
  cells: Cell[];
}

export interface ReportStatus {
  reportId: string;
  status: "not_generated" | "in_progress" | "failed" | "generated";
  downloadUrl?: string;
  error?: string;
  cause?: string;
}

export interface ReportRequest {
  exporter: string;
  partner: string;
  hs4: string;
  freq: Frequency;
  period: string;
}

export const isAnomaly = (status: CellStatus) => status.startsWith("anomaly");

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  // The API's JWT authorizer expects a Cognito token issued for this app client.
  const { tokens } = await fetchAuthSession();
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${tokens?.idToken?.toString() ?? ""}`,
      "Content-Type": "application/json",
      ...init.headers,
    },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.error ?? body.message ?? `Erreur ${response.status}`);
  }
  return body as T;
}

export const getHeatmap = (exporter: string, hs4: string, freq: Frequency) =>
  call<Heatmap>(`/anomalies?${new URLSearchParams({ exporter, hs4, freq })}`);

export const requestReport = (request: ReportRequest) =>
  call<ReportStatus>("/reports", { method: "POST", body: JSON.stringify(request) });

export const getReport = (reportId: string) => call<ReportStatus>(`/reports/${encodeURIComponent(reportId)}`);
