# Coding standards

How code in this repository is formatted, linted and reviewed, and which tool checks
each rule. Much of the code is written by agents, so the rules live in tools wherever
possible and this file explains them. A rule no tool checks is marked as a
**guideline**.

Existing code that breaks a rule is listed in a baseline that may only shrink:
`# noqa: <code> - baselined, may only shrink` for complexity, and
`tools/size_baseline.json` for file size. New code meets the rules.

## 1. Purpose and precedence

- Tools enforce; this file explains. When the two disagree, the tool's configuration
  is the truth and this file is the bug.
- `CLAUDE.md` wins over anything here. Its personal-data rules are absolute: never
  read `data/`, never commit it, never paste report content anywhere public, all test
  data from the generators.
- `DESIGN.md` owns everything the user sees. `SECURITY.md` owns what to report and
  how. `docs/legal-basis.md` owns the disclosure categories. This file repeats none
  of them.
- A pull request that changes a tool's configuration updates this file in the same
  pull request.

## 2. The layers

Fastest first. Each catches what the one before it cannot.

| Layer | When | What runs | Configured in |
|---|---|---|---|
| Format hook | After every Claude Code edit | `ruff format` and import sorting on the edited Python file, Prettier on the edited UI file; never a path under `data/` | `.claude/settings.json`, `tools/format_edited.sh` |
| Pre-commit | Every commit, staged files | Hygiene, YAML/TOML/JSON syntax, gitleaks, ruff, Prettier, actionlint, zizmor, markdownlint, shellcheck, hadolint, the size check, the `data/` refusal, the PII scanner | `.pre-commit-config.yaml` |
| Commit message | Every commit | Conventional Commits (section 8) | `.pre-commit-config.yaml` |
| Pre-push | Every push | The UI type-check, then the Python and UI unit tests (`make pre-push`) | `.pre-commit-config.yaml`, `Makefile` |
| CI | Every push and pull request | Everything above, plus editorconfig-checker, ty (advisory), the container suite, the PII history scan, CodeQL | `.github/workflows/` |

`make hooks` (which `make setup` runs) installs all three local hook types. An
existing `.git/hooks/pre-push` is kept as `pre-push.legacy` and still runs first.
**Never bypass a hook with `--no-verify`**; if one is wrong, fix the hook.

| Command | Does |
|---|---|
| `make fmt` | Format: ruff format and import sorting; Prettier over `frontend/` |
| `make lint` | ruff check and format check; Prettier check over `frontend/` |
| `make types` | ty over `src`, `tools` and `tests`; reports, never fails |
| `make size` | The size check; `make size-report` lists files over the preferred size; `make size-update` lowers the baseline |
| `make typecheck-frontend` | `tsc` over the UI, no emit |
| `make pre-push` | What the pre-push hook runs |
| `make check` | The PII scan, the fixture, lock and size checks, then every hook over every file, editorconfig-checker included |
| `pre-commit run --files <changed>` | The commit hooks on just what you changed |

Before finishing a change, run `make fmt lint`, or `pre-commit run --files` on what
changed. Never hand-format against the formatter: if its output looks wrong,
restructure the code.

## 3. Python

- **Formatting:** ruff format, line length 100, target Python 3.12
  (`pyproject.toml`, `[tool.ruff]`). Python blocks inside Markdown are formatted
  too; `docs/handoff.md` is excluded, because it is the live contract and is cited
  by section.
- **Version:** ruff is pinned exactly, to the same release in `pyproject.toml` and
  the pre-commit `rev`; `tests/test_packaging.py` fails when they disagree. Bump
  both together, in a pull request of their own.
- **Lint:** ruff check with `E, F, W, I, B, UP, C4, SIM, S, PLW, PLE, PLR0912,
  PLR0915, C90, RUF, ASYNC, PT, TID, PTH, ERA, BLE, DTZ`. Narrowed where a rule
  would only produce noise:
  - `PT018` (one condition per assert) is off: asserting two related facts
    together reads better than two asserts.
  - `RUF001`–`RUF003` accept the typographic characters the prose and the
    receipts use (`allowed-confusables`: `×`, `–`, `−`, curly quotes, `›` and a
    few more).
  - The per-file ignores in `pyproject.toml` say why each is narrowed, for
    example `S101` (assert) in tests and `S603`/`S607` in `tools/`.
- **Deliberate patterns the rules flag**, each suppressed on its line with the
  reason:
  - `BLE001`: a broad `except Exception` where a failure must degrade, never
    raise. An adapter's `sniff()` must not raise; one malformed basket costs one
    warning, not the parse (`docs/handoff.md` §4).
  - `DTZ001`/`DTZ007`: a retailer's timestamp is a wall clock with no zone, and is
    stored as one.
- **Suppressions:** `# noqa: <code> - <reason>` on the line, never a blanket
  `# noqa`, and never a rule disabled to make CI green. `RUF100` fails on a
  suppression that suppresses nothing, which is what keeps the baselines honest.
- **Types (advisory):** ty, pinned exactly (`ty==0.0.86`), over `src`, `tools` and
  `tests` (`[tool.ty.src]`). `make types` and CI's "Types (advisory)" job report
  the count and never fail: 163 diagnostics when it arrived, mostly
  `unresolved-attribute` and `unsupported-operator`. Do not add `# ty: ignore` to
  lower the count. It becomes a gate once ty is stable and the count is near zero.
- **Invoking tools:** everything in `tools/` runs as a module from the repository
  root, `python -m tools.scan_pii`, never by path. `tests/test_packaging.py`
  fails on a path-form call in the Makefile, the workflows or the hooks.
- **Imports:** `tools/` is repository tooling and is never imported by the
  application (`src/`). The application may import itself freely. (Guideline.)
