"""Source of taxfiling/layouts/efw2_ty2026.json.

The JSON is what the program reads; this script is where each field and its
source were typed in. It checks that every record is contiguous and 512 long.

Run: python tools/build_efw2_ty2026_layout.py taxfiling/layouts/efw2_ty2026.json
"""
import json, sys

SRC_SSA26 = "ssa-ty2026"       # SSA EFW2 TY2026 PDF text (web fetch)
SRC_VA = "va-web-upload"       # Virginia Tax Web Upload W-2/1099 guide, Appendix A
SRC_RITA = "rita-2025"         # RITA (Ohio) 2025 W-2 spec, reproduces SSA RS
SRC_HI = "hi-ef10-2025"        # Hawaii Pub EF-10 (Oct 2025), RS positions
SRC_RECALL = "recall"          # prior-year SSA layout from memory, NOT verified
SRC_SSA11 = "ssa-ty2011"       # SSA EFW2 TY2011 PDF text (web fetch): RT positions 1-399
SRC_NJ = "nj-2014"             # NJ EFW2 spec 2014: reproduces SSA RS 1-194 and blank areas
SRC_NC = "nc-suits"            # NC DES SUITS EFW2 interface: RF positions 1-16 match

def F(start, end, key, name, typ, req="N", src=SRC_SSA26, notes="", **extra):
    d = {"key": key, "name": name, "start": start, "end": end, "type": typ, "required": req}
    if notes: d["notes"] = notes
    d.update(extra)
    d["sources"] = src if isinstance(src, list) else [src]
    return d

def money(start, key, name, src=SRC_SSA26, width=11, **kw):
    return F(start, start + width - 1, key, name, "M", "Y", src, **kw)

RA = [
    F(1, 2, "record_id", "Record identifier", "const", "Y", value="RA"),
    F(3, 11, "submitter_ein", "Submitter EIN", "N", "Y"),
    F(12, 19, "user_id", "BSO User ID", "A", "Y"),
    F(20, 23, "software_vendor_code", "Software vendor code", "A", "N", notes="Only if software code is 99"),
    F(24, 28, "blank_24", "Blank", "blank"),
    F(29, 29, "resub_indicator", "Resub indicator", "N", "Y", notes="1 resubmission, else 0"),
    F(30, 35, "resub_wfid", "Resub WFID", "A"),
    F(36, 37, "software_code", "Software code", "N", "Y", notes="98 in-house, 99 off-the-shelf"),
    F(38, 94, "company_name", "Company name", "A", "Y"),
    F(95, 116, "company_location_address", "Company location address", "A"),
    F(117, 138, "company_delivery_address", "Company delivery address", "A", "Y"),
    F(139, 160, "company_city", "Company city", "A", "Y"),
    F(161, 162, "company_state", "Company state abbreviation", "A", "Y"),
    F(163, 167, "company_zip", "Company ZIP code", "A", "Y"),
    F(168, 171, "company_zip_ext", "Company ZIP code extension", "A"),
    F(172, 176, "blank_172", "Blank", "blank"),
    F(177, 199, "company_foreign_state", "Company foreign state/province", "A"),
    F(200, 214, "company_foreign_postal", "Company foreign postal code", "A"),
    F(215, 216, "company_country", "Company country code", "A"),
    F(217, 273, "submitter_name", "Submitter name", "A", "Y"),
    F(274, 295, "submitter_location_address", "Submitter location address", "A"),
    F(296, 317, "submitter_delivery_address", "Submitter delivery address", "A", "Y"),
    F(318, 339, "submitter_city", "Submitter city", "A", "Y"),
    F(340, 341, "submitter_state", "Submitter state abbreviation", "A", "Y"),
    F(342, 346, "submitter_zip", "Submitter ZIP code", "A", "Y"),
    F(347, 350, "submitter_zip_ext", "Submitter ZIP code extension", "A"),
    F(351, 355, "blank_351", "Blank", "blank"),
    F(356, 378, "submitter_foreign_state", "Submitter foreign state/province", "A"),
    F(379, 393, "submitter_foreign_postal", "Submitter foreign postal code", "A"),
    F(394, 395, "submitter_country", "Submitter country code", "A"),
    F(396, 422, "contact_name", "Contact name", "A", "Y"),
    F(423, 437, "contact_phone", "Contact phone number", "A", "Y"),
    F(438, 442, "contact_phone_ext", "Contact phone extension", "A"),
    F(443, 445, "blank_443", "Blank", "blank"),
    F(446, 485, "contact_email", "Contact e-mail", "A", "Y", case="mixed"),
    F(486, 488, "blank_486", "Blank", "blank"),
    F(489, 498, "contact_fax", "Contact fax", "A"),
    F(499, 499, "blank_499", "Blank", "blank"),
    F(500, 500, "preparer_code", "Preparer code", "A", "Y", notes="A, L, S, P or O"),
    F(501, 512, "blank_501", "Blank", "blank"),
]

