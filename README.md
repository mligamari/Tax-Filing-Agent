# Tax-Filing-Agent

Turns a spreadsheet of employee W-2 data into the fixed-width text files that state
wage-reporting portals accept (EFW2 today, ICESA next), then reads the file back to
prove it says exactly what the spreadsheet says.

It runs entirely on your own computer. Payroll files are full of Social Security
numbers, and many employers' privacy agreements forbid uploading them to a cloud AI
service. So the part that touches SSNs is plain Python with no network access. The one
step that uses AI, working out which column of a messy spreadsheet holds which W-2 box,
sends the model column headers and the *shape* of the values (`###-##-####`, `Aa`,
`#.##`), never a value, and a guard inside the HTTP client checks every request byte
against the workbook before it can leave. See
[AI column mapping and the privacy boundary](#ai-column-mapping-and-the-privacy-boundary)
and [SECURITY.md](SECURITY.md).

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
python -m pip install -e ".[test]"          # add ,ai (".[test,ai]") for the Claude / local-model mapper

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
| `map` | Proposes a column mapping for a spreadsheet laid out differently from the sample, with fixed rules (default, offline), Claude (`--ai claude`) or a model on your computer (`--ai local`). `--dry-run` prints exactly what would be sent. |

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

## AI column mapping and the privacy boundary

Real payroll exports never look like the sample sheet. One system says "Social Sec #",
another "EE SSN"; one puts Box 15's state in a column called "Work State", next to a
"St" column that is the employee's home state. Working out which column is which is
the one judgment call in this pipeline, and it is the only part a language model does.
Everything that touches a Social Security number stays deterministic, local Python.

```text
 workbook (.xlsx) ─► every cell, in memory on this computer ──────────────────────┐
        │                         │                                               │
        │                  denylist: every cell value                             │
        ▼                         │                                               │
 profile: headers, types,         │                                               │
 shapes (###-##-####, Aa, #.##)   │                                               │
        │                         ▼                                               │
        ├─► CHECK 1  build_payload(): patterns + denylist on the payload          │
        │                                                                         │
        ├─► CHECK 2  inside the HTTP client, on the request about to go:          │
        │            only POST to https://api.anthropic.com/v1/messages (or       │
        │            loopback for a local model), only the SDK's own headers,     │
        │            patterns + denylist on every header and the whole body       │
        │            ──► audit log: URL, headers, exact body (never the key)      │
        ▼                                                                         │
 ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ the only thing that leaves the machine ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ │
 Claude (api.anthropic.com) or a local model (loopback only) or no model          │
        │  answer: {field -> column id}                                           │
        ▼                                                                         │
 CHECK 3  untrusted answer: known ids and fields only, each column's              │
          shape must fit its field, required fields present                       │
        ▼                                                                         │
 mapping JSON (header names only) ─► a person reviews it                          │
        ▼                                                                         ▼
 `taxfiling w2 --mapping ...`: reader + writer + round-trip checker on the full rows
```

### What leaves the machine, and what does not

| Sent to the model | Never sent |
|---|---|
| Column headers and sheet names, unless they look like data (then withheld) | Any cell value below the header row, whole or in part |
| Cell types per column (`text`, `integer`, `decimal`, `date`) and fill counts | SSNs, names, addresses, ZIP codes, EINs, state account numbers, amounts |
| Up to three value *shapes* per column: letters collapsed to `A`/`a`/`Aa`, digits to `#` | The last four digits of an SSN (shapes have no digits at all) |
| Labels like `us_state_code` when *every* value is in a public list | The values behind those labels |
| The fixed instructions and the W-2 field list | Anything from the workbook in the URL or an HTTP header |
| The SDK's own HTTP headers: SDK and Python versions, operating system, CPU type, and your API key to authenticate | Your API key in the audit log (a placeholder stands in for it) |

This is the actual text sent for four columns of the messy example
([`examples/va_2025_messy/`](examples/va_2025_messy/)):

```text
{"id": "S1.C06", "column": "F", "header": "Social Sec #", "filled": "50 of 51", "types": {"integer": 50}, "shapes": ["#"]},
{"id": "S1.C09", "column": "I", "header": "St", "filled": "50 of 51", "types": {"text": 50}, "shapes": ["A"], "all_values_are": ["us_state_code"]},
{"id": "S1.C13", "column": "M", "header": "Fed Wages", "filled": "50 of 51", "types": {"text": 50}, "shapes": ["$#,###.##", "$#,###.#", "$#,###"]},
{"id": "S1.C35", "column": "AI", "header": "State Acct #", "filled": "50 of 51", "types": {"text": 50}, "shapes": ["##-#########A-###"]},
```

A name shape is `Aa` whatever its length; an amount shape shows its format (`#.##`,
`$#,###.##`), never its size. `python -m taxfiling map --input FILE --dry-run` prints the
whole payload, so you can read every byte before deciding to send it.

### How the boundary is enforced

The rule is enforced by code that does not trust the code before it:

1. **Shapes by construction.** The profiler ([`mapper/profile.py`](taxfiling/mapper/profile.py))
   never copies a cell value into its output, only its shape: ASCII punctuation is the only
   character of a value that can appear in one.
2. **Check 1, on the payload.** [`build_payload()`](taxfiling/mapper/boundary.py) is the only
   way to make an `OutboundPayload` (the constructor refuses to run without it), and it
   checks every string against the workbook itself: nothing shaped like an SSN, no run of
   5+ digits, no e-mail or phone number, no string equal to a cell value, no cell value of
   two or more words anywhere in it (a full name in a header), no cell value that mixes
   letters and 4+ digits (a street address), no 4-digit number that is the end of a
   9-digit cell. A header or sheet name that fails is withheld before this point.
3. **Check 2, on the wire.** The model providers ([`mapper/providers.py`](taxfiling/mapper/providers.py))
   install a request hook in the HTTP client. After the SDK has built the request, and
   before any byte is sent, the hook checks all of it, not only the body:
   * **where it goes:** only `POST https://api.anthropic.com/v1/messages` for Claude (a
     local model: the same path on `localhost`, `127.0.0.1` or `::1`), with no query
     string and no user name in the URL;
   * **the headers:** only the ones the SDK itself sends (its version, your operating
     system and Python version, timeouts, the API version). `Host` and `Content-Length`
     must match the request, and every other name and value gets the checks from check 1
     (all but the last-4 comparison, since the API version header is a date). The only
     values not compared are the ones from your Anthropic login: the API key and, with
     an `ant auth login` profile, your workspace ID;
   * **the body:** the checks from check 1 on every string and number, including each
     column header inside the user message, which travels as JSON inside a string.

   A failure stops the request; nothing is retried. Setting `ANTHROPIC_BASE_URL` cannot
   redirect it.
4. **Audit log of exactly what was sent.** Only after check 2 passes is the request
   written to `mapping-audit.jsonl`: the URL, every header and the exact body with its
   SHA-256, followed by the model's answer. The API key is the one thing left out (a
   placeholder stands in for it). A blocked request is logged with its reason and host
   only, never its path, headers or body. The mapping file records the payload hash, so
   each mapping can be traced to the request that produced it.
