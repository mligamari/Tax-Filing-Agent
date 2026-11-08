"""Synthetic 2025 W-2 + quarterly wage data for a fictional Virginia employer.

Same input shape as gen_w2_2026_ny.py (W-2 Data / Payroll Detail / Employer / Rates & Method)
plus a "Quarterly Wages" sheet (one row per employee per quarter with wages) for VEC UI
wage reports. Seeded, so reruns give the same file.
"""
import datetime as dt
import os
import random
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

random.seed(2025_51)
N = 50
YEAR = 2025

# ---------------- 2025 rates ----------------
SS_RATE, SS_BASE = 0.062, 176_100.00
MED_RATE, ADDL_MED_RATE, ADDL_MED_THRESH = 0.0145, 0.009, 200_000.00
FUTA_RATE, FUTA_BASE = 0.006, 7_000.00          # employer only, net of 5.4% credit
VA_SUI_BASE, VA_SUI_RATE = 8_000.00, 0.025        # employer only; ASSUMED (see Rates sheet)

FED_STD = {"Single": 15_750, "Married": 31_500, "Head of Household": 23_625}   # 2025 as amended by OBBBA
FED_BRACKETS = {   # Rev. Proc. 2024-40
    "Single": [(11_925, .10), (48_475, .12), (103_350, .22), (197_300, .24),
               (250_525, .32), (626_350, .35), (float("inf"), .37)],
    "Married": [(23_850, .10), (96_950, .12), (206_700, .22), (394_600, .24),
                (501_050, .32), (751_600, .35), (float("inf"), .37)],
    "Head of Household": [(17_000, .10), (64_850, .12), (103_350, .22), (197_300, .24),
                          (250_500, .32), (626_350, .35), (float("inf"), .37)],
}
# Virginia withholding (VA-4 / Virginia Tax withholding formula)
VA_STD = {"Single": 8_750, "Married": 17_500, "Head of Household": 8_750}   # ASSUMED amounts
VA_EXEMPTION = 930                                                          # per VA-4 exemption
VA_BRACKETS = [(3_000, .02), (5_000, .03), (17_000, .05), (float("inf"), .0575)]


def bracket_tax(income, brackets):
    tax, lo = 0.0, 0.0
    for hi, rate in brackets:
        if income <= lo:
            break
        tax += (min(income, hi) - lo) * rate
        lo = hi
    return tax


r2 = lambda x: round(x + 1e-9, 2)

# ---------------- employer ----------------
EMPLOYER = {
    "ein": "54-3187620",
    "name": "Blue Ridge Precision Components Inc",
    "addr": "2200 Commonwealth Blvd",
    "city": "Richmond", "state": "VA", "zip": "23230",
    "va_id": "30-543187620F-001",    # VA Tax withholding account no. (fictional; 30 + FEIN + F001 pattern)
    "vec_acct": "0918274",           # VEC UI employer account number (fictional)
}

FIRST = ["James", "Maria", "Robert", "Aisha", "Michael", "Jennifer", "David", "Priya", "William",
         "Linda", "Carlos", "Elizabeth", "Joseph", "Mei", "Thomas", "Sarah", "Daniel", "Fatima",
         "Matthew", "Karen", "Anthony", "Nancy", "Kevin", "Grace", "Brian", "Olivia", "Jamal",
         "Sofia", "Ryan", "Hannah", "Luis", "Emily", "Andrew", "Rachel", "Tyrone", "Ana", "Patrick",
         "Megan", "Wei", "Chloe", "Marcus", "Isabella", "Sean", "Noor", "Gregory", "Danielle",
         "Victor", "Lauren", "Omar", "Katherine", "Dwayne", "Yesenia", "Harold", "Leticia"]