RE = [
    F(1, 2, "record_id", "Record identifier", "const", "Y", value="RE"),
    F(3, 6, "tax_year", "Tax year", "N", "Y", src=[SRC_SSA26, SRC_VA]),
    F(7, 7, "agent_indicator", "Agent indicator code", "A"),
    F(8, 16, "employer_ein", "Employer/agent EIN", "N", "Y"),
    F(17, 25, "agent_for_ein", "Agent for EIN", "A"),
    F(26, 26, "terminating_business", "Terminating business indicator", "N", "Y"),
    F(27, 30, "establishment_number", "Establishment number", "A"),
    F(31, 39, "other_ein", "Other EIN", "A"),
    F(40, 96, "employer_name", "Employer name", "A", "Y"),
    F(97, 118, "employer_location_address", "Location address", "A"),
    F(119, 140, "employer_delivery_address", "Delivery address", "A", "Y"),
    F(141, 162, "employer_city", "City", "A", "Y"),
    F(163, 164, "employer_state", "State abbreviation", "A", "Y"),
    F(165, 169, "employer_zip", "ZIP code", "A", "Y"),
    F(170, 173, "employer_zip_ext", "ZIP code extension", "A"),
    F(174, 174, "kind_of_employer", "Kind of employer", "A", "Y", notes="F, S, T, Y or N"),
    F(175, 178, "blank_175", "Blank", "blank"),
    F(179, 201, "employer_foreign_state", "Foreign state/province", "A"),
    F(202, 216, "employer_foreign_postal", "Foreign postal code", "A"),
    F(217, 218, "employer_country", "Country code", "A"),
    F(219, 219, "employment_code", "Employment code", "A", "Y", notes="A, H, M, Q, X, F or R"),
    F(220, 220, "tax_jurisdiction", "Tax jurisdiction code", "A", notes="Blank for W-2"),
    F(221, 221, "third_party_sick_pay", "Third-party sick pay indicator", "N", "Y"),
    F(222, 248, "employer_contact_name", "Employer contact name", "A", "Y"),
    F(249, 263, "employer_contact_phone", "Employer contact phone", "A", "Y"),
    F(264, 268, "employer_contact_phone_ext", "Employer contact phone extension", "A"),
    F(269, 278, "employer_contact_fax", "Employer contact fax", "A"),
    F(279, 318, "employer_contact_email", "Employer contact e-mail", "A", "Y", case="mixed"),
    F(319, 328, "employer_signature_pin", "Employer signature PIN", "A", notes="New in TY2026"),
    F(329, 512, "blank_329", "Blank", "blank"),
]

