# Security Audit

**Date:** 2026-08-10
**Scope:** All tracked source, configuration, and documentation at `d48e788`, plus the
complete git history — all 12 commits reachable from any ref, including commits whose
changes were later reverted.

This document describes findings. It deliberately quotes no real taxpayer
identifiers, counterparty names, credentials, or invoice amounts.

## Method

1. **Credential sweep across full history.** Every commit's diff was searched for
   Anthropic key prefixes (`sk-ant-`), GitHub token prefixes (`ghp_`), AWS access key
   IDs (`AKIA`), and PEM private-key headers.
2. **Personal and business data sweep across full history.** Searched for ten-digit
   literals in the shape of a Polish NIP, and for email addresses outside of commit
   author trailers.
3. **Reverted-commit inspection.** A revert removes code from the working tree but not
   from history, so the reverted commit was inspected directly rather than assumed safe.
4. **Ignore-rule verification.** Used `git check-ignore` to confirm which sensitive paths
   are actually excluded, rather than reading `.gitignore` and assuming.
5. **Outbound data-flow review.** Traced what leaves the machine, since a repository can
   be clean while the program still transmits private data by design.

## Findings

### F1 — No credentials in history. No action.

No API key, token, or private key was committed at any point in the repository's
history. The live Anthropic API key and KSeF token exist only in `config.yaml`, which
`git check-ignore` confirms is excluded, along with `config.*.yaml`.

### F2 — No real business data in history. No action.

The only matches from the personal-data sweep were placeholders in
`config.example.yaml`: a sequential dummy NIP and `your_email@gmail.com`-style
addresses. No real counterparty, taxpayer identifier, or invoice amount has ever been
committed.

### F3 — The reverted QNAP commit is not an exposure. No action.

Commit `23926d4` added a QNAP File Station client and was reverted by `a54ec13`. It
remains reachable in history, so it was inspected directly. It contains a routine that
base64-encodes a password supplied at runtime from config; it contains no password
value. Not an exposure.

### F4 — No history rewrite is required. No action.

F1 through F3 together mean there is nothing to excise. No `git filter-repo`, no
force-push, and no credential rotation prompted by repository exposure. This is worth
stating explicitly, because a history rewrite is disruptive and is often performed
reflexively.

### F5 — Generated reports were only conditionally ignored. Fixed.

**Severity: medium.** The monthly workbook contains real counterparty names, NIPs, and
amounts.

The default report path is `invoices/output/`, which was already covered by the existing
`invoices/` rule — so the default configuration was never at risk. The gap was
conditional: running `rename` or `excel` with a non-default `--directory` produces an
`output/` directory outside any ignore rule, and a workbook there could be staged by a
careless `git add -A`.

Fixed by adding `output/`, `*.xlsx`, and `*.xls`, which match at any depth regardless of
the `--directory` chosen.

### F6 — `test/` is ignored, which would silently untrack a test suite. Worked around.

**Severity: low, but a live footgun.** Line 4 of `.gitignore` excludes `test/`. A test
suite created in a directory of that name would appear to be committed while remaining
untracked, and CI would run against nothing.

The suite therefore lives in `tests/` (plural). The rule itself is left in place: removing
it is not required by anything here, and it may be load-bearing for a local workflow.

### F7 — Malformed ignore rule. Fixed.

`__pycache__/i` on line 2 was a typo — a rule matching a directory named `i` inside
`__pycache__`. It was inert, and the correct rule on line 3 was doing the work. Removed
to avoid future confusion.

### F8 — Invoice contents are transmitted to the Anthropic API. By design; documented.

**Severity: informational. No code change.**

This is not a repository issue, but an audit that omitted it would be misleading. The
tool's core function requires sending private data off the machine:

| Path | What is transmitted |
|------|--------------------|
| `attachment_processor` NIP check | The user's own NIP, interpolated into the prompt, plus the attachment's text and page images |
| `invoice_renamer` categorisation | Extracted invoice text and page images — counterparty names, amounts, line items |
| `ksef_excel` description (new) | Counterparty name, flattened line items, and totals |

Two properties limit the exposure, both deliberate:

- The new description feature sends only the fields it needs — counterparty, positions,
  totals — rather than the whole FA(3) XML, which carries more than the task requires.
- Prompt templates live in `config.yaml`, so prompt text is never committed.

Anyone operating this tool should understand that invoice contents reach a third-party
API. That is inherent to an LLM-based classifier, not a defect.

## Residual risk

| Risk | Status |
|------|--------|
| Credentials in git history | None found |
| Real business data in git history | None found |
| Generated reports committed accidentally | Closed by F5 |
| Test suite silently untracked | Avoided by F6 |
| Secrets in the working tree | `config.yaml` correctly ignored; depends on the operator not renaming it to an untracked-but-unignored name |
| Invoice data sent to a third-party API | Accepted, inherent to the design (F8) |

## Fixture data policy

No real invoice data enters this repository, including in tests. Fixtures use invented
company names, synthetic NIPs generated to satisfy the Polish NIP checksum, and
fabricated KSeF reference numbers. Fixture FA(3) documents are hand-built
minimal-but-valid structures, never captured production payloads.
