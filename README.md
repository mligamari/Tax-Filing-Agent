# Tax-Filing-Agent

Turns a spreadsheet of employee W-2 data into the fixed-width text files that state
wage-reporting portals accept (EFW2 today, ICESA next), then reads the file back to
prove it says exactly what the spreadsheet says.

It runs entirely on your own computer. Payroll files are full of Social Security
numbers, and many employers' privacy agreements forbid uploading them to a cloud AI
service. So the part that touches SSNs is plain Python with no network access, and
the AI layer planned for later will only ever see column headers.

> All data in this repository is synthetic. The SSNs are random numbers attached to
> made-up people at a made-up company.

## What works today

**Virginia W-2 annual file** (Virginia Tax, Web Upload, EFW2 format) for tax year 2025,
the default, and tax year 2026:

```text
$ python -m taxfiling w2 --state VA \
      --input examples/va_2025/w2_2025_va_synthetic.xlsx \
      --submitter examples/va_2025/submitter.json

Read 50 employees for Blue Ridge Precision Components Inc from examples/va_2025/w2_2025_va_synthetic.xlsx
Input notes: 0 error(s), 2 warning(s)
  WARNING: row 39, control 00038 (Jamal Sullivan): RW Delivery Address: '5361 Jefferson Davis Hwy' is longer than 22 characters; written as '5361 JEFFERSON DAVIS H'
  WARNING: row 50, control 00049 (Marcus Smith): RW Delivery Address: '7038 Jefferson Davis Hwy' is longer than 22 characters; written as '7038 JEFFERSON DAVIS H'
Wrote W2 REPORT 2025.txt (54,482 bytes) for Virginia (Virginia Department of Taxation (Virginia Tax), Web Upload, W-2 text file)
Round-trip check: PASSED
  Records: RA 1, RE 1, RW 50, RO 1, RS 50, RT 1, RU 1, RF 1
  Compared with source: 50 employees, 1671 fields
  Box 1 wages               3,402,766.49
  Box 2 federal tax           314,718.98
  Box 3 SS wages            3,339,730.48
  Box 5 Medicare wages      3,588,144.72
  State wages (RS)          3,402,766.49
  State tax (RS)              140,869.55
  0 error(s), 4 warning(s)
    WARNING: row 39, control 00038 (Jamal Sullivan): RW street address shortened to fit: '5361 JEFFERSON DAVIS HWY' written as '5361 JEFFERSON DAVIS H'
    ...
```

The checker finds the two shortened streets on its own, by comparing the file with the
spreadsheet; it does not take the writer's word for anything.

The written file is committed at
[`examples/va_2025/output/W2 REPORT 2025.txt`](examples/va_2025/output/W2%20REPORT%202025.txt).
To read it field by field (SSNs masked):

```text
$ python -m taxfiling show "examples/va_2025/output/W2 REPORT 2025.txt" --record RS --limit 1
--- line 4: RS (512 characters)
      3-4   State Code                                              51
     10-18  Social Security Number (SSN)                            ***-**-5232
     19-33  Employee First Name                                     LAUREN
     34-48  Employee Middle Name or Initial                         R
     49-68  Employee Last Name                                      RAMIREZ
     95-116 Delivery Address                                        9142 MONUMENT AVE
    117-138 City                                                    COLONIAL HEIGHTS
    139-140 State Abbreviation                                      VA
    141-145 ZIP Code                                                23834
    248-267 State Employer Account Number                           30543187620F001
    274-275 State Code                                              51
    276-286 State Taxable Wages                                     82,297.12
    287-297 State Income Tax Withheld                               3,361.38

$ python -m taxfiling show "examples/va_2025/output/W2 REPORT 2025.txt" --record RO
--- line 26: RO (512 characters)
     56-66  Qualified Adoption Expenses (Code T)                    9,850.00
```

The file is a complete SSA EFW2 file, not just the records Virginia reads. RO and RU
records are written only when an employee has an amount that belongs in them, as SSA
requires: in this example, one employee's adoption benefits (Box 12 code T).

For 2026, add `--year 2026` and use [`examples/va_2026/`](examples/va_2026/). Six of its
employees have qualified overtime (Box 12 code TT, new on the 2026 Form W-2), so that
file has six RO records. The new Box 14b tipped-occupation codes go in RW from 2026; a
2025 file has no place for them, so they are reported instead.

## Install and run

Requires Python 3.10 or newer.