LAST = ["Smith", "Rodriguez", "Johnson", "Patel", "Williams", "Nguyen", "Brown", "Kowalski",
        "Garcia", "O'Brien", "Miller", "Chen", "Davis", "Okafor", "Martinez", "Fitzgerald",
        "Anderson", "Haddad", "Thompson", "Rossi", "Jackson", "Kim", "White", "Murphy", "Harris",
        "Lopez", "Clark", "Schwartz", "Lewis", "Romano", "Walker", "Singh", "Hall", "Esposito",
        "Allen", "Novak", "Young", "Cohen", "King", "Ramirez", "Wright", "Sullivan", "Scott",
        "Delgado", "Green", "Baker", "Adams", "Nelson", "Carter", "Mitchell", "Tran", "Batista-Cruz",
        "St. Clair", "Washington"]
STREETS = ["Monument Ave", "Broad St", "Cary St", "Grove Ave", "Patterson Ave", "Hull St",
           "Midlothian Tpke", "Jefferson Davis Hwy", "Granby St", "Colley Ave", "Main St",
           "Shore Dr", "Laskin Rd", "Williamson Rd", "Ivy Rd", "Lee Hwy", "Duke St", "Brook Rd"]
# All Virginia residents (no local income tax in VA)
CITIES = [("Richmond", "23220"), ("Richmond", "23226"), ("Henrico", "23228"), ("Glen Allen", "23060"),
          ("Midlothian", "23112"), ("Chesterfield", "23832"), ("Mechanicsville", "23111"),
          ("Petersburg", "23803"), ("Colonial Heights", "23834"), ("Ashland", "23005"),
          ("Norfolk", "23507"), ("Virginia Beach", "23451"), ("Chesapeake", "23320"),
          ("Williamsburg", "23185"), ("Charlottesville", "22903"), ("Fredericksburg", "22401"),
          ("Alexandria", "22314"), ("Arlington", "22201"), ("Roanoke", "24015"), ("Lynchburg", "24502")]
SUFFIXES = [""] * 46 + ["Jr", "Sr", "II", "III"]

QUARTERS = [(1, dt.date(YEAR, 1, 1), dt.date(YEAR, 3, 31)), (2, dt.date(YEAR, 4, 1), dt.date(YEAR, 6, 30)),
            (3, dt.date(YEAR, 7, 1), dt.date(YEAR, 9, 30)), (4, dt.date(YEAR, 10, 1), dt.date(YEAR, 12, 31))]
Y0, Y1 = dt.date(YEAR, 1, 1), dt.date(YEAR, 12, 31)


def fake_ssn():
    while True:
        a = random.randint(100, 899)
        if a != 666:
            return f"{a:03d}-{random.randint(1, 99):02d}-{random.randint(1, 9999):04d}"


def overlap_days(a0, a1, b0, b1):
    return max(0, (min(a1, b1) - max(a0, b0)).days + 1)


def split(total, weights):
    """Split an annual amount across quarters by weight; cents add back exactly to total."""
    tw = sum(weights)
    parts = [r2(total * w / tw) if tw else 0.0 for w in weights]
    last = max(i for i, w in enumerate(weights) if w) if tw else 3
    parts[last] = r2(total - sum(p for i, p in enumerate(parts) if i != last))
    return parts


def capped_quarters(qwages, base):
    """Per-quarter taxable wages under an annual wage base (first dollars are taxable)."""
    out, cum = [], 0.0
    for w in qwages:
        t = r2(max(0.0, min(w, base - cum)))
        out.append(t)
        cum += w
    return out


firsts = random.sample(FIRST, N)
lasts = random.sample(LAST, N)
salary_pool = ([random.uniform(24_000, 42_000) for _ in range(12)] +
               [random.uniform(42_000, 75_000) for _ in range(18)] +
               [random.uniform(75_000, 140_000) for _ in range(13)] +
               [random.uniform(140_000, 184_000) for _ in range(4)] +
               [191_250.00, 236_800.00, 312_400.00])
random.shuffle(salary_pool)
idx = random.sample(range(N), 9)
hires, terms, both = set(idx[:5]), set(idx[5:8]), {idx[8]}   # mid-year hires, separations, hired+separated

