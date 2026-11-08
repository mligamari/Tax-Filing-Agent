# SSA EFW2 specifications (text)

The EFW2 layout tables in [`taxfiling/layouts/`](../../taxfiling/layouts/) are generated from
these two files. They are the text of the Social Security Administration's own
specification, so every field position in the program can be traced to a line here.

| File | Document | SHA-256 |
|---|---|---|
| `efw2_ty2025.txt` | SSA Publication No. 42-007, *Specifications for Filing Forms W-2 Electronically (EFW2)*, Tax Year 2025, Version 3.3 (01/20/2026). [25efw2.pdf](https://www.ssa.gov/employer/efw/25efw2.pdf) | `a0c306e30a7f53f67549dfedede3ab3be42003086613cc7bb9deb20d86d34c53` |
| `efw2_ty2026.txt` | The same publication for Tax Year 2026, Version 2 (9/14/2026). [26efw2.pdf](https://www.ssa.gov/employer/efw/26efw2.pdf) | `65e5988a707df06356306acabfc10776d4daf6ebdaf2b1b8c5e6aebe34cb6175` |

## Where they came from

SSA's website refuses scripted downloads, so the PDFs were downloaded by hand from the
links above on 2026-11-08 and saved as text, one line per line of the PDF with page
headers and footers left in.

The only change made since then is the line endings: Windows CR/LF was changed to LF.
Before that change the SHA-256s were
`fd8c25e8c21d0df9f913278cc92a97aa2e27ee55f6b7291ff11785e869666a88` (2025) and
`a146d4d7843c3ad34927cb65858db3fea57c4712c683f05313b12917ef293bd1` (2026).

The PDF-to-text step left the control character U+0002 where the PDF has a hyphen
(13 times in 2025, 16 in 2026, as in `self\x02employed`). The parser reads it as a hyphen.

SSA publications are works of the U.S. government and are not under copyright in the
United States (17 U.S.C. § 105).

## How they are used

1. [`tools/efw2_spec_text.py`](../../tools/efw2_spec_text.py) reads every record's field
   table out of the text (positions, length, name, page and the field's description) and
   the Box 12 code chart in Appendix E.
2. [`tools/build_efw2_layout.py`](../../tools/build_efw2_layout.py) pairs every field with
   the key the program uses for it, and refuses to write the layout if anything disagrees
   (see the main [README](../../README.md#where-the-layouts-come-from)). The layout
   records this file's SHA-256.
3. [`tests/test_layout_matches_spec.py`](../../tests/test_layout_matches_spec.py) repeats
   the comparison on every test run and checks the SHA-256, and CI rebuilds both layouts
   from these files and fails if they differ from the committed JSON.

To add a new tax year or spec version: save the new PDF's text here, add the year to
`SPECS` in `tools/build_efw2_layout.py` along with keys for any new fields, then run

```bash
python tools/build_efw2_layout.py 2027 taxfiling/layouts/efw2_ty2027.json
python -m pytest
```

The builder names every field it cannot match, so a changed layout shows up as a list of
specific positions to review rather than a silent difference.
