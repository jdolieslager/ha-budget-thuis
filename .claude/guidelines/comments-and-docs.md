# Comments & Documentation Guidelines

Standards for how we use code comments and where different kinds of context belong,
for this Python / Home Assistant codebase. The rule we optimise for: comment the way
good engineers do it. That means **no line-by-line narration, no obvious restatement of
code** and instead **capturing the things the code cannot say for itself**, with most
context living in a more appropriate place than an inline comment.

This document is prescriptive. When in doubt, follow the decision tree in section 4.

---

## 1. Philosophy: when to comment, and when not to

### The core principle

> The overall idea behind comments is to capture information that was in the mind of the
> designer but couldn't be represented in the code.
> — John Ousterhout, *A Philosophy of Software Design*

Two consequences follow, and they drive everything below:

1. **Comment the *why*, not the *what*.** The code already states what it does. A comment
   earns its place only when it adds information the reader cannot get by reading the code:
   intent, rationale, invariants, constraints, gotchas, or the history of a decision.
2. **Good comments add value at a *different level* than the code.** Ousterhout's test: a
   useful comment is either *lower level* than the code (adding precision — units, ranges,
   what `None` means) or *higher level* (adding intuition — what this block is *for*). A
   comment at the *same level* as the code just repeats it and is noise.

### The two schools, and where we land

- **Clean Code (Robert C. Martin)** takes the hard line that "comments are always failures"
  — every comment is an admission that the code failed to express itself. This is a useful
  provocation (it pushes you toward better names and structure first) but it is wrong as an
  absolute rule.
- **A Philosophy of Software Design (Ousterhout)** refutes it directly: "Well-written
  comments are not failures. They increase the value of code and serve a fundamental role in
  defining abstractions and managing system complexity." He warns that treating comments as
  failures breeds programmers who avoid comments to avoid looking like failures — and the
  design information in their heads is lost forever.

**Our stance:** try naming and structure *first* (self-documenting code is the goal), but do
not starve the reader of the rationale, invariants and gotchas that code physically cannot
carry. "Self-documenting" covers *what*; it almost never covers *why*. Google's own review
guidance agrees: comments "mostly explain *why* instead of *what*," and if a reviewer can't
understand why you did something, that's a signal the code needs better structure or a better
comment (or both).

### The self-documenting-code caveat

Prefer a better name over a comment that explains a bad one.

```python
# BAD: comment compensating for a poor name
d = 30  # days until the subscription lapses

# GOOD: the name carries the meaning, no comment needed
days_until_subscription_lapses = 30
```

But naming has limits. It cannot express *why 30*, *why not the API's value*, or *what breaks
if you change it*. That is comment territory.

---

## 2. GOOD vs BAD comments — the taxonomy

### GOOD reasons to write a comment

| Reason | What it captures | Example trigger |
|---|---|---|
| **Non-obvious rationale** | *Why* the code is this way when it isn't self-evident | "We poll instead of subscribe because the vendor's push events drop silently." |
| **Why-not-the-other-way** | The rejected obvious alternative and why it fails | "Not `asyncio.gather` here — the device rejects concurrent requests." |
| **Workaround + link** | A hack forced by an external bug, with a tracking link | "Work around aiohttp#1234; remove when fixed." |
| **Cross-cutting invariant** | A rule the reader must hold in their head that spans code | "Callers must hold `self._lock` before calling this." |
| **Warning / gotcha** | A landmine that will bite the next person | "Order matters: setup must run before the listener registers." |
| **TODO with a ticket** | Known, tracked future work | `# TODO(jdolieslager): batch these calls. See PROJ-123.` |
| **Complex algorithm intent** | The high-level "what for" of dense logic | "Bresenham-style stepping to avoid float drift over long ranges." |
| **Units / ranges / meaning of magic values** | Precision the type can't express | "Timeout in seconds. 0 disables it." |
| **Public API contract** | Preconditions, side effects, error modes — but this belongs in a **docstring**, see §3 |

### BAD / redundant comments — delete on sight

| Anti-pattern | Example | Fix |
|---|---|---|
| **Restating the code** | `i += 1  # increment i` | Delete. |
| **Narrating every line** | A comment above each of five obvious statements | Delete all; keep at most one "why" comment if warranted. |
| **Commented-out code** | `# old_value = fetch_legacy()` | Delete. Git remembers it (ruff `ERA001` flags this). |
| **Changelog / history in comments** | `# 2024-03: changed by J. to fix rounding` | Delete. That is what commit history and PRs are for. |
| **Author / ownership tags** | `# Author: jdolieslager` | Delete. `git blame` and `CODEOWNERS` own this. |
| **Obvious getters/setters/docstring-of-the-name** | `def get_name(self): """Get the name."""` | Prefer a name that makes it obvious; keep the docstring only if a rule requires one, and make it say something. |
| **Comment compensating for a bad name** | `x = ...  # the retry count` | Rename the variable, drop the comment. |
| **Zombie / stale comment** | Comment describing behaviour the code no longer has | Update it or delete it. A wrong comment is worse than none. |
| **Divider banners / ASCII art** | `# ===== HELPERS =====` | Use functions, classes and modules for structure instead. |