rows, detail, quarterly = [], [], []
for i in range(N):
    status = random.choices(["Single", "Married", "Head of Household"], [50, 38, 12])[0]
    va_exemptions = {"Single": 1, "Married": 2, "Head of Household": 1}[status] + random.choice([0, 0, 0, 1, 2])
    hire = Y0 if i not in hires | both else Y0 + dt.timedelta(days=random.randint(40, 300))
    if i in both:
        hire = Y0 + dt.timedelta(days=random.randint(60, 150))
    term = None
    if i in terms | both:
        term = max(hire, Y0) + dt.timedelta(days=random.randint(60, 200))
    start, end = hire, term or Y1
    frac = ((end - start).days + 1) / 365
    orig_hire = (hire if hire > Y0 else
                 dt.date(random.randint(2008, 2025), random.randint(1, 12), random.randint(1, 28)))

    base = r2(salary_pool[i] * frac)
    ot_hourly = salary_pool[i] < 80_000 and random.random() < 0.35
    overtime = r2(random.uniform(800, 6_500) * frac) if ot_hourly else 0.0
    bonus = r2(salary_pool[i] * random.choice([0.03, 0.05, 0.08, 0.10])) if (salary_pool[i] > 90_000 and term is None and random.random() < .6) else 0.0
    gross = r2(base + overtime + bonus)
    city, zipc = random.choice(CITIES)

    k401_pct = random.choice([0, 0, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10])
    k401 = r2(min(gross * k401_pct, 24_500.00))
    health125 = random.choice([0, 1_560.00, 2_340.00, 3_120.00, 4_680.00, 6_240.00])
    health125 = 0.0 if gross < 30_000 and random.random() < .5 else r2(health125 * frac)
    hsa = r2(random.choice([0, 0, 0, 1_000.00, 2_000.00, 3_000.00]) * frac) if health125 else 0.0
    hsa = 0.0 if overtime else hsa  # keep Box 12 within four codes
    er_health = r2(health125 * 4)
    dep_care = random.choice([0] * 8 + [2_500.00, 5_000.00]) if status != "Single" and frac == 1 else 0.0
    life_cov = min(round(salary_pool[i] * 2, -3), 500_000)
    imputed_life = 0.0
    if life_cov > 50_000:
        age_rate = random.choice([0.06, 0.08, 0.09, 0.10, 0.15, 0.23])  # IRS Table I $/1000/month
        imputed_life = r2((life_cov - 50_000) / 1000 * age_rate * 12 * frac)
    gross_taxable = r2(gross + imputed_life)

    sec125 = r2(health125 + hsa + dep_care)
    box1 = r2(gross_taxable - k401 - sec125)
    fica_wages = r2(gross_taxable - sec125)

    # ---- quarterly allocation: days worked per quarter, bonus paid in Q4 ----
    qdays = [overlap_days(start, end, q0, q1) for _, q0, q1 in QUARTERS]
    q_base = split(r2(base + overtime), qdays)
    q_gross = [r2(b + (bonus if n == 3 else 0)) for n, b in enumerate(q_base)]
    q_wt = q_gross
    q_imputed = split(imputed_life, q_wt)
    q_sec125 = split(sec125, q_wt)
    q_k401 = split(k401, q_wt)
    q_fica = [r2(g + im - s) for g, im, s in zip(q_gross, q_imputed, q_sec125)]
    q_box1 = [r2(f - k) for f, k in zip(q_fica, q_k401)]
    q_ss = capped_quarters(q_fica, SS_BASE)
    box3 = r2(sum(q_ss))
    box5 = fica_wages
    q_ss_tax, cum = [], 0.0
    for w in q_ss:  # tax on cumulative wages, so quarters reconcile to the annual box 4
        q_ss_tax.append(r2(r2((cum + w) * SS_RATE) - r2(cum * SS_RATE)))
        cum += w
    box4 = r2(sum(q_ss_tax))
    q_med_tax, cum = [], 0.0
    for w in q_fica:
        before = r2(cum * MED_RATE + max(0, cum - ADDL_MED_THRESH) * ADDL_MED_RATE)
        cum += w
        after = r2(cum * MED_RATE + max(0, cum - ADDL_MED_THRESH) * ADDL_MED_RATE)
        q_med_tax.append(r2(after - before))
    box6 = r2(sum(q_med_tax))

    fed_taxable = max(0, box1 - FED_STD[status])
    box2 = r2(bracket_tax(fed_taxable, FED_BRACKETS[status]))
    box16 = box1
    va_taxable = max(0, box16 - VA_STD[status] - VA_EXEMPTION * va_exemptions)
    box17 = r2(bracket_tax(va_taxable, VA_BRACKETS))
    q_fed_wh = split(box2, q_box1)
    q_va_wh = split(box17, q_box1)

    q_ui_tax = capped_quarters(q_fica, VA_SUI_BASE)
    q_futa = capped_quarters(q_fica, FUTA_BASE)

    box12 = []
    if k401: box12.append(("D", k401))
    if er_health: box12.append(("DD", r2(er_health + health125)))
    if hsa: box12.append(("W", hsa))
    if imputed_life: box12.append(("C", imputed_life))
    box12 += [("", None)] * (4 - len(box12))
    box12 = box12[:4]

    mi = random.choice("ABCDEFGHJKLMNPRSTW") if random.random() < 0.6 else ""
    ssn = fake_ssn()
    row = {
        "Control Number (d)": f"{i + 1:05d}",
        "Employee SSN (a)": ssn,
        "Employer EIN (b)": EMPLOYER["ein"],
        "Employer Name (c)": EMPLOYER["name"],
        "Employer Address (c)": EMPLOYER["addr"],
        "Employer City (c)": EMPLOYER["city"],
        "Employer State (c)": EMPLOYER["state"],
        "Employer ZIP (c)": EMPLOYER["zip"],
        "Employee First Name (e)": firsts[i],
        "Employee Middle Initial (e)": mi,
        "Employee Last Name (e)": lasts[i],
        "Employee Suffix (e)": random.choice(SUFFIXES),
        "Employee Address (f)": f"{random.randint(2, 9_899)} {random.choice(STREETS)}"
                                + (f" Apt {random.randint(1, 24)}{random.choice('ABCD')}" if random.random() < .25 else ""),
        "Employee City (f)": city,
        "Employee State (f)": "VA",
        "Employee ZIP (f)": zipc,
        "Box 1 Wages, Tips, Other Comp": box1,
        "Box 2 Federal Income Tax Withheld": box2,
        "Box 3 Social Security Wages": box3,
        "Box 4 Social Security Tax Withheld": box4,
        "Box 5 Medicare Wages and Tips": box5,
        "Box 6 Medicare Tax Withheld": box6,
        "Box 7 Social Security Tips": 0.0,
        "Box 8 Allocated Tips": 0.0,
        "Box 9 (blank)": None,
        "Box 10 Dependent Care Benefits": dep_care,
        "Box 11 Nonqualified Plans": 0.0,
    }
    for j, (code, amt) in enumerate(box12):
        L = "abcd"[j]
        row[f"Box 12{L} Code"] = code or None
        row[f"Box 12{L} Amount"] = amt
    row["Box 13 Statutory Employee"] = "N"
    row["Box 13 Retirement Plan"] = "Y" if k401 else "N"
    row["Box 13 Third-Party Sick Pay"] = "N"
    row["Box 14a Other"] = None                     # Virginia has no SDI/PFL employee deductions
    row["Box 15 State"] = "VA"
    row["Box 15 Employer State ID"] = EMPLOYER["va_id"]
    row["Box 16 State Wages"] = box16
    row["Box 17 State Income Tax"] = box17
    row["Box 18 Local Wages"] = None                # no local income tax in Virginia
    row["Box 19 Local Income Tax"] = None
    row["Box 20 Locality Name"] = None
    rows.append(row)

    ui_gross = r2(sum(q_fica))
    detail.append({
        "Control Number": row["Control Number (d)"],
        "Employee": f"{firsts[i]} {lasts[i]}",
        "Filing Status (W-4)": status,
        "VA-4 Exemptions": va_exemptions,
        "Original Hire Date": orig_hire,
        "Separation Date": term,
        "Full Year?": "Y" if (start == Y0 and end == Y1) else "N",
        "Gross Pay": gross,
        "Overtime Pay (incl. above)": overtime,
        "Bonus (Q4, incl. above)": bonus,
        "Imputed Group-Term Life": imputed_life,
        "401(k) Deferral": k401,
        "Sec 125 Health Premium": health125,
        "Sec 125 HSA": hsa,
        "Sec 125 Dependent Care": dep_care,
        "FUTA Taxable Wages": r2(sum(q_futa)),
        "FUTA Tax (employer)": r2(sum(q_futa) * FUTA_RATE),
        "VA UI Gross Wages": ui_gross,
        "VA UI Excess Wages": r2(ui_gross - sum(q_ui_tax)),
        "VA UI Taxable Wages": r2(sum(q_ui_tax)),
        "VA UI Contribution (employer)": r2(sum(r2(t * VA_SUI_RATE) for t in q_ui_tax)),
    })

    for n, (q, q0, q1) in enumerate(QUARTERS):
        if not qdays[n]:
            continue
        twelfths = [dt.date(YEAR, m, 12) for m in range(q0.month, q0.month + 3)]
        quarterly.append({
            "Control Number": row["Control Number (d)"],
            "Employee SSN": ssn,
            "Employee First Name": firsts[i],
            "Employee Middle Initial": mi,
            "Employee Last Name": lasts[i],
            "Tax Year": YEAR,
            "Quarter": q,
            "Quarter End Date": q1,
            "Employed on 12th - Month 1": "Y" if start <= twelfths[0] <= end else "N",
            "Employed on 12th - Month 2": "Y" if start <= twelfths[1] <= end else "N",
            "Employed on 12th - Month 3": "Y" if start <= twelfths[2] <= end else "N",
            "VA UI Gross Wages": q_fica[n],
            "VA UI Excess Wages": r2(q_fica[n] - q_ui_tax[n]),
            "VA UI Taxable Wages": q_ui_tax[n],
            "VA UI Contribution (employer)": r2(q_ui_tax[n] * VA_SUI_RATE),
            "Federal Wages (Box 1 basis)": q_box1[n],
            "Federal Income Tax Withheld": q_fed_wh[n],
            "Social Security Wages": q_ss[n],
            "Social Security Tax": q_ss_tax[n],
            "Medicare Wages": q_fica[n],
            "Medicare Tax": q_med_tax[n],
            "VA State Wages": q_box1[n],
            "VA Income Tax Withheld": q_va_wh[n],
            "FUTA Taxable Wages": q_futa[n],
        })