```bash
git clone https://github.com/mligamari/Tax-Filing-Agent.git
cd Tax-Filing-Agent
python -m pip install -e ".[test]"

python -m taxfiling w2 --state VA --input examples/va_2025/w2_2025_va_synthetic.xlsx \
    --submitter examples/va_2025/submitter.json --out "W2 REPORT 2025.txt"
python -m taxfiling check "W2 REPORT 2025.txt" --state VA \
    --input examples/va_2025/w2_2025_va_synthetic.xlsx
python -m taxfiling layout --record RO       # a layout table, with the spec page of each field
python -m pytest
```

| Command | What it does |
|---|---|
| `w2` | Reads the workbook, validates it, builds the state file and runs the round-trip check on it. The file is saved only if there are no errors and the check passes. |
| `check` | Checks any file against the layout and state rules. With `--input` it also compares every employee and amount with the source. |
| `show` | Prints a file field by field with positions. SSNs are masked unless `--show-ssn` is given. |
| `layout` | Lists a layout table, with the page of SSA's spec that defines each field. |

`check` and `show` read the tax year from the file and pick the matching layout. `w2`
uses the state's default year (2025 for Virginia) unless `--year` is given.

## How it works

```text
 SSA spec text ──► layout builder ──► layout table (JSON) ─────────┐
 (specs/ssa/)      (tools/)           (taxfiling/layouts/)         │
                                                                   ▼
 W-2 workbook ──► column mapping ──► W-2 model ──► validation ──► EFW2 builder ──► text file
 (.xlsx)          (header → box)     (Decimal      (SSN, EIN,     (which value        │
                                      amounts)      SSA rules)     goes where)        │
                                                                                      │
             round-trip checker ◄── file parsed back with the layout table alone ◄────┘
             (record order, every field, RT/RU/RF totals, state rules,
              every employee and amount compared with the source)
```

* **Layouts are data, not code.** Every record's field positions, types and padding
  live in [`taxfiling/layouts/efw2_ty2025.json`](taxfiling/layouts/efw2_ty2025.json)
  (and `efw2_ty2026.json`), generated from SSA's own spec text as described below. A
  new tax year or a new format (ICESA) is a new table; the encoder in
  [`taxfiling/layout.py`](taxfiling/layout.py) knows nothing about EFW2.
* **States are data too.** [`taxfiling/states/va.json`](taxfiling/states/va.json)
  holds Virginia's rules: state code 51, the account number format, one RS record per
  employee, the fields Virginia requires, CR/LF between records but not after the last
  one, and the file name the state recommends. Employees with no Virginia wages are
  left out of the Virginia file, with a note naming each one.
* **The checker is independent of the writer.** It parses the file with the layout
  table alone and works out what each field should hold from the source data on its
  own (RW field `code_DD` must equal the Box 12 DD amount; RT and RU totals must equal
  the sums of the RW and RO records). A mapping mistake in the writer shows up as a
  mismatch instead of being repeated. The tests damage a good file in more than twenty
  ways and confirm each one is caught.
* **SSA's reject rules are checked before writing.** Social Security wages over the
  year's wage base ($176,100 for 2025, $184,500 for 2026), Social Security tax over its
  maximum, Medicare wages below Social Security wages plus tips, and tax withheld with
  no matching wages are errors, and no file is written.
* **Nothing is silently dropped or cut.** A name too long for its field is an error.
  An apartment number that does not fit moves to the EFW2 location-address field, and
  a street that still does not fit is shortened with a warning that shows both
  versions. A Box 12 code that SSA's file has no field for (codes J, K, L and P in
  2025) is reported for each employee who has one, and a code that is not on that
  year's Form W-2 (TT in a 2025 file) stops the file.
* **Excel damage is caught.** SSNs and ZIP codes stored as numbers lose their leading
  zeros; the reader restores them and says so.

## Where the layouts come from

