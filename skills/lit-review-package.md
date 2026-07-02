---
name: lit-review-package
description: Use when the user invokes /lit-review-package to generate a polished, shareable PDF + DOCX of a completed lit-review. Bundles executive summary, methods, findings-by-outcome, evidence quality, gaps, conclusion, and full reference list into one document. No LLM analysis — mechanical assembly + Pandoc rendering.
---

# Lit Review Packager — Skill v1

Turns any completed `/lit-review` (or `/lit-review-update`) output into a single polished PDF + DOCX deliverable suitable for C-suite, quality-committee, or clinician audiences. Pure assembly: no new prose, no LLM calls during packaging.

---

## On Activation

1. Parse the slash-command argument. If the user typed `/lit-review-package <slug>`, use that slug. If no argument, scan and prompt:
   ```bash
   python3 -m src.skills.packager --scan reviews/
   ```
   Read the JSON. If exactly one `complete` review exists, ask: "Package `{slug}`?" If multiple, list them and ask which to package. If none, tell the user there are no completed reviews to package.

2. Confirm with the user before continuing.

---

## Step 1: Validate

```bash
python3 -m src.skills.packager --validate reviews/{SLUG}
```

Read the JSON. If `ok: true`, continue. If `ok: false`, present each `issues[]` line to the user verbatim and stop. Do **not** continue past validation failures.

Common validation failures and how to relay them:

| Issue | Tell the user |
|---|---|
| `pandoc not found` | "Pandoc isn't installed. Run: `brew install pandoc`" |
| `xelatex not found` | "xelatex isn't installed. Run: `brew install --cask basictex` then open a new shell" |
| `Missing source file: <name>` | Name the file; explain that it's required and that the review pipeline may not have completed cleanly. |
| Quick-review folder | Tell the user `/lit-review-package` v1 supports systematic + scoping reviews only. |

---

## Step 2: Assemble

```bash
python3 -m src.skills.packager --assemble reviews/{SLUG}
```

Read the JSON. Tell the user:

> "Assembled `package.md` with {cards_emitted} article cards. Audit log written to `_package_audit.json`."

If `audit.synthesis_citations_unmatched` (read the audit JSON) is non-empty, surface it:

> "Note: {N} articles cited in synthesis but no card was emitted. See `_package_audit.json`."

---

## Step 3: Render

```bash
python3 -m src.skills.packager --render reviews/{SLUG} --format both
```

Read the JSON. If render fails, surface the stderr output to the user; explain that `package.md` is preserved and they can hand-edit and re-run `--render`.

On success, tell the user:

> "Rendered `{slug}_report.pdf` and `{slug}_report.docx` (Pandoc {version})."

---

## Step 4: Finalize

```bash
python3 -m src.skills.packager --finalize reviews/{SLUG} \
  --pandoc-version "{VERSION}" \
  --artifact "package.md" \
  --artifact "{SLUG}_report.pdf" \
  --artifact "{SLUG}_report.docx" \
  --artifact "_package_audit.json"
```

Read the JSON.

---

## Final Message

> "**Packaging complete.**
>
> | Artifact | File |
> |---|---|
> | PDF report | `reviews/{SLUG}/{SLUG}_report.pdf` |
> | DOCX report | `reviews/{SLUG}/{SLUG}_report.docx` |
> | Master Markdown | `reviews/{SLUG}/package.md` |
> | Audit log | `reviews/{SLUG}/_package_audit.json` |
>
> Re-run `/lit-review-package {SLUG}` any time to regenerate. The PDF/DOCX overwrite the previous outputs; the audit log is rewritten too."

---

## Re-running

`/lit-review-package <slug>` is idempotent. `--assemble` overwrites `package.md` and `_package_audit.json`; `--render` overwrites the PDF and DOCX; `--finalize` updates only the `packaged_*` keys in `state.json`. Source files are never modified.

---

## Troubleshooting

- **PDF render fails with LaTeX error:** `package.md` is preserved. The user can edit it (e.g., escape special characters) and re-run only `--render`.
- **DOCX styling looks wrong:** The DOCX style reference is `src/skills/templates/package_reference.docx`. Edit it in Word and re-render.
- **Citation not matched:** The cross-citation regex requires `Surname YYYY` form within ~40 characters. If synthesis prose deviates, the article lands in `§3.Z — Additional Included Studies`. Listed in `cards_in_uncited_section` of the audit log.