# ---------------- self-check: quarters reconcile to the W-2 ----------------
for row in rows:
    qs = [q for q in quarterly if q["Control Number"] == row["Control Number (d)"]]
    for qk, wk in [("Federal Wages (Box 1 basis)", "Box 1 Wages, Tips, Other Comp"),
                   ("Federal Income Tax Withheld", "Box 2 Federal Income Tax Withheld"),
                   ("Social Security Wages", "Box 3 Social Security Wages"),
                   ("Social Security Tax", "Box 4 Social Security Tax Withheld"),
                   ("Medicare Wages", "Box 5 Medicare Wages and Tips"),
                   ("Medicare Tax", "Box 6 Medicare Tax Withheld"),
                   ("VA Income Tax Withheld", "Box 17 State Income Tax")]:
        assert abs(sum(q[qk] for q in qs) - row[wk]) < 0.005, (row["Control Number (d)"], qk)

# ---------------- workbook ----------------
wb = Workbook()
HDR = Font(name="Arial", bold=True, color="FFFFFF")
HFILL = PatternFill("solid", fgColor="1F3864")
BODY = Font(name="Arial", size=10)
MONEY = '#,##0.00;(#,##0.00);"-"'
TEXT_COLS = {"Employee SSN (a)", "Employee SSN", "Employer EIN (b)", "Employer ZIP (c)", "Employee ZIP (f)",
             "Control Number (d)", "Control Number", "Box 15 Employer State ID"}


