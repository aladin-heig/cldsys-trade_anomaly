import { Fragment } from "react";
import { isAnomaly, type Cell, type Heatmap as HeatmapData } from "./api";
import { countryName } from "./countries";

const MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."];

// No gap when the FOB price is zero: the division by zero tends to infinity.
export const pct = (x?: number) => (x === undefined || x === null ? "infini (FOB nul)" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)} %`);
export const usd = (x?: number) => (x === undefined || x === null ? "non déclaré" : `${Math.round(x).toLocaleString("fr-CH")} USD/t`);

function describe(cell: Cell | undefined, partner: string, period: string): string {
  const where = `${countryName(partner)} · ${period}`;
  if (!cell) return `${where}\nAucune donnée`;
  if (cell.status === "anomaly_one_sided") {
    return `${where}\nFlux déclaré par un seul des deux pays\nFOB ${usd(cell.fob_usd_t)} · CIF ${usd(cell.cif_usd_t)}\nAnomalie : cliquer pour le rapport`;
  }
  const verdict = isAnomaly(cell.status) ? "Anomalie : cliquer pour le rapport" : "Cohérent";
  return `${where}\nFOB ${usd(cell.fob_usd_t)} · CIF ${usd(cell.cif_usd_t)}\nÉcart ${pct(cell.gap)}\n${verdict}`;
}

/** Column label under the year row: the quarter ("T1") or the month ("janv."). */
function subLabel(period: string): string {
  if (period.includes("-Q")) return `T${period.slice(-1)}`;
  return MONTHS[Number(period.slice(5, 7)) - 1];
}

interface Props {
  data: HeatmapData;
  selected?: { partner: string; period: string };
  onSelect: (cell: Cell) => void;
}

export function Heatmap({ data, selected, onSelect }: Props) {
  const byKey = new Map(data.cells.map((c) => [`${c.partner}|${c.period}`, c]));
  const yearly = data.freq === "Y";
  // For months and quarters, a first header row groups the columns by year.
  const years = [...new Set(data.periods.map((p) => p.slice(0, 4)))];
  const span = (year: string) => data.periods.filter((p) => p.startsWith(year)).length;

  if (data.partners.length === 0) {
    return <p className="empty">Aucune donnée pour cette sélection.</p>;
  }

  return (
    <div className="heatmap-scroll">
      <table className={`heatmap freq-${data.freq}`}>
        <thead>
          <tr>
            <th className="corner" rowSpan={yearly ? 1 : 2} />
            {yearly
              ? data.periods.map((p) => <th key={p}>{p}</th>)
              : years.map((y) => (
                  <th key={y} colSpan={span(y)} className="year">
                    {y}
                  </th>
                ))}
          </tr>
          {!yearly && (
            <tr>
              {data.periods.map((p) => (
                <th key={p} className="sub">
                  {subLabel(p)}
                </th>
              ))}
            </tr>
          )}
        </thead>
        <tbody>
          {data.partners.map((partner) => (
            <tr key={partner}>
              <th scope="row" className="partner">
                {countryName(partner)}
              </th>
              {data.periods.map((period) => {
                const cell = byKey.get(`${partner}|${period}`);
                const isSelected = selected?.partner === partner && selected.period === period;
                // One-sided flows are anomalies too, hatched to tell them from price gaps.
                const kind = !cell
                  ? "none"
                  : cell.status === "anomaly_one_sided"
                    ? "anomaly one-sided"
                    : isAnomaly(cell.status)
                      ? "anomaly"
                      : cell.status;
                return (
                  <Fragment key={period}>
                    {cell && isAnomaly(cell.status) ? (
                      <td className={`cell ${kind}${isSelected ? " selected" : ""}`}>
                        <button type="button" title={describe(cell, partner, period)} onClick={() => onSelect(cell)}>
                          <span className="sr-only">{describe(cell, partner, period)}</span>
                        </button>
                      </td>
                    ) : (
                      <td className={`cell ${kind}`} title={describe(cell, partner, period)} />
                    )}
                  </Fragment>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
