# PDF → HWP semantic reconstruction baseline

This contract is the small bridge between the reviewed PDF/OCR manifests and
the HWP writer.  It is intentionally source-independent: real PDF, OCR,
HWP/HWPX, screenshots, and rendered pages remain local working evidence and
are never fixtures or Git inputs.

## Approved flow

```text
reviewed scope → reviewed OCR candidate → semantic item model
              → editable problem/solution HWP/HWPX checkpoint
              → native endnote (staged_atomic) → COM/reopen/render QA
```

Physical OCR rows are temporary evidence only.  The writer receives one
semantic item at a time, with its stem, ask, materials, condition/보기 box,
choices, tables, formulas, and owned figures.  A page without an included
problem/solution region is excluded before OCR.  A mixed page is cropped to its
reviewed item regions.  A page is never treated as one item.

Problem and solution regions are joined by a stable `item_id`; section,
printed label, problem first sentence, and solution content are all required
mapping evidence.  Printed number alone, page order, modulo, and
`page_round_robin` are not mappings.

## One-request execution with internal checkpoints

The user may request “convert and add endnotes” in one request.  That is a
single delivery request, not permission to bypass verification.  The runner
must still execute the following atomic checkpoints in order:

1. freeze the copyright-source hashes and the reviewed scope/region manifest;
2. reconstruct complete semantic problem and solution items (including side
   boxes, continuation blocks, choices, tables, figures, and formulas);
3. save and reopen the editable problem/solution HWP and HWPX and pass the
   semantic, formula, layout, and image audits;
4. only then insert one native endnote per reviewed item and run the endnote
   reopen/copy-move/render audit.

If a checkpoint fails, the runner must keep the last passing checkpoint and
stop the dependent stage.  It must not silently insert a page capture, plain
text formula, or incomplete solution into an endnote to make the counts match.
The final delivery is `PASS` only when every checkpoint and the final endnote
gate pass; otherwise the report is a staged candidate with explicit failure
codes and is not labelled complete.

Every solution item also declares `solution_completeness` with the number of
source blocks and reconstructed blocks plus an empty `omitted_block_ids` list.
Explanation boxes (for example 출제코드, 해설특강, 핵심개념), answer lines,
tables, figures, and continuation blocks are content blocks. A missing side box
is not cosmetic: `SOLUTION_CONTENT_INCOMPLETE` blocks the pre-endnote
checkpoint until the block is transcribed or the item is removed from scope.

## Semantic and native object rules

- HWP paragraphs must have `origin: semantic`; direct physical OCR-row
  paragraphs fail with `PHYSICAL_OCR_ROW_SPLIT`.
- Sentence units are complete reviewed sentences.  Fragments fail with
  `SENTENCE_FRAGMENTATION`.
- When the source item has choices, they are the exact reviewed count of ordered
  native choice objects in the reviewed layout; an open-response item declares
  `expected_choice_count: 0` and does not invent choices.
  `<보기>`/condition boxes and tables are native HWP tables, never raster
  captures.
- Only a tight-cropped pure figure may remain an image.  Page, question,
  solution-body, screenshot, and text-bearing captures are forbidden.  Each
  figure has exactly one owning `item_id`.
- Body text uses 함초롬돋움 11 pt and 160% line spacing.  Paragraph spacing is
  native margins (0 pt before, 2 pt after); blank-line spacing is forbidden.
  Justification must not stretch a short/final body line.
- Equations are editable `eqed` objects using `HYhwpEQ`, 11 pt, and HWPX
  `baseUnit=1100`, with a non-empty script.  The reviewed source count,
  HWPX, HWP, COM, and rendered-PDF equation counts must form one chain.
  Scripts use Hancom equation grammar only: raw LaTeX commands, `!=`, and
  unbalanced braces/parentheses fail before HWP generation.  Subscripts,
  superscripts, sigma/product/limit bounds, fractions, roots, piecewise
  conditions, matrices, and vector symbols must remain structured native
  equation content rather than plain text.

Reviewed chunks may also contain semantic diagrams (`representation:
native_semantic` or a graph/key-point description) without a raster path. The
writer must materialise those as editable tables/text plus native equations;
it must never invent a screenshot or silently drop the diagram. Raster figures
are accepted only with a tight crop, SHA-256, owner, and explicit pure
graph/geometry/illustration reason. Semantic diagrams are excluded from the
BinData image count but remain subject to visual coordinate, label, and
numeric-value review.

Before `EquationCreate`, an explicit allow-list may normalize source shorthands
such as `\\times`, `\\Pi`, `\\leq`, escaped braces, and Unicode operators into
HancomEQN tokens. Unsupported raw backslash commands, empty scripts, and
plain-text/image formula fallbacks remain hard failures.

## Endnote release gate

The default is `endnote_mode: staged_atomic`.  Native endnotes are not written
while content is still being reconstructed.  The pre-endnote editable HWP/HWPX
checkpoint must be `PASS` (and hash-addressed) before the native endnote stage
is scheduled.  Native endnote QA remains a separate final gate, including
HWP/HWPX reopen, COM inspection, copy/move tracking, and PDF render review.

Run the copyright-safe preflight with a local manifest:

```powershell
python tools/pdf_hwp_semantic_preflight.py reviewed-semantic-manifest.json `
  --json semantic-qa.json
```

The preflight is fail-closed.  The stable failure codes include
`PHYSICAL_OCR_ROW_SPLIT`, `BODY_JUSTIFY_STRETCH`,
`SENTENCE_FRAGMENTATION`, `CHOICE_LAYOUT_MISMATCH`,
`CONDITION_BOX_NOT_NATIVE`, `ITEM_COLUMN_MISMATCH`,
`FIGURE_OWNERSHIP_MISMATCH`, `PARAGRAPH_SPACING_OUT_OF_PROFILE`,
`OCR_UNREVIEWED`, `FORMULA_NATIVE_MISSING`, and
`FORMULA_UNSUPPORTED_SYNTAX`, and `SOLUTION_CONTENT_INCOMPLETE`.

The checked-in policy is
`config/pdf_hwp_semantic_reconstruction_policy_v1.json`, and the generic
validator is `app/pdf_hwp_semantic_reconstruction.py`.