def write_table(ws, records, money_cols):
    cols = list(records[0].keys())
    for c, name in enumerate(cols, 1):
        cell = ws.cell(row=1, column=c, value=name)
        cell.font, cell.fill = HDR, HFILL
        cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
    for r, rec in enumerate(records, 2):
        for c, name in enumerate(cols, 1):
            cell = ws.cell(row=r, column=c, value=rec[name])
            cell.font = BODY
            if name in money_cols:
                cell.number_format = MONEY
            elif isinstance(rec[name], dt.date):
                cell.number_format = "yyyy-mm-dd"
            elif name in TEXT_COLS:
                cell.number_format = "@"
    for c, name in enumerate(cols, 1):
        width = max(len(str(name)) * 0.55, max(len(str(rec[name] or "")) for rec in records) + 2, 10)
        ws.column_dimensions[get_column_letter(c)].width = min(width, 38)
    ws.row_dimensions[1].height = 45
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions
    return cols


def totals_row(ws, cols, money_cols, nrows):
    tr = nrows + 2
    ws.cell(row=tr, column=1, value="TOTAL").font = Font(name="Arial", bold=True)
    for c, name in enumerate(cols, 1):
        if name in money_cols:
            L = get_column_letter(c)
            cell = ws.cell(row=tr, column=c, value=f"=SUM({L}2:{L}{nrows + 1})")
            cell.font, cell.number_format = Font(name="Arial", bold=True), MONEY
            cell.border = Border(top=Side(style="double"))


