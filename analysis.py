"""
What keeps Dutch teachers in the classroom?
Modifiable working conditions and teachers' intention to leave the profession.

Data : OECD TALIS 2024, international teacher file (ttgintt4.csv), Netherlands.
Author: Fatima Mashood, 2026.

The Dutch teacher shortage (lerarentekort) is usually discussed as "teachers are
unhappy". The more useful question for schools and policy is "which conditions we
can actually change predict a teacher thinking about leaving?". This script
separates *modifiable* working conditions (stress, mental-health strain, work-life
balance, recognition, autonomy, pay, leadership, workload) from *fixed* background
characteristics (age, experience) and estimates how each relates to the intention
to leave.

Survey design notes that shape the modelling:
  * Estimates use the final teacher weight (TCHWGT); standard errors use TALIS's
    100 balanced-repeated-replication weights (TRWGT1-100, Fay's k = 0.5).
  * TALIS 2024 rotates question blocks. The well-being / recognition items are
    asked of (almost) all teachers, but the job-design items (autonomy, pay,
    hours, leadership, classroom disruption) are split across rotated forms that
    do NOT all co-occur. We therefore fit ONE well-powered base model on the
    universal items (n~2,300), then add each rotated lever to that base on the
    subsample where it was administered ("augmented models"). This avoids
    listwise-deleting the whole sample down to zero.
  * Gender and contract tenure are fully suppressed in the NL file, so they are
    dropped.
  * Data source is the OECD's CSV export of the teacher file. The CSV carries no
    SPSS value labels, so TALIS's reserved missing codes are stored as numbers:
    single-digit items use 6-9 (8 = "not administered" on rotated forms, 9 =
    "omitted/invalid"), and the wide count fields (hours, years) use 996-999.
    Substantive values never reach those ranges, so recoding them to NaN exactly
    reproduces the value-label-based missing handling of the .sav file.
"""

from pathlib import Path
import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.stats import norm
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

# ----------------------------------------------------------------------------
# 0. Config
# ----------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent
DATA = HERE / "datasets" / "SPSS" / "TALIS2024_teachers_NoESE_CSV" / "ttgintt4.csv"
FIGS = HERE / "figures"; FIGS.mkdir(exist_ok=True)
OUT = HERE / "outputs"; OUT.mkdir(exist_ok=True)
COUNTRY = "NLD"
GREEN, GREY = "#2e8b57", "#9aa0a6"     # modifiable vs fixed
N_REPL = 100
BRR_MULT = 1.0 / (N_REPL * (1 - 0.5) ** 2)   # Fay k=0.5  -> 0.04

OUTCOME = "TT4G78F"          # "I wonder whether it would be better to choose another profession"
SECOND = "TT4G74"            # years intend to keep teaching (independent robustness outcome)
AUT_ITEMS = ["TT4G57A", "TT4G57B", "TT4G57C", "TT4G57D", "TT4G57E"]
WEIGHTS = ["TCHWGT"] + [f"TRWGT{i}" for i in range(1, N_REPL + 1)]

# Universal base predictors: (clean name, group, continuous)
BASE = {
    "STRESS":      ("Work stress",              "mod", True),
    "MHEALTH":     ("Job harms mental health",  "mod", True),
    "WORKLIFE":    ("Time for personal life",   "mod", True),
    "VALUED":      ("Feels valued by society",  "mod", True),
    "AGEGRP":      ("Age (grouped)",            "fixed", True),
    "EXP":         ("Years experience",         "fixed", True),
}
# Rotated levers, each added to the base on its own subsample
AUGMENT = {
    "AUTONOMY":    ("Classroom autonomy",         "mod", True),
    "DISRUPT":     ("Disruption / lost time",     "mod", True),
    "SALARY":      ("Salary satisfaction",        "mod", True),
    "HOURS":       ("Weekly working hours",       "mod", True),
    "LEADERSHIP":  ("Supportive school leader",   "mod", True),
    "FIRSTCHOICE": ("Teaching was 1st choice",    "fixed", False),
}