RW = [
    F(1, 2, "record_id", "Record identifier", "const", "Y", value="RW"),
    F(3, 11, "ssn", "Social Security number", "N", "Y"),
    F(12, 26, "first_name", "Employee first name", "A", "Y"),
    F(27, 41, "middle_name", "Employee middle name or initial", "A"),
    F(42, 61, "last_name", "Employee last name", "A", "Y"),
    F(62, 65, "suffix", "Suffix", "A"),
    F(66, 87, "location_address", "Location address", "A"),
    F(88, 109, "delivery_address", "Delivery address", "A", "Y"),
    F(110, 131, "city", "City", "A", "Y"),
    F(132, 133, "state", "State abbreviation", "A", "Y"),
    F(134, 138, "zip", "ZIP code", "A", "Y"),
    F(139, 142, "zip_ext", "ZIP code extension", "A"),
    F(143, 147, "blank_143", "Blank", "blank"),
    F(148, 170, "foreign_state", "Foreign state/province", "A"),
    F(171, 185, "foreign_postal", "Foreign postal code", "A"),
    F(186, 187, "country", "Country code", "A"),
    money(188, "box1_wages", "Wages, tips and other compensation (Box 1)"),
    money(199, "box2_fed_tax", "Federal income tax withheld (Box 2)"),
    money(210, "box3_ss_wages", "Social Security wages (Box 3)"),
    money(221, "box4_ss_tax", "Social Security tax withheld (Box 4)"),
    money(232, "box5_medicare_wages", "Medicare wages and tips (Box 5)"),
    money(243, "box6_medicare_tax", "Medicare tax withheld (Box 6)"),
    money(254, "box7_ss_tips", "Social Security tips (Box 7)"),
    F(265, 275, "blank_265", "Blank", "blank"),
    money(276, "box10_dependent_care", "Dependent care benefits (Box 10)"),
    money(287, "code_D", "401(k) deferrals (Box 12 D)"),
    money(298, "code_E", "403(b) deferrals (Box 12 E)"),
    money(309, "code_F", "408(k)(6) deferrals (Box 12 F)"),
    money(320, "code_G", "457(b) deferrals (Box 12 G)"),
    money(331, "code_H", "501(c)(18)(D) deferrals (Box 12 H)"),
    F(342, 352, "blank_342", "Blank", "blank"),
    money(353, "box11_nonqual_457", "Nonqualified plan section 457 (Box 11)"),
    money(364, "code_W", "Employer contributions to HSA (Box 12 W)"),
    money(375, "box11_nonqual_not_457", "Nonqualified plan not section 457 (Box 11)"),
    money(386, "code_Q", "Nontaxable combat pay (Box 12 Q)"),
    F(397, 407, "blank_397", "Blank", "blank"),
    money(408, "code_C", "Group-term life over $50,000 (Box 12 C)"),
    money(419, "code_V", "Nonstatutory stock options (Box 12 V)"),
    money(430, "code_Y", "409A deferrals (Box 12 Y)"),
    money(441, "code_AA", "Roth 401(k) (Box 12 AA)"),
    money(452, "code_BB", "Roth 403(b) (Box 12 BB)"),
    money(463, "code_DD", "Cost of employer-sponsored health coverage (Box 12 DD)"),
    money(474, "code_FF", "QSEHRA benefits (Box 12 FF)"),
    F(485, 485, "blank_485", "Blank", "blank"),
    F(486, 486, "statutory_employee", "Statutory employee indicator (Box 13)", "N", "Y"),
    F(487, 487, "blank_487", "Blank", "blank"),
    F(488, 488, "retirement_plan", "Retirement plan indicator (Box 13)", "N", "Y"),
    F(489, 489, "third_party_sick_pay", "Third-party sick pay indicator (Box 13)", "N", "Y"),
    F(490, 512, "tipped_occupation_unverified", "Treasury tipped occupation codes + blank (Box 14b)", "blank",
      src=[SRC_SSA26], verified=False,
      notes="TY2026 puts Primary and Secondary Treasury Tipped Occupation Code here, but the fetched "
            "spec text cut off before their positions. Written as blanks; the writer refuses rows with Box 14b."),
]