Every field position comes from SSA's own specification, Publication 42-007 (EFW2), for
[tax year 2025](https://www.ssa.gov/employer/efw/25efw2.pdf) (Version 3.3, 01/20/2026)
and [tax year 2026](https://www.ssa.gov/employer/efw/26efw2.pdf) (Version 2, 9/14/2026).
The text of both PDFs is committed in [`specs/ssa/`](specs/ssa/) with its SHA-256, and
the layout tables are generated from it rather than typed in:

1. [`tools/efw2_spec_text.py`](tools/efw2_spec_text.py) reads each record's field table
   from the text: positions, lengths, names, page numbers and each field's description.
   It also reads the chart in Appendix E that says where each Box 12 code goes.
2. [`tools/build_efw2_layout.py`](tools/build_efw2_layout.py) gives every field the key
   the program uses for it, and refuses to write the layout if
   * a record has a gap, or does not end at position 512;
   * a record's field table disagrees with the summary table SSA prints above it;
   * a spec field has no key, or a key matches no spec field;
   * the spec describes a field differently from how the program treats it (blank,
     constant, text or numeric);
   * a Box 12 key names different codes from the spec's field name (`code_DD` must be
     the field named "... (Code DD)");
   * an RT or RU total is not named "Total" plus the name of the field it adds up;
   * Appendix E puts a Box 12 code anywhere other than where the layout does.
3. [`tests/test_layout_matches_spec.py`](tests/test_layout_matches_spec.py) repeats these
   comparisons on every test run, and CI rebuilds both tables from the spec text and
   fails if they differ from the committed JSON.

`python -m taxfiling layout` prints each field with the spec page that defines it, and
`python -m taxfiling layout --unverified` lists the fields not confirmed against a
published spec. There are none.

Virginia's rules come from Virginia Tax's
[Web Upload Guide for W-2s, W-2Cs, and 1099s](https://tax.am.virginia.gov/sites/default/files/inline-files/web-upload-w2-1099-guide.pdf)
(revised 1/2025), Appendix A. Virginia reads the RE record and the RS records, and
accepts the other records without checking them.

## Privacy and security

* **No network access.** The program never opens a connection. A test runs the whole
  write, check and show cycle with Python's socket functions replaced by ones that
  fail, so any network call would fail the test.
* **SSNs stay out of everything printed.** Console output, warnings and error
  messages mask SSNs (`***-**-5232`). The same test checks that none of the 50 SSNs
  appears anywhere in the console output. Only the output file holds full SSNs,
  because the state requires them.
* **What is checked is what is saved.** The round-trip check runs on the exact bytes
  before they are written, so a file that fails its check never sits on disk under
  the name the state expects.
* **Real data stays out of git.** `.gitignore` excludes a `private/` folder for real
  workbooks, and only synthetic data is committed.
* **Planned AI step sees headers only.** The one judgment call in this pipeline is
  which spreadsheet column holds which W-2 box. That answer is a small JSON mapping
  ([`taxfiling/mappings/w2_standard.json`](taxfiling/mappings/w2_standard.json)).
  A model can propose it from the column headers alone (with masked samples at
  most), and the local code applies it to the real rows. It will also run with no
  model at all, or with a local model, and will log exactly what was sent.

This makes the tool much safer to run on real payroll data than pasting a
spreadsheet into a chat window. Whether that is allowed for a particular client is
still the employer's decision.

## Limitations and next steps

* **Box 11** must be split into section 457 and non-457 amounts for EFW2; the sample
  sheet has a single column, so a nonzero Box 11 stops the file.
* **One employer per file**, and Virginia only so far.
* **Not supported yet:** Puerto Rico and U.S. territory wages (their RO fields are left
  at zero), the optional employer signature PIN that 2026 adds to the RE record (left
  blank), and the state-defined RV record (Virginia defines none, so none is written).
* Next: Virginia Employment Commission quarterly UI wage file (the sample workbooks
  already have a quarterly sheet), then ICESA, more states, the AI column mapper for
  messy spreadsheets, and a simple local app.

Before filing for real, upload a test file through the state's portal and compare
it with SSA's free AccuWage tool. This project is a portfolio piece, not tax advice.

## Development

```bash
python -m pytest                                   # the full suite, offline
python tools/build_efw2_layout.py 2025 taxfiling/layouts/efw2_ty2025.json   # rebuild a layout
python examples/va_2025/gen_w2_2025_va.py          # regenerate a synthetic workbook
```

The tests compare each example's output with the committed file byte for byte. After a
change that is meant to alter the output, rewrite it and review the difference:

```bash
python -m taxfiling w2 --state VA --year 2025 --input examples/va_2025/w2_2025_va_synthetic.xlsx \
    --submitter examples/va_2025/submitter.json --out "examples/va_2025/output/W2 REPORT 2025.txt"
```

## Repository layout

```text
taxfiling/
  layout.py            fixed-width encoder/decoder driven by layout tables
  layouts/             layout tables (JSON) generated from SSA's spec, one per tax year
  states/              per-state rules (JSON)
  mappings/            spreadsheet column mappings (JSON)
  readers/excel.py     workbook -> W-2 model
  validate.py          SSN, EIN, address and amount checks
  formats/efw2.py      W-2 model -> EFW2 records
  check.py             round-trip checker
  cli.py               command line
specs/ssa/             text of SSA's EFW2 specifications for 2025 and 2026
tools/                 spec-text parser and layout builder
examples/va_2025/      synthetic workbook, its generator, submitter settings, output
examples/va_2026/      the same for tax year 2026
tests/                 pytest suite
```