Rule of thumb: **if deleting the comment loses no information, it was noise.** If deleting it
loses information that a future maintainer needs and can't recover from the code, keep it.

---

## 3. Where context should live *instead* of inline comments

Inline comments are the *smallest, most local* home for context and the easiest to leave
stale. Most context deserves a more durable, more discoverable home. Map each kind of
information to its proper place:

### Docstrings — the API contract
- **What lives here:** the *purpose* and *contract* of a module, class, or function — what it
  does, how to use it, parameters, return values, exceptions, side effects. This is the
  reader-facing "what and how to call it," as opposed to inline comments which explain the
  *why* of the implementation *to the maintainer*.
- **Why here:** docstrings are attached to the object (`__doc__`), surface in IDEs and REPLs,
  and are extracted by documentation tooling — **Sphinx** (with the Napoleon extension for
  Google style), **mkdocstrings**, and **pdoc**. Inline comments do none of this.
- **PEP 257:** write docstrings for all public modules, functions, classes and methods; use
  `"""triple double quotes"""`; a one-line summary, and for multi-line, a summary line then a
  blank line then detail.
- **Key distinction:** docstring = the *interface* (for callers). Inline comment = the
  *implementation reasoning* (for maintainers). Don't put implementation trivia in a docstring,
  and don't restate the contract in inline comments.

### Type annotations — replace type-describing comments
- **What lives here:** the types of parameters, returns and variables.
- **Why here:** annotations are checked by tooling (mypy, ruff) and never go stale silently. A
  comment saying "`timeout` is an int" is both unchecked and redundant. Both PEP 257's spirit
  and Home Assistant's rules say type info belongs in annotations and should be *omitted* from
  docstrings.

### README — orientation and quickstart
- **What lives here:** what the project is, why it exists, how to install and run it, a
  quickstart, and pointers to deeper docs.
- **Why here:** it's the first thing a human (or an assistant) reads. Anything a newcomer needs
  in the first five minutes goes here, not buried in a comment.

### Architecture Decision Records (ADRs) — the "why we chose X over Y"
- **What lives here:** significant, hard-to-reverse design decisions and their rationale,
  including the alternatives you rejected and *why*.
- **Why here:** this is exactly the "why not the other way" context that is too big and too
  long-lived for an inline comment. An inline comment can't hold "we evaluated three
  approaches"; an ADR can, and it stays discoverable after the code has moved on.
- **Format:** Michael Nygard's original ADR — **Title, Status, Context, Decision,
  Consequences**. Or **MADR** (Markdown Any Decision Records) when you want explicit
  "Considered Options" with pros/cons. Keep them as sequentially numbered markdown files in
  `docs/adr/` (or `docs/decisions/`); numbers are never reused.
- **Link from code:** when a subtle bit of code exists *because* of an ADR, a one-line inline
  comment pointing at it is ideal: `# See docs/adr/0007-polling-over-push.md`.

### Design docs / `docs/` folder — subsystem overviews
- **What lives here:** how a subsystem fits together, data flow, cross-file narratives,
  diagrams. The high-level map that no single file's comments can carry.
- **Why here:** module docstrings can give a one-paragraph overview; anything longer or
  spanning files belongs in `docs/`.

### Commit messages & PR descriptions — rationale for a *change over time*
- **What lives here:** why *this change* was made, what problem it solves, context at the moment
  of change. This is the history — Git is the changelog, **not** comments.
- **Why here:** it's tied to the exact diff and recoverable via `git log`/`git blame` forever,
  without cluttering the source. A `# changed 2024-03 to fix X` comment is a strictly worse
  version of a commit message.
- **Format:** follow Chris Beams' seven rules — separate subject from body with a blank line;
  subject ≤ 50 chars, capitalised, imperative mood, no trailing period; wrap the body at 72;
  use the body to explain *what and why*, not *how*. Consider Conventional Commits
  (`feat:`, `fix:`, `chore:` …) if we want machine-parseable history and automated releases.

### Tests — executable documentation of behaviour
- **What lives here:** concrete examples of how the code behaves — inputs, expected outputs,
  edge cases, and the contract in force.
- **Why here:** tests are documentation that *cannot go stale* — if behaviour drifts from the
  documented example, the test fails. A well-named test (`test_setup_retries_on_timeout`)
  documents intent better than a paragraph of prose.

---

## 4. Decision tree — "I want to write down X, where does it go?"