usecols = list(dict.fromkeys(
    ["CNTRY", OUTCOME, SECOND] + AUT_ITEMS +
    ["TT4G76A", "TT4G76C", "TT4G76B", "TT4G78H", "T4TAGEGR", "T4TYEXPTT",
     "TT4G54D", "TT4G79A", "TT4G14", "TT4G66F", "TT4G08"] + WEIGHTS))

# Wide count fields use 996-999 as reserved-missing; everything else 6-9.
COUNT_VARS = ["TT4G14", SECOND]   # weekly hours, years intend to keep teaching
# Experience-group labels (came from SPSS value labels, hardcoded for the CSV).
EXP_LABELS = {1: "Less than or equal to 5 years", 2: "6-10 years",
              3: "11-20 years", 4: "Above 20 years"}

# ----------------------------------------------------------------------------
# 1. Load + clean
# ----------------------------------------------------------------------------
print("Reading TALIS file (NL subset)…")
raw = pd.read_csv(str(DATA), sep=";", usecols=usecols)
nl = raw[raw["CNTRY"] == COUNTRY].copy()
print(f"Netherlands teachers: n = {len(nl):,}")

# Recode TALIS reserved-missing codes to NaN. The CSV has no value labels, but
# the sentinels sit above every substantive value: 6-9 for single-digit items,
# 996-999 for the wide count fields.
for col in usecols:
    if col == "CNTRY" or col in WEIGHTS:
        continue
    cutoff = 996 if col in COUNT_VARS else 6
    nl[col] = pd.to_numeric(nl[col], errors="coerce").where(lambda s: s < cutoff)

# Build a tidy derived frame in one shot (avoids fragmentation).
D = pd.DataFrame({
    "LEAVE":       np.where(nl[OUTCOME].isin([3, 4]), 1.0,
                            np.where(nl[OUTCOME].isin([1, 2]), 0.0, np.nan)),
    "OUTORD":      nl[OUTCOME],
    "STRESS":      nl["TT4G76A"],
    "MHEALTH":     nl["TT4G76C"],
    "WORKLIFE":    nl["TT4G76B"],
    "VALUED":      nl["TT4G78H"],
    "AGEGRP":      pd.to_numeric(nl["T4TAGEGR"], errors="coerce"),
    "EXP":         pd.to_numeric(nl["T4TYEXPTT"], errors="coerce"),
    "AUTONOMY":    nl[AUT_ITEMS].mean(axis=1),
    "DISRUPT":     nl["TT4G54D"],
    "SALARY":      nl["TT4G79A"],
    "HOURS":       pd.to_numeric(nl["TT4G14"], errors="coerce"),
    "LEADERSHIP":  nl["TT4G66F"],
    "FIRSTCHOICE": (nl["TT4G08"] == 1).astype(float).where(nl["TT4G08"].notna()),
    "YEARS_LEFT":  pd.to_numeric(nl[SECOND], errors="coerce"),
}).join(nl[WEIGHTS])

# ----------------------------------------------------------------------------
# 2. Weighted descriptives
# ----------------------------------------------------------------------------
def wpct(frame, col="LEAVE"):
    w, m = frame["TCHWGT"], frame[col].astype(float)
    keep = m.notna() & w.notna()
    return 100 * np.average(m[keep], weights=w[keep])

valid = D[D["LEAVE"].notna() & D["TCHWGT"].notna()].copy()
overall = wpct(valid)
print(f"\nWeighted % considering leaving (overall): {overall:.1f}%")

by_exp = {c: (EXP_LABELS.get(c, str(c)), wpct(valid[valid.EXP == c]), int((valid.EXP == c).sum()))
          for c in sorted(valid["EXP"].dropna().unique())}
print("By experience group (label, % leaving, n):")
for c, (lab, p, n) in by_exp.items():
    print(f"  [{c:.0f}] {lab:<30} {p:5.1f}%  (n={n})")

stress_lab = {1: "Not at all", 2: "Some", 3: "Quite a bit", 4: "A lot"}
by_stress = {c: (stress_lab[c], wpct(valid[valid.STRESS == c]), int((valid.STRESS == c).sum()))
             for c in [1, 2, 3, 4] if (valid.STRESS == c).any()}

