import { useState, type FormEvent } from "react";
import { getHeatmap, type Cell, type Heatmap as HeatmapData } from "./api";
import { EXPORTERS, FREQUENCIES, PRODUCTS, type Frequency } from "./catalog";
import { countryName } from "./countries";
import { Heatmap } from "./Heatmap";
import { ReportPanel } from "./ReportPanel";

interface Props {
  userEmail?: string;
  onSignOut?: () => void;
}

export function App({ userEmail, onSignOut }: Props) {
  const [exporter, setExporter] = useState<string>("USA");
  const [hs4, setHs4] = useState("1001");
  const [freq, setFreq] = useState<Frequency>("Y");
  const [data, setData] = useState<HeatmapData>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const [selected, setSelected] = useState<Cell>();

  async function show(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(undefined);
    setSelected(undefined);
    try {
      setData(await getHeatmap(exporter, hs4, freq));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="page">
      <header className="topbar">
        <h1>Détection d'anomalies commerciales</h1>
        {onSignOut && (
          <div className="user">
            <span>{userEmail}</span>
            <button type="button" onClick={onSignOut}>
              Se déconnecter
            </button>
          </div>
        )}
      </header>

      <main className="layout">
        <section className="panel">
          <form className="config" onSubmit={show}>
            <label>
              <span>Exportateur</span>
              <select value={exporter} onChange={(e) => setExporter(e.target.value)}>
                {EXPORTERS.map((code) => (
                  <option key={code} value={code}>
                    {countryName(code)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>Produit (HS)</span>
              <select value={hs4} onChange={(e) => setHs4(e.target.value)}>
                {PRODUCTS.map((p) => (
                  <option key={p.code} value={p.code}>
                    {p.code} · {p.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>Fréquence</span>
              <select value={freq} onChange={(e) => setFreq(e.target.value as Frequency)}>
                {FREQUENCIES.map((f) => (
                  <option key={f.code} value={f.code}>
                    {f.name}
                  </option>
                ))}
              </select>
            </label>
            <button type="submit" className="show" disabled={loading}>
              {loading ? "Chargement…" : "Afficher"}
            </button>
          </form>

          {error && <p className="error">{error}</p>}
          {!data && !error && (
            <p className="hint">Choisissez un exportateur, un produit et une fréquence, puis cliquez sur Afficher.</p>
          )}
          {data && (
            <>
              <Heatmap
                data={data}
                selected={selected && { partner: selected.partner, period: selected.period }}
                onSelect={setSelected}
              />
              <ul className="legend">
                <li>
                  <span className="swatch ok" /> Cohérent
                </li>
                <li>
                  <span className="swatch anomaly" /> Anomalie, cliquer pour le rapport
                </li>
                <li>
                  <span className="swatch anomaly one-sided" /> Anomalie : flux déclaré d'un seul côté, cliquer pour le rapport
                </li>
                <li>
                  <span className="swatch none" /> Pas de données
                </li>
              </ul>
            </>
          )}
        </section>

        {data && selected && (
          <ReportPanel
            exporter={data.exporter}
            hs4={data.hs4}
            freq={data.freq}
            cell={selected}
            onClose={() => setSelected(undefined)}
          />
        )}
      </main>
    </div>
  );
}
