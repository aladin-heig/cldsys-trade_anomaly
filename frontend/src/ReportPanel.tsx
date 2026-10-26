import { useEffect, useState } from "react";
import { getReport, requestReport, type Cell, type ReportStatus } from "./api";
import type { Frequency } from "./catalog";
import { countryName } from "./countries";
import { pct, usd } from "./Heatmap";

const POLL_MS = 3000;
const GIVE_UP_MS = 5 * 60 * 1000;

const STATUS_TEXT: Record<ReportStatus["status"], string> = {
  not_generated: "Rapport non généré",
  in_progress: "Génération en cours…",
  failed: "La génération a échoué",
  generated: "Rapport généré",
};

const CAUSE_TEXT: Record<string, string> = {
  anomaly_fob_above_cif: "le prix déclaré à l'export (FOB) dépasse le prix déclaré à l'import (CIF)",
  anomaly_gap_above_threshold: "l'écart entre CIF et FOB dépasse 35 %",
  anomaly_one_sided: "le flux n'est déclaré que par un des deux pays, aucun écart ne peut être calculé",
};

interface Props {
  exporter: string;
  hs4: string;
  freq: Frequency;
  cell: Cell;
  onClose: () => void;
}

/** A click on a red cell starts the report workflow at once, then follows its status until the PDF is ready. */
export function ReportPanel({ exporter, hs4, freq, cell, onClose }: Props) {
  const [report, setReport] = useState<ReportStatus>();
  const [error, setError] = useState<string>();

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const startedAt = Date.now();
    setReport(undefined);
    setError(undefined);

    const follow = (status: ReportStatus) => {
      if (stopped) return;
      setReport(status);
      if (status.status !== "in_progress") return;
      if (Date.now() - startedAt > GIVE_UP_MS) {
        setError("Le rapport prend plus de temps que prévu. Réessayez dans quelques minutes.");
        return;
      }
      timer = setTimeout(() => getReport(status.reportId).then(follow, fail), POLL_MS);
    };
    const fail = (e: Error) => !stopped && setError(e.message);

    requestReport({ exporter, partner: cell.partner, hs4, freq, period: cell.period }).then(follow, fail);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [exporter, hs4, freq, cell]);

  return (
    <aside className="report-panel" aria-live="polite">
      <header>
        <h2>
          {countryName(exporter)} → {countryName(cell.partner)} · {cell.period}
        </h2>
        <button type="button" className="close" onClick={onClose} aria-label="Fermer">
          ×
        </button>
      </header>

      <p className="cause">Anomalie : {CAUSE_TEXT[cell.status] ?? cell.status}.</p>
      <dl className="figures">
        <dt>Prix FOB (exportateur)</dt>
        <dd>{usd(cell.fob_usd_t)}</dd>
        <dt>Prix CIF (importateur)</dt>
        <dd>{usd(cell.cif_usd_t)}</dd>
        <dt>Écart</dt>
        <dd>{cell.status === "anomaly_one_sided" ? "non calculable" : pct(cell.gap)}</dd>
      </dl>

      <div className={`status status-${report?.status ?? "starting"}`}>
        {error ? (
          <p className="error">{error}</p>
        ) : !report ? (
          <p>Demande du rapport…</p>
        ) : (
          <>
            <p>{STATUS_TEXT[report.status]}</p>
            {report.status === "in_progress" && <div className="spinner" aria-hidden />}
            {report.status === "generated" && report.downloadUrl && (
              <a className="download" href={report.downloadUrl} target="_blank" rel="noreferrer">
                Télécharger le rapport PDF
              </a>
            )}
            {report.status === "failed" && (
              <p className="error">
                {report.error}
                {report.cause ? ` : ${report.cause}` : ""}
              </p>
            )}
          </>
        )}
      </div>
    </aside>
  );
}