```
I want to record something. What is it?
│
├─ The type of a parameter / return / variable
│     → Type annotation. Never a comment.
│
├─ What a function/class/module does and how to call it (the contract)
│     → Docstring (Google style). Not an inline comment.
│
├─ Why this change was made right now / what problem this diff solves
│     → Commit message + PR description. Never a comment.
│
├─ Why we chose this architecture / approach over the alternatives
│     → ADR in docs/adr/. Optionally a one-line comment linking to it.
│
├─ How a whole subsystem fits together / data flow / diagrams
│     → docs/ design doc (or a module docstring for a short overview).
│
├─ How to install / run / get started
│     → README.
│
├─ A concrete example of expected behaviour / an edge case
│     → A test.
│
├─ Why THIS line/block is non-obvious, surprising, or a workaround
│  (rationale, invariant, gotcha, "why not the other way", units)
│     → Inline comment (a full sentence, ending with a period). This is
│       the ONE thing inline comments are for.
│
└─ Restating what the code plainly does
      → Write nothing. Improve the name if it isn't clear.
```

---

## 5. Home Assistant specific rules

This codebase follows Home Assistant's developer style guidelines. These are enforced in HA
core and we adopt them here. HA requires strict **PEP 8** and **PEP 257** compliance, checked
by **Ruff** on every PR.

- **Module docstring is required.** Every file starts with a docstring describing its purpose,
  e.g. `"""Support for MQTT lights."""`.
- **Docstrings use Google style** (decided by the HA core team on 2025-07-03, chosen for
  human + tool readability and Ruff/VS Code support). Use `Args:`, `Returns:` and `Raises:`
  sections when documenting parameters, return values or exceptions. A `Raises:` section is
  encouraged. Docstrings are not *globally* mandated on every function — type annotations often
  suffice — but when present they must be meaningful.
- **Type info goes in annotations, not docstrings.** "Type information for the parameters and
  the return value should already be specified in the type annotations, and should be omitted
  from the docstrings." Fully typing your code is encouraged.
- **Comments are full sentences and end with a period.**
- **US English** spelling throughout (e.g. "color," "initialize," "canceled").
- **No author tags. No changelog comments. No commented-out code.** History belongs to Git.
- **Logging:** log messages should *not* include a trailing period or the component name (HA
  adds the component name automatically). Use `_LOGGER.debug` for anything not aimed at the
  end user; be sparing with `_LOGGER.info`. Use `%`-style lazy formatting in log calls (this is
  the one place we don't use f-strings). Never log secrets — API keys, tokens, usernames or
  passwords — even when they're wrong.

---

## 6. Before / after examples

### Example A — kill the line-by-line narration

```python
# BEFORE: every line narrated, zero information added
def update(self):
    # get the response from the api
    response = self._api.get_status()
    # parse the json
    data = response.json()
    # loop over the sensors
    for sensor in data["sensors"]:
        # set the state
        self._states[sensor["id"]] = sensor["value"]
```

```python
# AFTER: self-documenting; no comment needed because nothing is non-obvious
def update(self) -> None:
    """Fetch the latest sensor states from the device."""
    data = self._api.get_status().json()
    for sensor in data["sensors"]:
        self._states[sensor["id"]] = sensor["value"]
```

### Example B — keep the one comment that actually matters

```python
# BEFORE: no "why". The next person will "simplify" this and reintroduce the bug.
await asyncio.sleep(2)
data = await self._api.get_status()
```

```python
# AFTER: one purposeful why-comment, full sentence ending with a period.
# The device needs ~2s after a command before its status endpoint reflects
# the change; polling sooner returns stale data. See docs/adr/0007-device-polling.md.
await asyncio.sleep(2)
data = await self._api.get_status()
```

### Example C — contract in the docstring, reasoning in the comment

```python
# BEFORE: types and contract crammed into comments; implementation trivia in a docstring
def schedule_retry(delay, attempt):
    """This function multiplies delay by 2 and adds it to a list then returns."""
    # delay is an int in seconds, attempt is an int
    # cap so we don't wait forever
    return min(delay * 2 ** attempt, 300)
```

```python
# AFTER: types in annotations, contract in a Google-style docstring,
# only the non-obvious cap gets a why-comment.
def schedule_retry(delay: int, attempt: int) -> int:
    """Return the next backoff delay in seconds.

    Args:
        delay: Base delay in seconds.
        attempt: Zero-based retry attempt number.

    Returns:
        The delay to wait before the next attempt, in seconds.
    """
    # Cap at 5 minutes so a long outage doesn't push retries hours apart.
    return min(delay * 2 ** attempt, 300)
```

### Example D — TODO done right

```python
# BAD: untracked, will live forever
# TODO: make this faster someday

# GOOD: attributed and linked to a tracked ticket (satisfies ruff TD002 + TD003)
# TODO(jdolieslager): batch these per-device calls into one request. See PROJ-123.
```

---

## 7. What tooling can and cannot enforce

Linters enforce the *mechanical* rules. They **cannot judge whether a comment is worth
writing** — that stays a human responsibility, guided by this document.

**Ruff can enforce:**
- `D` rules (pydocstyle) — docstring *presence* and *format* (Google convention). Configure
  `[tool.ruff.lint.pydocstyle] convention = "google"`.
- `DOC` rules (pydoclint, preview) — that documented args/returns/raises match the signature.
- `ERA001` (eradicate) — flags commented-out code.
- `TD002` / `TD003` (flake8-todos) — TODO comments must have an author and a link/issue
  reference.
- `FIX` rules — flag leftover TODO/FIXME/XXX markers if you want them gone before merge.

**No linter can enforce (human rules of thumb):**
- Whether a comment explains *why* rather than *what*.
- Whether a comment is stale / lying about the current code.
- Whether a name is good enough to make a comment unnecessary.
- Whether context should have gone in an ADR / commit / docstring instead of inline.

For the unenforceable half, use the **comment smells checklist** below in code review.

---

## 8. Comment smells checklist (use in review)

Flag a comment if any of these are true:

- [ ] It restates what the code plainly does (delete it).
- [ ] It narrates line by line (delete all but any real "why").
- [ ] It's commented-out code (delete it — Git remembers).
- [ ] It's a changelog, date, or author tag (delete — that's Git/PR/CODEOWNERS).
- [ ] It exists to explain a bad name (rename instead).
- [ ] It describes types (move to annotations).
- [ ] It contradicts the code (stale — fix or delete; a wrong comment is worse than none).
- [ ] It's a TODO with no ticket and no owner (add a link or delete).
- [ ] It repeats the docstring contract inside the implementation.
- [ ] It's a decoration/banner rather than information (delete; use structure instead).
- [ ] It's not a full sentence ending with a period (HA style — fix it).