5. **The answer is untrusted.** The answer format lists the real column ids, so the model
   can only name columns that exist; [`mapper/proposal.py`](taxfiling/mapper/proposal.py)
   checks it again locally and compares each column's shape with its field (an SSN column
   must look like `###-##-####`, a money column must hold numbers, a flag Y/N). A model that
   swaps two columns gets a warning on each, and a person reviews the table before use.
6. **The file is still checked.** The writer and the round-trip checker run on the full rows
   exactly as before, so a wrong mapping shows up as validation errors or a failed check.
7. **Consent, and no network otherwise.** Sending asks first (or needs `--yes`). The
   `anthropic` package is an optional extra imported only by the model providers; a test
   proves `w2`, `check`, `show` and `map --ai none` never load it or any HTTP library.

### Three modes

| `--ai` | Who proposes the mapping | Network |
|---|---|---|
| `none` (default) | Fixed header rules ([`mapper/heuristic.py`](taxfiling/mapper/heuristic.py)). Cautious: when two columns match, it maps neither and says so. | None |
| `claude` | Claude (`claude-opus-5-5` by default) through Anthropic's API, with structured output | `api.anthropic.com` only |
| `local` | Any model on your computer that serves the Anthropic Messages API, such as Ollama (`--model qwen3`) | Loopback only; any other address is refused |

All three get the same payload and answer in the same format, so the checks after them
do not care which one answered.

```bash
python -m taxfiling map --input examples/va_2025_messy/w2_2025_va_messy.xlsx --dry-run      # read the payload
python -m taxfiling map --input examples/va_2025_messy/w2_2025_va_messy.xlsx                # no model
export ANTHROPIC_API_KEY=...                                                                  # or `ant auth login`
python -m taxfiling map --input examples/va_2025_messy/w2_2025_va_messy.xlsx --ai claude    # asks before sending
python -m taxfiling w2 --state VA --input examples/va_2025_messy/w2_2025_va_messy.xlsx \
    --submitter examples/va_2025/submitter.json --mapping w2_2025_va_messy.mapping.json
```