- **Network:** no outbound call at runtime, outside the opt-in local vision model
  `CLAUDE.md` describes. (Guideline, backed by tests of the one carve-out.)

## 4. TypeScript and React

- **Formatting:** Prettier, pinned exactly in `frontend/package.json` (3.9.10),
  with one root `.prettierrc.json`: width 100, double quotes, trailing commas.
  `.prettierignore` leaves out build output, lockfiles and Markdown (which is
  markdownlint's). The pre-commit hook runs it from `frontend/node_modules`
  through `tools/npm_hook.sh`, so run `make setup-frontend` once first; CI runs
  `npm run format:check`.
- **Types:** `tsc` in strict mode (`frontend/tsconfig.json`: `strict`,
  `noUnusedLocals`, `noUnusedParameters`, `noFallthroughCasesInSwitch`,
  `verbatimModuleSyntax`). It runs in the pre-push hook and in CI's frontend build.
- **Tests:** vitest over the pure functions behind what the reader sees
  (`frontend/src/logic.test.ts`); browser behaviour is the container suite's.
- Visual rules (colour, type, spacing, no cards, `.num` for aligned numerals) are
  `DESIGN.md`'s, and are checked in review. (Guideline.)

## 5. Size and complexity

- **File size** (`tools/check_size.py`, a pre-commit hook and a CI step): code files
  prefer 400 lines, warn past 500, fail past 800; test files 600, 750 and 1,200.
  The scope is `src/unbagged`, `tests`, `tools` and `frontend/src`, without the
  fixture generators. The ten files already past the limit when the check arrived
  are in `tools/size_baseline.json` at their line counts: they may shrink, never
  grow. Only `make size-update` changes the baseline, and only downward.
- **Function size** (ruff): McCabe complexity 12 (`C901`), 15 branches
  (`PLR0912`), 50 statements (`PLR0915`). Fifteen functions past these when they
  arrived carry `# noqa: <codes> - baselined, may only shrink`.
- Split a file by responsibility, never into `part1`/`part2`, and never squeeze
  lines to fit. (Guideline.)

## 6. Tests

- **Data is synthetic, always.** Every fixture comes from a generator under
  `src/unbagged/adapters/*/fixtures/`, run by `tools/make_fixtures.py`;
  `make fixtures-check` fails on a fixture that is not generator output. A fixture
  the scanner cannot read (a workbook, an image) is built where it is used, never
  committed.
- **Tiers:** fast tests by default (`make test`); container and browser tests carry
  the `container` marker (`make test-container`). `CONTRIBUTING.md`, "Which tier a
  test belongs in", says where a new test goes.
- **A fix comes with the test that failed before it.** (Guideline.)

## 7. Docs, YAML, shell, Docker, workflows

- **Every file:** UTF-8, LF, final newline, no trailing whitespace except a Markdown
  line break (`.editorconfig`, `.gitattributes`, the pre-commit hygiene hooks;
  editorconfig-checker in CI, with indent size and line length left to the
  formatters). Indent 4 for Python, TOML, shell, SQL and the Dockerfile; 2 for
  TypeScript, JSON, YAML, CSS and HTML; tabs in the Makefile.
- **YAML, TOML, JSON:** must parse (`check-yaml`, `check-toml`, `check-json`).
  `docker-compose.dev.yml` is excluded from `check-yaml`: its `!override` tag is
  Compose's, not plain YAML's.
- **Workflows:** actionlint (which runs shellcheck over `run:` blocks) and zizmor.
  Every action is pinned to a commit SHA with its version in a comment; every
  checkout sets `persist-credentials: false`; a `${{ }}` value reaches a shell
  through `env:`, never interpolated into the script.
- **Markdown:** markdownlint with the relaxed set in `.markdownlint-cli2.yaml`, which
  says why each rule that is off is off. `docs/handoff.md` is excluded. Code blocks
  are fenced. A line-level `markdownlint-disable-next-line` needs a comment saying
  why.
- **Shell:** shellcheck. **Docker:** hadolint, with `DL3008` (apt version pins) off
  for the reason given in `.hadolint.yaml`.
- **The CHANGELOG** is public and permanent: nothing from a real response appears in
  it (`CONTRIBUTING.md`, "The CHANGELOG is public and permanent").

## 8. Commits and pull requests

- **Messages are Conventional Commits** (`conventional-pre-commit`, commit-msg
  stage): `type(scope): subject`, with the types this history uses: `feat`, `fix`,
  `docs`, `test`, `chore`, `style`, `ci`, `deps`, `release`, `refactor`, `perf`,
  `build`, `revert`. A merge commit's generated message passes.
- **Nothing from a real report** in a commit message, PR description, comment, test
  name or docstring. The PII scanner checks the tree and history; the rest is on
  you. (Partly guideline.)
- **One concern per pull request.** Formatting-only changes go in their own pull
  request, and their commit is added to `.git-blame-ignore-revs`. (Guideline.)
- **Run `make check-pii` before every commit**; the hook runs it too.

## 9. Dependencies

- Dependabot opens grouped monthly updates (`.github/dependabot.yml`): one PR per
  ecosystem for minor and patch bumps, React and React DOM together, and no
  `@types/node` major until the Node runtime moves.
- The runtime is hash-locked (`docker/requirements.txt`, `make lock-check`); the UI
  installs from `package-lock.json` with `npm ci`.
- Every pre-commit hook rev and every action SHA is pinned. Bump hook revs with
  `pre-commit autoupdate` in a pull request of their own, since a linter upgrade can
  change what passes.

## 10. Later

Arriving in their own pull requests, each of which updates this file:

- ty as a gate, once it is stable and the count is near zero.
- ESLint over the UI, with a shrink-only suppressions baseline.