Conversely, **be suspicious of a diff with no comments at all** where there's a workaround, a
magic constant, a subtle ordering dependency, or a rejected-alternative — that "why" belongs
somewhere, per the decision tree.

---

## Sources

- John Ousterhout, *A Philosophy of Software Design* — comments capture what the designer
  couldn't put in code; comments add value at a different (higher/lower) level; well-written
  comments are not failures:
  https://web.stanford.edu/~ouster/cgi-bin/aposd.php ·
  https://danlebrero.com/2021/02/24/philosophy-of-software-design-summary/ ·
  https://quastor.substack.com/p/clean-codes-advice-on-comments
- Robert C. Martin, *Clean Code* ch.4 — "comments are always failures" (the view we
  qualify): https://www.linkedin.com/pulse/summary-clean-code-chapter-4-comments-robert-c-martin-ilias-el-mhamdi
- Home Assistant — Style guidelines (PEP 8/257, Ruff, module docstring, comments as full
  sentences ending in a period, Google-style docstrings, type info in annotations not
  docstrings, logging): https://developers.home-assistant.io/docs/development_guidelines/
- Home Assistant — Google-style docstring decision (approved 2025-07-03):
  https://github.com/home-assistant/architecture/discussions/878
- Google — *Software Engineering at Google*, Code Review; comments explain *why* not *what*:
  https://abseil.io/resources/swe-book/html/ch09.html ·
  https://google.github.io/eng-practices/review/reviewer/standard.html
- Google Python Style Guide — docstrings, block/inline comments used sparingly, don't state
  the obvious: https://google.github.io/styleguide/pyguide.html
- PEP 8 — Comments; "inline comments are unnecessary and in fact distracting if they state the
  obvious": https://peps.python.org/pep-0008/
- PEP 257 — Docstring Conventions: https://peps.python.org/pep-0257/
- Michael Nygard — Documenting Architecture Decisions (Title/Status/Context/Decision/
  Consequences): https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions
- MADR — Markdown Any Decision Records: https://adr.github.io/madr/ ·
  ADR templates: https://adr.github.io/adr-templates/
- Chris Beams — How to Write a Git Commit Message (the seven rules):
  https://cbea.ms/git-commit/
- Conventional Commits: https://www.conventionalcommits.org/
- Ruff rules — pydocstyle `D`, `DOC`, eradicate `ERA001`, flake8-todos `TD002`/`TD003`:
  https://docs.astral.sh/ruff/rules/ ·
  https://docs.astral.sh/ruff/rules/missing-todo-author/ ·
  https://docs.astral.sh/ruff/rules/missing-todo-link/
- Documentation tooling: Sphinx/Napoleon
  https://www.sphinx-doc.org/en/master/usage/extensions/napoleon.html ·
  mkdocstrings https://mkdocstrings.github.io/ · pdoc https://pdoc.dev/