# ----------------------------------------------------------------------------
# 3. Weighted logistic + BRR standard errors
# ----------------------------------------------------------------------------
def zscore(s):
    return (s - s.mean()) / s.std(ddof=0)

def fit_with_brr(frame, predictors):
    """Fit weighted logistic; return OR table with BRR 95% CI. Continuous
    predictors are z-scored so ORs are per 1 SD."""
    cont = {**BASE, **AUGMENT}
    X = pd.DataFrame(index=frame.index)
    for name in predictors:
        col = frame[name].astype(float)
        is_cont = cont[name][2]
        X[name] = zscore(col) if is_cont else col
    X = sm.add_constant(X)
    y = frame["LEAVE"].astype(float)

    def _fit(w):
        return sm.GLM(y, X, family=sm.families.Binomial(), freq_weights=w).fit().params

    beta = _fit(frame["TCHWGT"])
    reps = np.array([_fit(frame[f"TRWGT{i}"]).values for i in range(1, N_REPL + 1)])
    se = np.sqrt(BRR_MULT * ((reps - beta.values) ** 2).sum(axis=0))
    z = beta.values / se
    return pd.DataFrame({
        "OR": np.exp(beta.values),
        "CI_low": np.exp(beta.values - 1.96 * se),
        "CI_high": np.exp(beta.values + 1.96 * se),
        "p_value": 2 * (1 - norm.cdf(np.abs(z))),
    }, index=beta.index)

# --- Base model (universal items, full power) ---
base_pred = list(BASE.keys())
base_df = valid.dropna(subset=base_pred + ["LEAVE"]).copy()
print(f"\nBase model n = {len(base_df):,}")
base_res = fit_with_brr(base_df, base_pred)

# --- Augmented models: add each rotated lever to the base, on its subsample ---
aug_rows = []
for lever in AUGMENT:
    sub = valid.dropna(subset=base_pred + [lever, "LEAVE"]).copy()
    r = fit_with_brr(sub, base_pred + [lever]).loc[lever]
    r["n"] = len(sub)
    aug_rows.append(r.rename(lever))
    print(f"  augmented (+{lever:<11}) n = {len(sub):,}")
aug_res = pd.DataFrame(aug_rows)

# --- Assemble combined results table ---
meta_map = {**BASE, **AUGMENT}
combined = pd.concat([base_res.drop("const"), aug_res[["OR", "CI_low", "CI_high", "p_value"]]])
combined["label"] = [meta_map[i][0] for i in combined.index]
combined["group"] = [meta_map[i][1] for i in combined.index]
combined["source"] = ["base" if i in BASE else "augmented" for i in combined.index]
combined = combined[["label", "group", "source", "OR", "CI_low", "CI_high", "p_value"]]
combined.to_csv(OUT / "odds_ratios.csv")
print("\n=== Adjusted odds ratios (BRR 95% CI) — outcome: considering leaving ===")
with pd.option_context("display.float_format", lambda v: f"{v:.3f}", "display.width", 120):
    print(combined)

# ----------------------------------------------------------------------------
# 4. Robustness 1 — ordinal proportional-odds (unweighted) on the 4-point item
# ----------------------------------------------------------------------------
try:
    from statsmodels.miscmodels.ordinal_model import OrderedModel
    od = valid.dropna(subset=base_pred + ["OUTORD"]).copy()
    Xo = pd.DataFrame({n: zscore(od[n].astype(float)) for n in base_pred}, index=od.index)
    om = OrderedModel(od["OUTORD"].astype(int), Xo, distr="logit").fit(method="bfgs", disp=False)
    ord_tab = pd.DataFrame({"OR": np.exp(om.params[Xo.columns]), "p": om.pvalues[Xo.columns]})
    ord_tab.to_csv(OUT / "ordinal_robustness.csv")
    print("\nRobustness 1 (ordinal proportional-odds, base predictors):")
    with pd.option_context("display.float_format", lambda v: f"{v:.3f}"):
        print(ord_tab)
except Exception as e:
    print("ordinal model skipped:", e)