# RS: SSA optional state record; Virginia requires it (state code 51).
RS = [
    F(1, 2, "record_id", "Record identifier", "const", "Y", value="RS", src=[SRC_VA, SRC_RITA]),
    F(3, 4, "state_code", "State code (FIPS)", "N", "Y", src=[SRC_VA, SRC_RITA, SRC_HI], notes="Virginia = 51"),
    F(5, 9, "taxing_entity_code", "Taxing entity code", "A", src=[SRC_VA, SRC_RITA]),
    F(10, 18, "ssn", "Social Security number", "N", "Y", src=[SRC_VA, SRC_RITA, SRC_HI]),
    F(19, 33, "first_name", "Employee first name", "A", "Y", src=[SRC_VA, SRC_RITA, SRC_HI]),
    F(34, 48, "middle_name", "Employee middle name or initial", "A", src=[SRC_VA, SRC_RITA]),
    F(49, 68, "last_name", "Employee last name", "A", "Y", src=[SRC_VA, SRC_RITA, SRC_HI]),
    F(69, 72, "suffix", "Suffix", "A", src=[SRC_VA, SRC_RITA]),
    F(73, 94, "location_address", "Location address", "A", src=[SRC_VA, SRC_RITA, SRC_HI]),
    F(95, 116, "delivery_address", "Delivery address", "A", "Y", src=[SRC_VA, SRC_RITA, SRC_HI]),
    F(117, 138, "city", "City", "A", "Y", src=[SRC_RITA, SRC_HI]),
    F(139, 140, "state", "State abbreviation", "A", "Y", src=[SRC_RITA]),
    F(141, 145, "zip", "ZIP code", "A", "Y", src=[SRC_RITA]),
    F(146, 149, "zip_ext", "ZIP code extension", "A", src=[SRC_RITA]),
    F(150, 154, "blank_150", "Blank", "blank", src=[SRC_NJ]),
    F(155, 177, "foreign_state", "Foreign state/province", "A", src=[SRC_NJ]),
    F(178, 192, "foreign_postal", "Foreign postal code", "A", src=[SRC_NJ]),
    F(193, 194, "country", "Country code", "A", src=[SRC_NJ]),
    F(195, 196, "optional_code", "Optional code (state UI use)", "A", src=[SRC_RECALL]),
    F(197, 202, "reporting_period", "Reporting period (MMCCYY)", "A", src=[SRC_RECALL], notes="UI reporting only"),
    F(203, 213, "ui_total_wages", "State quarterly UI total wages", "M", src=[SRC_HI], empty="blank"),
    F(214, 224, "ui_taxable_wages", "State quarterly UI taxable wages", "M", src=[SRC_HI], empty="blank"),
    F(225, 226, "weeks_worked", "Number of weeks worked", "N", src=[SRC_HI], empty="blank"),
    F(227, 234, "date_first_employed", "Date first employed (MMDDCCYY)", "N", src=[SRC_HI], empty="blank"),
    F(235, 242, "date_of_separation", "Date of separation (MMDDCCYY)", "N", src=[SRC_HI], empty="blank"),
    F(243, 247, "blank_243", "Blank", "blank", src=[SRC_NJ]),
    F(248, 267, "state_employer_account", "State employer account number", "A", "Y", src=[SRC_VA, SRC_HI],
      notes="Virginia withholding account number, no hyphens"),
    F(268, 273, "blank_268", "Blank", "blank", src=[SRC_NJ]),
    F(274, 275, "state_code_2", "State code (FIPS)", "N", "Y", src=[SRC_VA, SRC_HI]),
    money(276, "state_wages", "State taxable wages (Box 16)", src=[SRC_VA, SRC_RITA, SRC_HI]),
    money(287, "state_tax", "State income tax withheld (Box 17)", src=[SRC_VA, SRC_RITA, SRC_HI]),
    F(298, 307, "other_state_data", "Other state data", "A", src=[SRC_RECALL]),
    F(308, 308, "tax_type_code", "Tax type code", "A", src=[SRC_RITA], notes="Local tax only"),
    F(309, 319, "local_wages", "Local taxable wages (Box 18)", "M", src=[SRC_RITA], empty="blank"),
    F(320, 330, "local_tax", "Local income tax withheld (Box 19)", "M", src=[SRC_RITA], empty="blank"),
    F(331, 337, "state_control_number", "State control number", "A", src=[SRC_RECALL]),
    F(338, 412, "supplemental_1", "Supplemental data 1", "A", src=[SRC_RITA]),
    F(413, 487, "supplemental_2", "Supplemental data 2", "A", src=[SRC_RITA]),
    F(488, 512, "blank_488", "Blank", "blank", src=[SRC_NJ]),
]

def total(start, key, name, sum_of, src=SRC_SSA11, **kw):
    return F(start, start + 14, key, name, "M", "Y", src, sum_of=sum_of, **kw)