On the messy example, the fixed rules map 31 of the sheet's 33 W-2 fields and all four Box 12 pairs, and stop at "St"
versus "Work State", which only context can settle:

```text
Required fields not mapped: state, box15_state
  note: state: columns S1.C09, S1.C34 all match; left unmapped for you to choose
```

[`w2_2025_va_messy.mapping.json`](examples/va_2025_messy/w2_2025_va_messy.mapping.json) is
that proposal with the two lines added by hand. The messy workbook holds the same people
and amounts as the standard 2025 example, so the file it produces is byte-for-byte the
committed `W2 REPORT 2025.txt`; the tests and CI check that, and check that a model's
correct answer gives the same result.

### Tests that hold the boundary in place

* [`tests/test_mapper_boundary.py`](tests/test_mapper_boundary.py): for all three example
  workbooks, no SSN (dashed or not), last four of an SSN, name, street, city, ZIP, EIN, state
  account number or amount appears in the payload, and it has no run of 5+ digits. With
  shape masking switched off, or a bug that copies a value into the profile, the payload is
  refused, and the error message itself quotes no data. Headers holding a name (alone or
  inside a longer header), phone number or e-mail, and a sheet named after an SSN, are
  withheld. Each kind of data is blocked by the wire check, including in JSON keys,
  numbers and headers inside the user message. Of the HTTP headers, only the SDK's own
  pass (even when an SSN ends in the API version's year), and workbook data in any
  header's name or value is refused.
* [`tests/test_mapper_providers.py`](tests/test_mapper_providers.py) (against a mock HTTP
  transport, with sockets disabled): the request that reaches the transport is clean, and
  its URL, headers and body equal the audit log entry, except the API key, which is not in
  the log; a request built by calling the SDK directly with an SSN in it never reaches the
  transport, and neither does data put in the query string, the path, a user name in the
  URL, or a header's name or value; `ANTHROPIC_BASE_URL` and a moved client cannot change
  the host; a "local" model on another machine is refused.
* [`tests/test_mapper_proposal.py`](tests/test_mapper_proposal.py): unknown sheets, columns,
  fields, duplicates and columns from another sheet are refused; swapped columns are flagged.
* [`tests/test_mapper_end_to_end.py`](tests/test_mapper_end_to_end.py): messy workbook to
  checked Virginia file through the command line, byte-identical to the committed file, with
  no SSN in anything printed or logged.

[SECURITY.md](SECURITY.md) has the threat model: what is protected, from what, how, and
what is left over.

## Privacy and security (the rest of the pipeline)

* **No network access.** Only `map --ai claude` and `map --ai local` can open a connection.
  A test runs the whole write, check and show cycle with Python's socket functions replaced
  by ones that fail, and another proves those commands never import an HTTP library.
* **SSNs stay out of everything printed.** Console output, warnings and error
  messages mask SSNs (`***-**-5232`). The same test checks that none of the 50 SSNs
  appears anywhere in the console output. Only the output file holds full SSNs,
  because the state requires them.
* **What is checked is what is saved.** The round-trip check runs on the exact bytes
  before they are written, so a file that fails its check never sits on disk under
  the name the state expects.
* **Real data stays out of git.** `.gitignore` excludes a `private/` folder for real
  workbooks and the mapping audit log, and only synthetic data is committed.

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
* **The mapper** reads one row per employee with one column per box. A sheet with one row
  per Box 12 entry, or names in a single "Last, First" column, needs reshaping first.
* Next: Virginia Employment Commission quarterly UI wage file (the sample workbooks
  already have a quarterly sheet), then ICESA, more states, and a simple local app.

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
  mapper/              column mapper: profile, privacy boundary, providers, answer checks
  validate.py          SSN, EIN, address and amount checks
  formats/efw2.py      W-2 model -> EFW2 records
  check.py             round-trip checker
  cli.py               command line
specs/ssa/             text of SSA's EFW2 specifications for 2025 and 2026
tools/                 spec-text parser and layout builder
examples/va_2025/      synthetic workbook, its generator, submitter settings, output
examples/va_2026/      the same for tax year 2026
examples/va_2025_messy/ the 2025 data as a messy payroll export, its generator and mapping
tests/                 pytest suite
```
