# Trust report and sign-off workflow

Every export ships with a package (`out_dir/package/`, #750) and the
trust report inside it is the sign-off document (#976). This page
explains what it contains and how a professional signs it.

## What the report carries

- **Inputs, hashed.** Every file the run read (drawing set, IFC model,
  sheet image, schedule csv, config) with its SHA-256. That is the
  source/revision traceability: two reports differ only where their
  inputs differ, and an input's revision is identified by its hash.
- **Extracted values, sourced.** Every number names the file and field
  it was read from (`stage_02_model.json model.spaces[].area_m2`, and
  so on).
- **Convention biases and defaults used.** Undrawn parameters filled
  from ASHRAE 90.1-2019 or stated storey-height fallbacks are listed
  with their source, so nothing is silent.
- **Validation summary.** Check counts by severity plus every warn and
  error with its message.
- **Review queue state.** The worst open items by confidence; when the
  run wrote a review report, the package carries the review page and
  the editable decisions template with the replay command.
- **Sign-off block (#976).** The generation timestamp (UTC), the tool
  version, and blank lines for **prepared by / date** and **reviewed
  by / date** plus a **stamp/seal** column. matchline never signs
  anything; the professional fills these and applies their seal.

## How to sign it

1. Work the review queue to zero open items (or accept the remainder
   explicitly in the decisions file) before stamping.
2. Open `trust_report.html` (or the one-page PDF); confirm the inputs
   and their hashes match what was actually used.
3. Fill the preparer and reviewer lines with name, date, and seal.
   A stamped PDF or a print-sign-scan of the PDF is the deliverable;
   `trust_report.json` keeps the machine-readable copy.
4. The manifest hashes every file in the package, so a stamped package
   can be re-verified later against `manifest.json`.

## Professional-use framing

The disclaimer that ships in the package and heads the report:

> matchline produces building energy modeling inputs for review by a
> qualified professional. It is not an engineering stamp, a code
> compliance determination or a substitute for professional judgement.
> Check every value against the drawings before using it.

This framing (and the workflow above) describes how the tool is meant
to be used; it is not legal advice and not an errors-and-omissions
policy. A firm's own liability practices govern what they stamp.