RT = [
    F(1, 2, "record_id", "Record identifier", "const", "Y", value="RT", src=SRC_SSA11),
    F(3, 9, "rw_count", "Number of RW records", "N", "Y", src=SRC_SSA11, count_of="RW"),
    total(10, "total_box1_wages", "Total wages, tips and other compensation", "box1_wages"),
    total(25, "total_box2_fed_tax", "Total federal income tax withheld", "box2_fed_tax"),
    total(40, "total_box3_ss_wages", "Total Social Security wages", "box3_ss_wages"),
    total(55, "total_box4_ss_tax", "Total Social Security tax withheld", "box4_ss_tax"),
    total(70, "total_box5_medicare_wages", "Total Medicare wages and tips", "box5_medicare_wages"),
    total(85, "total_box6_medicare_tax", "Total Medicare tax withheld", "box6_medicare_tax"),
    total(100, "total_box7_ss_tips", "Total Social Security tips", "box7_ss_tips"),
    F(115, 129, "blank_115", "Blank", "blank", src=[SRC_SSA11, SRC_RECALL],
      notes="Advance EIC total in TY2011; blank since that credit ended (recalled)"),
    total(130, "total_box10_dependent_care", "Total dependent care benefits", "box10_dependent_care"),
    total(145, "total_code_D", "Total 401(k) deferrals", "code_D"),
    total(160, "total_code_E", "Total 403(b) deferrals", "code_E"),
    total(175, "total_code_F", "Total 408(k)(6) deferrals", "code_F"),
    total(190, "total_code_G", "Total 457(b) deferrals", "code_G"),
    total(205, "total_code_H", "Total 501(c)(18)(D) deferrals", "code_H"),
    F(220, 234, "blank_220", "Blank", "blank", src=[SRC_SSA11, SRC_RECALL],
      notes="Military quarters/subsistence total in TY2011; blank in later years (recalled)"),
    total(235, "total_box11_nonqual_457", "Total nonqualified plan section 457", "box11_nonqual_457"),
    total(250, "total_code_W", "Total employer contributions to HSA", "code_W"),
    total(265, "total_box11_nonqual_not_457", "Total nonqualified plan not section 457", "box11_nonqual_not_457"),
    total(280, "total_code_Q", "Total nontaxable combat pay", "code_Q"),
    total(295, "total_code_DD", "Total cost of employer-sponsored health coverage", "code_DD"),
    total(310, "total_code_C", "Total group-term life over $50,000", "code_C"),
    total(325, "total_third_party_withheld", "Total income tax withheld by third-party payer", None),
    total(340, "total_code_V", "Total nonstatutory stock options", "code_V"),
    total(355, "total_code_Y", "Total 409A deferrals", "code_Y"),
    total(370, "total_code_AA", "Total Roth 401(k)", "code_AA"),
    total(385, "total_code_BB", "Total Roth 403(b)", "code_BB"),
    total(400, "total_code_FF", "Total QSEHRA benefits", "code_FF", src=SRC_RECALL,
          notes="Added after TY2011 (QSEHRA began 2017); position recalled, not verified"),
    F(415, 512, "blank_415", "Blank", "blank", src=SRC_RECALL),
]

RF = [
    F(1, 2, "record_id", "Record identifier", "const", "Y", value="RF", src=[SRC_RECALL, SRC_NC]),
    F(3, 7, "blank_3", "Blank", "blank", src=[SRC_RECALL, SRC_NC]),
    F(8, 16, "rw_count", "Number of RW records", "N", "Y", src=[SRC_RECALL, SRC_NC], count_of="RW"),
    F(17, 512, "blank_17", "Blank", "blank", src=SRC_RECALL),
]

