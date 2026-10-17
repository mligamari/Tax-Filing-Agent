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

**Virginia W-2 annual file** (Virginia Tax, Web Upload, EFW2 format) for tax year 2026:

```text
$ python -m taxfiling w2 --state VA \
      --input examples/va_2026/w2_2026_va_synthetic.xlsx \
      --submitter examples/va_2026/submitter.json

Read 50 employees for Blue Ridge Precision Components Inc from examples/va_2026/w2_2026_va_synthetic.xlsx
Input notes: 0 error(s), 8 warning(s)
  WARNING: row 2, control 00001 (Noor Nguyen): Box 12 code TT (809.38) belongs in the RO record, ...
  WARNING: row 29, control 00028 (Yesenia White): RW Delivery address: '1919 Jefferson Davis Hwy' is longer than 22 characters; ...
  ...
Wrote W2 REPORT 2026.txt (53,454 bytes) for Virginia (Virginia Department of Taxation (Virginia Tax), Web Upload, W-2 text file)
Round-trip check: PASSED
  Records: RA 1, RE 1, RW 50, RS 50, RT 1, RF 1
  Compared with source: 50 employees, 1700 fields
  Box 1 wages               3,753,338.92
  Box 2 federal tax           420,958.49
  ...
  State wages (RS)          3,753,338.92
  State tax (RS)              166,284.12
```

The written file is committed at
[`examples/va_2026/output/W2 REPORT 2026.txt`](examples/va_2026/output/W2%20REPORT%202026.txt).
To read it field by field (SSNs masked):

```text
$ python -m taxfiling show "examples/va_2026/output/W2 REPORT 2026.txt" --record RS --limit 1
--- line 4: RS (512 characters)
      3-4   State code (FIPS)                                       51
     10-18  Social Security number                                  ***-**-9322
     19-33  Employee first name                                     NOOR
     ...
    248-267 State employer account number                           30543187620F001
    274-275 State code (FIPS)                                       51
    276-286 State taxable wages (Box 16)                            33,435.14
    287-297 State income tax withheld (Box 17)                      1,108.42
```

## Install and run

Requires Python 3.10 or newer.

```bash
git clone https://github.com/mligamari/Tax-Filing-Agent.git
cd Tax-Filing-Agent
python -m pip install -e ".[test]"

python -m taxfiling w2 --state VA --input examples/va_2026/w2_2026_va_synthetic.xlsx \
    --submitter examples/va_2026/submitter.json --out "W2 REPORT 2026.txt"
python -m taxfiling check "W2 REPORT 2026.txt" --state VA \
    --input examples/va_2026/w2_2026_va_synthetic.xlsx
python -m taxfiling layout --unverified      # which layout fields are not yet confirmed
python -m pytest
```

| Command | What it does |
|---|---|
| `w2` | Reads the workbook, validates it, builds the state file and runs the round-trip check on it. The file is saved only if there are no errors and the check passes. |
| `check` | Checks any file against the layout and state rules. With `--input` it also compares every employee and amount with the source. |
| `show` | Prints a file field by field with positions. SSNs are masked unless `--show-ssn` is given. |
| `layout` | Lists a layout table and the source document for every field. |

## How it works

```text
 W-2 workbook ──► column mapping ──► W-2 model ──► validation ──► EFW2 builder ──► text file
 (.xlsx)          (JSON: header       (Decimal       (SSN/EIN        (which value         │
                   → W-2 box)          amounts)       rules, wage     goes in which        │
                                                      base, totals)   field)               │
                                       layout table (JSON) ─────────────► encoder ◄────────┘
                                                                              │
                                    round-trip checker ◄── parse file back ◄──┘
                                    (record order, every field, RT/RF totals,
                                     state rules, field-by-field vs. source)
```

* **Layouts are data, not code.** Every record's field positions, types and padding
  live in [`taxfiling/layouts/efw2_ty2026.json`](taxfiling/layouts/efw2_ty2026.json),
  and each field names the document it was taken from. A new tax year or a new format
  (ICESA) is a new table; the encoder in [`taxfiling/layout.py`](taxfiling/layout.py)
  knows nothing about EFW2.
