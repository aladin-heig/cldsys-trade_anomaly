"""Step 2 of the report workflow: chart + PDF, stored in S3 under reports/<reportId>.pdf.

The PDF's presence in S3 is what marks the report as generated for the read function.
"""
import io
import math
import os

import boto3
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from fpdf import FPDF

s3 = boto3.client("s3")
REPORTS_BUCKET = os.environ["REPORTS_BUCKET"]
# matplotlib ships DejaVu Sans, which covers French accents and typographic quotes.
FONT_DIR = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
STATUS_LABELS = {
    "anomaly_fob_above_cif": "FOB supérieur au CIF",
    "anomaly_gap_above_threshold": "écart supérieur à 35 %",
    "anomaly_one_sided": "flux déclaré par un seul pays",
    "ok": "cohérent",
}
LIKELIHOOD = {"faible": "faible", "moyenne": "moyenne", "elevee": "élevée"}
CAUSES = {
    "reacheminement": "Réacheminement par un pays tiers",
    "conflit": "Conflit",
    "tarifs_sanctions": "Tarifs ou sanctions",
    "erreur_declaration": "Erreur de déclaration",
    "absence_declaration": "Absence de déclaration d'un pays",
    "decalage_temporel": "Décalage dans le temps entre les deux déclarations",
    "autre": "Autre cause",
}


def gap_text(gap):
    # No gap when the FOB price is zero: the division by zero tends to infinity.
    return "infini (prix FOB nul)" if gap is None else f"{gap:+.1%}"


def usd(x):
    return f"{x:,.0f}".replace(",", " ")  # thin space as thousands separator


def figures(a, exporter, partner):
    """One sentence with the cell's prices; a one-sided cell only has the side that declared."""
    if a["status"] != "anomaly_one_sided":
        text = (
            f"Prix FOB {usd(a['fob_usd_t'])} USD/t, prix CIF {usd(a['cif_usd_t'])} USD/t, "
            f"écart {gap_text(a.get('gap'))} ({STATUS_LABELS.get(a['status'], a['status'])})."
        )
        # Quarter or year: each side counts every month it declared, which may differ.
        if a.get("fob_months") != a.get("cif_months"):
            text += f" Mois déclarés : {a['fob_months']} à l'export, {a['cif_months']} à l'import."
        return text
    if "fob_usd_t" in a:
        declared = f"{exporter} déclare une exportation de {usd(a['fob_value_usd'])} USD ({usd(a['fob_usd_t'])} USD/t FOB)"
        silent = partner
    else:
        declared = f"{partner} déclare une importation de {usd(a['cif_value_usd'])} USD ({usd(a['cif_usd_t'])} USD/t CIF)"
        silent = exporter
    return f"Flux déclaré par un seul pays : {declared} ; {silent} ne déclare pas ce flux. Aucun écart ne peut être calculé."


def chart(history, period):
    # A one-sided period has a single price: the missing one is left as a gap in its line.
    periods = [c["period"] for c in history]
    fig, ax = plt.subplots(figsize=(8, 3.2), dpi=150)
    ax.plot(periods, [c.get("fob_usd_t", math.nan) for c in history], marker="o", ms=3, label="FOB (exportateur)")
    ax.plot(periods, [c.get("cif_usd_t", math.nan) for c in history], marker="o", ms=3, label="CIF (importateur)")
    if period in periods:
        ax.axvline(period, color="#c62828", linestyle="--", linewidth=1)
    ax.set_ylabel("USD par tonne")
    ax.tick_params(axis="x", labelrotation=60, labelsize=7)
    ax.legend(frameon=False, fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png")
    plt.close(fig)
    buffer.seek(0)
    return buffer


def build_pdf(event):
    a, e = event["anomaly"], event["explanation"]
    pdf = FPDF()
    pdf.add_font("DejaVu", "", os.path.join(FONT_DIR, "DejaVuSans.ttf"))
    pdf.add_font("DejaVu", "B", os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf"))
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    def para(height, text):
        # Back to the left margin after each block (fpdf2 otherwise continues at the right edge).
        pdf.multi_cell(0, height, text, new_x="LMARGIN", new_y="NEXT")

    def heading(text):
        pdf.ln(3)
        pdf.set_font("DejaVu", "B", 12)
        para(7, text)
        pdf.set_font("DejaVu", "", 10)

    pdf.set_font("DejaVu", "B", 15)
    para(8, e["titre"])
    pdf.set_font("DejaVu", "", 9)
    para(5, f"{event['exporter']} vers {event['partner']} · HS {event['hs4']} · période {event['period']} · rapport {event['reportId']}")
    pdf.ln(2)
    pdf.set_font("DejaVu", "", 10)
    para(5, figures(a, event["exporter"], event["partner"]))
    pdf.image(chart(event["history"], event["period"]), w=180)

    heading("Résumé")
    para(5, e["resume"])
    heading("Hypothèses")
    for h in e["hypotheses"]:
        pdf.set_font("DejaVu", "B", 10)
        para(5, f"{CAUSES[h['cause']]} (vraisemblance {LIKELIHOOD[h['vraisemblance']]})")
        pdf.set_font("DejaVu", "", 10)
        para(5, h["explication"])
        pdf.ln(1)
    heading("Vérifications suggérées")
    for v in e["verifications"]:
        para(5, f"• {v}")
    heading("Conclusion")
    para(5, e["conclusion"])
    heading("Sources")
    para(5, "Données : UN Comtrade, déclarations mensuelles des deux pays.")
    sources = event.get("sources", [])
    if not sources:
        para(5, "Aucune page Wikipédia n'a pu être consultée : l'explication repose sur les seules données.")
    for s in sources:
        # The numbers match the [n] references in the text; uncited pages were read but not used.
        para(5, f"[{s['index']}] {s['title']}, Wikipédia{'' if s['cited'] else ' (consultée, non citée)'} : {s['url']}")
    pdf.ln(4)
    pdf.set_font("DejaVu", "", 8)
    para(4, f"Explication générée par {event.get('model', 'un modèle de langage')} à partir des données UN Comtrade et des pages listées ; à vérifier avant toute décision.")
    return bytes(pdf.output())


def handler(event, context):
    key = f"reports/{event['reportId']}.pdf"
    s3.put_object(Bucket=REPORTS_BUCKET, Key=key, Body=build_pdf(event), ContentType="application/pdf")
    return {"reportId": event["reportId"], "reportKey": key}