layout = {
    "format": "EFW2",
    "title": "SSA EFW2, tax year 2026 (W-2 wage file)",
    "tax_year": 2026,
    "record_length": 512,
    "line_ending": "\r\n",
    "sources": {
        SRC_SSA26: {"title": "SSA Pub. 42-007, EFW2 Tax Year 2026, Version 2 (9/14/2026)",
                    "url": "https://www.ssa.gov/employer/efw/26efw2.pdf",
                    "how": "Transcribed from web-fetched PDF text (this environment cannot download ssa.gov files). RA and RE positions matched across two separate fetches on 2026-10-05 and 2026-10-17; RW positions 143-489 matched across two fetches. The fetched text stops after RW position 489, so later records come from the other sources listed."},
        SRC_VA: {"title": "Virginia Tax, Web Upload Guide for W-2 and 1099 files, Appendix A (RS layout)",
                 "url": "https://tax.am.virginia.gov/sites/default/files/inline-files/web-upload-w2-1099-guide.pdf",
                 "how": "Web-fetched. Virginia reads only RE (tax year) and RS; other EFW2 records are accepted but not validated."},
        SRC_RITA: {"title": "Regional Income Tax Agency (Ohio), 2025 W-2 specification (reproduces SSA RS)",
                   "url": "https://www.ritaohio.com/Media/703065/2025%20W-2%20SPEC.pdf", "how": "Web-fetched."},
        SRC_HI: {"title": "Hawaii Dept. of Taxation Pub. EF-10 (Oct 2025), RS positions",
                 "url": "https://files.hawaii.gov/tax/eservices/ebiz/25pubef10_EFW2_Specs_Oct2025.pdf", "how": "Web-fetched."},
        SRC_SSA11: {"title": "SSA EFW2 Tax Year 2011 (Pub. 42-007)",
                     "url": "https://www.ssa.gov/employer/efw/11efw2.pdf",
                     "how": "Web-fetched; RT positions 1-399 read from it. RT totals have tracked RW money fields since, and SSA's TY2026 change list names RE, RW, RO and RU only."},
        SRC_NJ: {"title": "New Jersey EFW2 specification (2014), RS layout",
                 "url": "https://www.nj.gov/www.nj.gov/treasury/taxation/pdf/other_forms/git-er/2014/nj_efw2.pdf",
                 "how": "Web-fetched; confirms the SSA RS name/address block and blank areas."},
        SRC_NC: {"title": "NC DES SUITS Employer EFW2 interface file definition",
                 "url": "https://www.des.nc.gov/documents/files/employer-efw2-interface-file-definition/open",
                 "how": "Web-fetched; a UI variant of EFW2 whose RF positions 1-16 match SSA's."},
        SRC_RECALL: {"title": "Prior-year SSA EFW2 layout, recalled",
                     "url": "", "how": "NOT VERIFIED against a published TY2026 spec. Check before production use."},
    },
    "records": {
        "RA": {"name": "Submitter record", "fields": RA},
        "RE": {"name": "Employer record", "fields": RE},
        "RW": {"name": "Employee wage record", "fields": RW},
        "RS": {"name": "State wage record", "fields": RS},
        "RT": {"name": "Total record (sums of RW)", "fields": RT},
        "RF": {"name": "Final record", "fields": RF},
    },
}

# contiguity check
ok = True
for rt, rec in layout["records"].items():
    pos = 1
    for f in rec["fields"]:
        if f["start"] != pos:
            print(rt, f["key"], "starts", f["start"], "expected", pos); ok = False
        pos = f["end"] + 1
    if pos - 1 != 512:
        print(rt, "ends at", pos - 1); ok = False
    print(rt, len(rec["fields"]), "fields, ends", pos - 1)
if not ok:
    sys.exit(1)
out = sys.argv[1]
with open(out, "w") as fh:
    fh.write("{\n")
    top = [k for k in layout if k != "records"]
    for k in top:
        fh.write(f'  {json.dumps(k)}: {json.dumps(layout[k], indent=None) if k != "sources" else json.dumps(layout[k], indent=2).replace(chr(10), chr(10) + "  ")},\n')
    fh.write('  "records": {\n')
    recs = list(layout["records"].items())
    for i, (rt, rec) in enumerate(recs):
        fh.write(f'    {json.dumps(rt)}: {{\n      "name": {json.dumps(rec["name"])},\n      "fields": [\n')
        fs = rec["fields"]
        for j, f in enumerate(fs):
            fh.write("        " + json.dumps(f) + ("," if j < len(fs) - 1 else "") + "\n")
        fh.write("      ]\n    }" + ("," if i < len(recs) - 1 else "") + "\n")
    fh.write("  }\n}\n")
json.load(open(out))
print("wrote", out)