* **States are data too.** [`taxfiling/states/va.json`](taxfiling/states/va.json)
  holds Virginia's rules: state code 51, the account number format, one RS record per
  employee, CR/LF between records but not after the last one, and the file name the
  state recommends. Employees with no Virginia wages are left out of the Virginia
  file, with a note naming each one.
* **The checker is independent of the writer.** It parses the file with the layout
  table alone and works out what each field should hold from the source data on its
  own (RW field `code_DD` must equal the Box 12 DD amount; RT totals must equal the
  sum of the RW records). A mapping mistake in the writer shows up as a mismatch
  instead of being repeated. The tests damage a good file in more than a dozen
  ways and confirm each one is caught.
* **Nothing is silently dropped or cut.** A name too long for its field is an error.
  An apartment number that does not fit moves to the EFW2 location-address field. A
  street that still does not fit is shortened with a warning that shows both versions.
  Amounts the file cannot carry yet are listed by employee.
* **Excel damage is caught.** SSNs and ZIP codes stored as numbers lose their leading
  zeros; the reader restores them and says so.

## Privacy and security

* **No network access.** The program never opens a connection. A test runs the whole
  write, check and show cycle with Python's socket functions replaced by ones that
  fail, so any network call would fail the test.
* **SSNs stay out of everything printed.** Console output, warnings and error
  messages mask SSNs (`***-**-9322`). The same test checks that none of the 50 SSNs
  appears anywhere in the console output. Only the output file holds full SSNs,
  because the state requires them.
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

## How trustworthy are the layouts?

This environment could not download the official PDFs, so each table was read from
web-fetched text and cross-checked against a second source wherever possible. Run
`python -m taxfiling layout --unverified` for the current list.

| Record | Source |
|---|---|
| RA, RE | SSA EFW2 TY2026, matched across two separate reads |
| RW 1-489 | SSA EFW2 TY2026. Positions 490-512 hold the new Box 14b tipped-occupation codes, which the fetched text cut off, so the writer refuses any row with Box 14b filled in. |
| RS | Virginia Tax Web Upload guide, cross-checked with the RS layouts New Jersey, Hawaii and Ohio's RITA publish |
| RT | SSA EFW2 TY2011 for positions 1-399 (SSA's TY2026 change list does not touch RT); the QSEHRA total at 400-414 is recalled, not confirmed |
| RF | Positions 1-16 match North Carolina's SUITS layout; the rest is blank |

Virginia reads only the RE and RS records and accepts the others without validating
them, so the fields still unconfirmed cannot cause a Virginia rejection. They are
flagged so they get confirmed against the official PDF before any other state is
added.

## Limitations and next steps

* **RO/RU records are not written yet.** Box 8 and the Box 12 codes kept in RO
  (including the new TT qualified overtime, TP tips and TA Trump account codes) are
  reported as warnings and left out. Virginia does not read RO; SSA would.
* **Box 11** must be split into section 457 and non-457 amounts for EFW2; the sample
  sheet has a single column, so a nonzero Box 11 stops the file.
* **One employer per file**, and Virginia only so far.
* Next: Virginia Employment Commission quarterly UI wage file (the sample workbook
  already has a quarterly sheet), then ICESA, more states, the AI column mapper for
  messy spreadsheets, and a simple local app.

Before filing for real, upload a test file through the state's portal and compare
it with SSA's free AccuWage tool. This project is a portfolio piece, not tax advice.

## Repository layout

```text
taxfiling/
  layout.py            fixed-width encoder/decoder driven by layout tables
  layouts/             layout tables (JSON), one per format and tax year
  states/              per-state rules (JSON)
  mappings/            spreadsheet column mappings (JSON)
  readers/excel.py     workbook -> W-2 model
  validate.py          SSN, EIN, address and amount checks
  formats/efw2.py      W-2 model -> EFW2 records
  check.py             round-trip checker
  cli.py               command line
tools/                 source script for the layout table
examples/va_2026/      synthetic workbook, its generator, submitter settings, output
tests/                 pytest suite
```