ws = wb.active
ws.title = "W-2 Data"
money = {k for k in rows[0] if k.startswith("Box") and ("Amount" in k or any(
    w in k for w in ["Wages", "Tax", "Tips", "Comp", "Benefits", "Plans"])) and "Code" not in k}
cols = write_table(ws, rows, money)
totals_row(ws, cols, money, N)

ws2 = wb.create_sheet("Payroll Detail")
dmoney = {k for k, v in detail[0].items() if isinstance(v, float)}
write_table(ws2, detail, dmoney)

ws5 = wb.create_sheet("Quarterly Wages")
qmoney = {k for k, v in quarterly[0].items() if isinstance(v, float)}
qcols = write_table(ws5, quarterly, qmoney)
totals_row(ws5, qcols, qmoney, len(quarterly))

ws3 = wb.create_sheet("Employer")
emp_rows = [("Employer Name", EMPLOYER["name"]), ("EIN (Box b)", EMPLOYER["ein"]),
            ("Address", EMPLOYER["addr"]), ("City", EMPLOYER["city"]), ("State", EMPLOYER["state"]),
            ("ZIP", EMPLOYER["zip"]), ("VA Withholding Account No. (Box 15)", EMPLOYER["va_id"]),
            ("VEC UI Employer Account No.", EMPLOYER["vec_acct"]),
            ("VA UI Contribution Rate", VA_SUI_RATE), ("VA UI Taxable Wage Base", VA_SUI_BASE),
            ("Kind of Employer", "None apply (private, for-profit)"), ("Employment Code", "R (Regular, Form 941)"),
            ("Tax Year", YEAR), ("Note", "All data is fictional and generated for testing.")]
for r, (k, v) in enumerate(emp_rows, 1):
    ws3.cell(row=r, column=1, value=k).font = Font(name="Arial", bold=True)
    c = ws3.cell(row=r, column=2, value=v)
    c.font = BODY
    if isinstance(v, str):
        c.number_format = "@"
ws3.column_dimensions["A"].width, ws3.column_dimensions["B"].width = 36, 48