# ----------------------------------------------------------------------------
# 5. Robustness 2 — independent outcome: years intend to keep teaching
# ----------------------------------------------------------------------------
cc = valid.dropna(subset=["YEARS_LEFT", "STRESS", "TCHWGT"])
if len(cc):
    r = np.corrcoef(cc["YEARS_LEFT"], cc["STRESS"])[0, 1]
    print(f"\nRobustness 2: corr(work stress, years intend to keep teaching) = {r:+.3f} "
          f"(n={len(cc)}); negative = more stress -> fewer years.")

# ----------------------------------------------------------------------------
# 6. Figure 1 — landscape
# ----------------------------------------------------------------------------
fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
fig.suptitle("TALIS 2024 — Netherlands secondary teachers considering leaving the profession",
             fontweight="bold", fontsize=12)
codes = sorted(by_exp)
ax[0].bar(range(len(codes)), [by_exp[c][1] for c in codes], color=GREEN, alpha=.85)
ax[0].axhline(overall, ls="--", color="grey", lw=1)
ax[0].text(len(codes) - .5, overall + .5, f"overall {overall:.0f}%", ha="right",
           color="grey", fontsize=8)
ax[0].set_xticks(range(len(codes)))
ax[0].set_xticklabels([by_exp[c][0].replace("years", "yr").replace("Less than or equal to", "≤")
                       for c in codes], rotation=30, ha="right", fontsize=8)
ax[0].set_ylabel("% considering leaving"); ax[0].set_title("…by years of experience", fontsize=10)
for i, c in enumerate(codes):
    ax[0].text(i, by_exp[c][1] + .4, f"{by_exp[c][1]:.0f}", ha="center", fontsize=8)
sc = sorted(by_stress)
ax[1].bar([by_stress[c][0] for c in sc], [by_stress[c][1] for c in sc], color=GREEN, alpha=.85)
ax[1].set_ylabel("% considering leaving"); ax[1].set_title("…by self-reported work stress", fontsize=10)
for i, c in enumerate(sc):
    ax[1].text(i, by_stress[c][1] + .8, f"{by_stress[c][1]:.0f}", ha="center", fontsize=8)
fig.tight_layout(rect=[0, 0, 1, 0.94])
fig.savefig(FIGS / "fig1_landscape.png", dpi=150, bbox_inches="tight")
print("\nsaved figures/fig1_landscape.png")

# ----------------------------------------------------------------------------
# 7. Figure 2 — odds-ratio forest plot
# ----------------------------------------------------------------------------
plot = combined.sort_values("OR")
colors = [GREEN if g == "mod" else GREY for g in plot["group"]]
fig, ax = plt.subplots(figsize=(8.6, 6.2))
yp = np.arange(len(plot))
for i, (_, row) in enumerate(plot.iterrows()):
    ax.plot([row["CI_low"], row["CI_high"]], [i, i], color=colors[i], lw=2, alpha=.85, zorder=2)
ax.scatter(plot["OR"], yp, color=colors, s=44, zorder=3)
ax.axvline(1, color="black", lw=1)
ax.set_yticks(yp); ax.set_yticklabels(plot["label"], fontsize=9)
ax.set_xscale("log")
ax.set_xlabel("Adjusted odds ratio for 'considering leaving the profession'\n"
              "continuous predictors per 1 SD · log scale · 95% CI (BRR)", fontsize=9)
ax.set_title("What predicts a Dutch teacher considering leaving?\n"
             "TALIS 2024 · weighted logistic regression", fontsize=11, fontweight="bold")
ax.legend(handles=[Line2D([0], [0], marker="o", color="w", markerfacecolor=GREEN, markersize=9,
                          label="Modifiable working condition"),
                   Line2D([0], [0], marker="o", color="w", markerfacecolor=GREY, markersize=9,
                          label="Fixed background characteristic")],
          loc="lower right", fontsize=8, frameon=True)
fig.tight_layout()
fig.savefig(FIGS / "fig2_oddsratios.png", dpi=150, bbox_inches="tight")
print("saved figures/fig2_oddsratios.png\nDone.")
