# Paper Digest Card Redesign Implementation Plan

> **For Hermes:** Implement this plan task-by-task with strict TDD and verify in a headless browser.

**Goal:** Remove thumbnail-oriented presentation and render each daily paper as a readable card containing the original paper title, metadata, English abstract, Chinese abstract, concise paper summary, and a star marker only when an internal detailed note is available.

**Architecture:** Keep the existing Markdown data as the source of truth. Extend the Python static-site generator to parse each `### [N]. Title` paper block into structured fields, resolve available note pages by arXiv ID, and render semantic paper-card HTML. Add focused CSS for information hierarchy and responsive layout; do not add thumbnails or image placeholders.

**Tech Stack:** Python 3.11, `python-frontmatter`, Python Markdown, static HTML/CSS, `unittest`, Playwright/headless Chromium.

---

### Task 1: Specify card rendering behavior with tests

**Files:**
- Create: `tests/test_build_github_pages.py`
- Modify later: `scripts/build_github_pages.py`

**Steps:**
1. Add a fixture with two papers: one with a matching detailed note and one without.
2. Assert cards include original title, EN abstract, ZH abstract, and concise Problem / Method / Result / Contribution summary content.
3. Assert only the paper with an internal note gets a visible `★ 精读` marker and an internal note URL.
4. Assert no thumbnail/image container is emitted.
5. Run the test and confirm it fails because structured card rendering does not yet exist.

### Task 2: Implement Markdown block parsing and note-link resolution

**Files:**
- Modify: `scripts/build_github_pages.py`
- Test: `tests/test_build_github_pages.py`

**Steps:**
1. Parse paper headings and field blocks without changing the source title.
2. Build an arXiv-ID-to-note-HTML index from `notes/**/*.md` frontmatter.
3. Render semantic `<article class="paper-card">` elements.
4. Render abstracts in EN-first, ZH-second order.
5. Render a compact paper summary grid from the existing problem, method, result, and contribution fields.
6. Emit `★ 精读` only when the card can jump to an existing detailed note.
7. Run focused tests until they pass.

### Task 3: Redesign card layout without thumbnails

**Files:**
- Modify: `docs/assets/style.css`

**Steps:**
1. Create a strong title/meta/actions hierarchy.
2. Use readable abstract panels rather than image slots.
3. Use a compact two-column summary grid on desktop and one column on mobile.
4. Make the starred internal-note link visually distinct and keyboard-focusable.
5. Keep card width, font size, spacing, and contrast suitable for long academic text.

### Task 4: Rebuild and verify generated pages

**Files generated:**
- `docs/digest/2026-08-02.html`
- Other static outputs from the existing build command

**Steps:**
1. Run the full unit-test suite.
2. Run `python3 scripts/build_github_pages.py` and confirm successful generation.
3. Serve `docs/` locally and use headless Chromium at desktop and mobile viewport sizes.
4. Verify: no thumbnails; 20 cards; bilingual abstracts; summary sections; exactly the papers with real internal notes receive stars; starred links resolve successfully; no horizontal overflow; stylesheets load without 404s.
5. Capture desktop/mobile screenshots for visual inspection.

### Acceptance criteria

- No thumbnail or thumbnail placeholder exists in digest cards.
- Every parsed card shows the unchanged paper title and bibliographic metadata.
- Abstract appears EN original first, then Chinese translation.
- Each card includes concise paper-level summary fields sourced from the digest.
- A star marker appears only for cards with an existing internal detailed-note target.
- Starred links navigate to valid generated note pages.
- Desktop and mobile layouts are readable and free of horizontal overflow.