ws4 = wb.create_sheet("2025 Rates & Method")
notes = [
    ("Item", "2025 value used", "Source / status"),
    ("Social Security", "6.2% on wages up to $176,100", "SSA 2025 COLA fact sheet (official)"),
    ("Medicare", "1.45% all wages + 0.9% over $200,000", "IRC 3101/3102 (official, not indexed)"),
    ("Federal withholding", "Annualized: Box 1 wages less standard deduction ($15,750 S / $31,500 MFJ / $23,625 HoH), 2025 brackets",
     "Rev. Proc. 2024-40 brackets; standard deduction as raised for 2025 by the One Big Beautiful Bill Act (from $15,000 / $30,000 / $22,500). Approximates the Pub 15-T percentage method with no W-4 Step 2-4 entries"),
    ("FUTA (employer)", "0.6% on first $7,000", "Not a W-2 box; shown on Payroll Detail and Quarterly Wages"),
    ("VA UI (employer)", "2.5% on first $8,000", "ASSUMPTION, not verified from VEC: $8,000 wage base recalled from prior years; 2.5% is an assumed new-employer rate. Not a W-2 box"),
    ("VA withholding", "Box 16 wages less standard deduction ($8,750 S and HoH / $17,500 MFJ) less $930 per VA-4 exemption; 2% / 3% / 5% / 5.75% brackets (breaks at $3k / $5k / $17k)",
     "Brackets and $930 exemption are statutory (Va. Code 58.1-320). Standard deduction amounts are an ASSUMPTION (recalled 2025 amounts, not verified). Approximates the Virginia Tax withholding formula"),
    ("Local tax", "None", "Virginia has no local income tax, so Boxes 18-20 are blank"),
    ("Box 14", "Blank", "Virginia has no employee SDI/PFL deductions"),
    ("Box 15 state ID", "30-543187620F-001", "Fictional; follows the VA Tax withholding account pattern (30 + FEIN + F001)"),
    ("Box 12 codes", "D 401(k), DD employer health cost, W HSA, C group-term life >$50k", "The 2025 W-2 has no code TT (qualified overtime) or Box 14b; both start with the 2026 form"),
    ("Box 16 VA wages", "Equal to Box 1", "No Virginia-specific modifications assumed"),
    ("Quarterly split", "Base pay and overtime spread by days employed in each quarter; bonuses paid in Q4; deductions and withholding split in proportion to quarterly wages",
     "SS, Medicare, VA UI and FUTA wage bases applied cumulatively, so taxable wages fall in the earliest quarters. Quarterly columns sum exactly to the W-2 boxes (checked by the generator)"),
    ("Employment flags", "Employed on the 12th of each month, from hire and separation dates", "Used for the monthly employment counts on UI wage reports"),
    ("Taxes", "Computed as annual amounts, not summed per-paycheck", "So amounts will not reconcile to a real payroll register to the penny"),
]
for r, rec in enumerate(notes, 1):
    for c, v in enumerate(rec, 1):
        cell = ws4.cell(row=r, column=c, value=v)
        cell.font = HDR if r == 1 else BODY
        if r == 1: cell.fill = HFILL
        cell.alignment = Alignment(wrap_text=True, vertical="top")
for L, w in zip("ABC", (22, 60, 70)):
    ws4.column_dimensions[L].width = w

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "w2_2025_va_synthetic.xlsx")
os.makedirs(os.path.dirname(out), exist_ok=True)
wb.save(out)
print("saved", out, len(cols), "W-2 columns,", len(quarterly), "quarterly rows")
print("SS over base:", sum(1 for r in rows if r["Box 5 Medicare Wages and Tips"] > SS_BASE))
print("Partial year:", sum(1 for d in detail if d["Full Year?"] == "N"),
      "Separated:", sum(1 for d in detail if d["Separation Date"]))
for q in range(1, 5):
    qs = [x for x in quarterly if x["Quarter"] == q]
    print(f"Q{q}: {len(qs)} employees, UI gross {sum(x['VA UI Gross Wages'] for x in qs):,.2f}, "
          f"UI taxable {sum(x['VA UI Taxable Wages'] for x in qs):,.2f}, VA WH {sum(x['VA Income Tax Withheld'] for x in qs):,.2f}")
