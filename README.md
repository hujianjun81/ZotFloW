# ZotFlow

[English](README.md) | [简体中文](README.zh-CN.md) · [MIT License](LICENSE)

**A modular research assistant grounded in your Zotero library, paper annotations, and actual research progress.**

ZotFlow helps you understand why a paper's method works, compare evidence across papers, find resources that fit your situation, and turn results into useful research discussions. It began with wireless foundation models (WFM) and model compression; recommendations should follow the user's own field, goals, and constraints.

This repository contains a **local agent skill**, not a standalone application or a Zotero extension. A compatible agent executes the workflows and bundled Python helpers. Version: **2.0.0**.

## What it does

ZotFlow is one entry point with modules loaded only when relevant.

| Workflow | What you can ask for |
| --- | --- |
| `library` | Search local Zotero metadata; read available full text, annotations, and paper-linked conversations. |
| `close-reading` | Explain the problem, design rationale, implementation, and supporting ablations; create focused reading notes. |
| `resource-search` | Find papers, code, weights, datasets, or tutorials suited to current knowledge and practical constraints. |
| `research-review` | Review learning progress and research plans; distinguish completed work, confirmed decisions, and AI suggestions. |
| `paper-comparison` | Compare mechanisms and evidence, identify missing controls, and assess transfer to the user's research task. |
| `experiment-review` | Interpret existing results, check comparison conditions, and prepare questions for a supervisor discussion. |
| `figures` | Draw editable method diagrams or convert selected experimental tables into appropriate charts. |
| `note-quality` | Check reading-note structure, equation references, and supported image/link formats. |

The guiding principle is **compress repetition and routine configuration; preserve “problem → why this design → how it works → ablation evidence.”** Formula explanations connect the mathematical operation to its practical purpose. Annotations and recurring questions guide where more explanation is needed. Notes have no fixed word limit.

## Requirements

- An agent that can load `SKILL.md`, read local files, and run Python commands. The installation below targets **Codex**; other hosts need their own skill setup and permissions.
- **Python 3.10+**, with standard-library modules only for the bundled helpers. Local validation used Python 3.12 on Windows; other platforms have not been independently verified in this release.
- A local Zotero database for library and annotation access. The default location is `~/Zotero/zotero.sqlite`; custom locations are configurable.
- Optional: a compatible **LLM-for-Zotero conversation registry** for paper-linked chat history, and its **MinerU cache layout** for structured full text and figures. ZotFlow does not bundle either plugin or a PDF parser.
- Optional: web/search tools for external discovery, plotting tools for charts, and compatible `nature-academic-search` or `nature-figure` skills. These are not bundled dependencies or automatically connected services.

Research reviews can also use documents and context supplied directly by the user. Missing Zotero chats or MinerU output should be reported as unavailable, rather than invented or treated as proof that no records exist.

## Install in Codex

Install this repository as a skill folder named `zotflow`. These paths follow the [official Codex skill documentation](https://learn.chatgpt.com/docs/build-skills).

**Windows / PowerShell**

```powershell
New-Item -ItemType Directory -Force -Path (Join-Path $HOME '.agents\skills') | Out-Null
git clone https://github.com/hujianjun81/ZotFloW.git (Join-Path $HOME '.agents\skills\zotflow')
```

**macOS / Linux**

```bash
mkdir -p ~/.agents/skills
git clone https://github.com/hujianjun81/ZotFloW.git ~/.agents/skills/zotflow
```

For a project-only installation, clone into `<project>/.agents/skills/zotflow` instead. Choose one installation scope to avoid duplicate skill entries. If that destination already contains a personal skill, back it up or compare it before installing; `git clone` will not replace a nonempty folder.

Codex normally detects skill changes automatically. If ZotFlow does not appear, restart Codex. Invoke it by name, or use `$zotflow` in a Codex prompt where supported. Work inside your research project so the agent can locate project records and save outputs in the intended directory.

## Configure local data

No API key is required by the bundled scripts. Set environment variables in the environment where the agent's shell runs. Run the CLI examples below from the installed skill folder.

| Variable | Default | Purpose |
| --- | --- | --- |
| `ZOTERO_ROOT` | `~/Zotero` | Directory containing `zotero.sqlite` and usually `storage/`. |
| `MINERU_BASE` | `$ZOTERO_ROOT/llm-for-zotero-mineru` | Root of the compatible parsed-paper cache. |
| `ZOTFLOW_TEMP_DIR` | System temporary directory | Optional existing writable directory for database snapshot files. |

PowerShell example; replace these sample paths with your own:

```powershell
$env:ZOTERO_ROOT = 'D:\Research\Zotero'
$env:MINERU_BASE = 'D:\Research\parsed-papers'
python scripts/query_zotero.py --list-collections
```

Bash equivalent:

```bash
export ZOTERO_ROOT="$HOME/Zotero"
export MINERU_BASE="$ZOTERO_ROOT/llm-for-zotero-mineru"
python scripts/query_zotero.py --list-collections
```

Skip the overrides when defaults match your setup. If using `ZOTFLOW_TEMP_DIR`, create the directory first. For desktop agents, ensure their processes inherit the variables; setting them in an unrelated terminal may not affect the app.

For personalized recommendations, point ZotFlow to existing project records or describe your field, current question, supervisor's requirements, available data/models/hardware, and decisions already made. No fixed profile filename is required. Keep private records in your research project, outside this public skill repository.

## Example requests

Use natural language; workflow names are optional.

```text
Use ZotFlow to create reading notes for [paper title]. Read my annotations
and available paper-linked questions. Explain why the core design is needed,
how it works, and what the ablations actually establish. Save to [directory].

Use ZotFlow to find resources for my current WFM compression question.
Check my existing reading history first, then find useful new papers and
official code. Explain what each resource helps me do with my available setup.

Review my learning progress using [project records]. Separate what I have
demonstrated from what I have only read, and suggest a verifiable next step.

Compare [paper A] and [paper B] for [target task]. Check assumptions,
supervision, costs, evidence, and conditions required for transfer.

Analyze [result files] and prepare a supervisor discussion: what is supported,
what is uncertain, and which comparison would resolve the uncertainty?
```

Default internal instructions, templates, and reading-note style checks are primarily **Chinese**. You can request English output, but the built-in linter does not provide equivalent English style coverage. Bilingual READMEs do not imply fully localized internal templates.

## Bundled helpers

Replace `PAPER001` with an actual eight-character Zotero item or PDF attachment key.

```bash
python scripts/query_zotero.py --search "quantization" --json
python scripts/query_zotero.py --key PAPER001 --annotations --json
python scripts/read_zotero_context.py --key PAPER001
python scripts/read_mineru.py --key PAPER001 --toc
python scripts/read_mineru.py --key PAPER001 --table 2
python scripts/lint_note.py --note /path/to/paper-note.md
python scripts/lint_note.py --vault /path/to/notes --moc index.md
python scripts/audit_figures.py --vault /path/to/notes --selftest
```

- **Database access:** source database files are read only. The helper verifies stable copies of the database and any WAL, checks the copy with SQLite, then queries an in-memory snapshot. A continuously changing source produces a bounded-retry error. It creates and cleans temporary copies and does not write to the original Zotero library.
- **Conversation lookup:** only supported message tables associated with a valid paper registry are queried. Raw branches and repeated questions are retained; possible inherited duplicates are flagged. Unsupported plugin schemas and unavailable selected-text context are reported. This is not a universal export tool for every Zotero AI plugin.
- **MinerU reader:** expects `_llm_source.json`, `full.md`, and, for structured operations, `manifest.json`, `content_list.json`, and `images/` under each parsed-paper directory. Arbitrary raw MinerU output may need adaptation. See [library details](references/library.md).
- **Note checks:** use `zotflow_type: close-reading` for new reading notes; use the primary workflow name for other research artifacts, optionally adding a `zotflow_workflows` list. This prevents a comparison report containing a Zotero key from inheriting reading-note style rules. `--moc` explicitly enables coverage checks against an existing index. Image auditing covers Obsidian image embeds and HTML `<img>` references; it is not a general Markdown validator.

Use `--help` for available options. If your Python 3 interpreter is named `python3` or `py`, use that command in place of `python`. Script output can contain private annotations and conversations; inspect it before sharing.

## Figures and evidence

Method diagrams normally use editable `.drawio.svg`; experimental data plots use `.svg`. Keep one final format per figure, embed it next to the relevant explanation, and put temporary previews in the research project's `.build/`. Plotting/rendering dependencies depend on the selected backend and are not installed by this repository.

Charts should preserve source values, units, controls, and table/page references. Do not invent error bars or call theoretical FLOP reductions measured latency gains. Missing ablations are evidence gaps. Generated notes and lint passes do not establish scientific correctness or mastery.

## Privacy and scope

This public repository contains skill instructions, templates, helper source code, and synthetic tests. It excludes personal libraries, PDFs, annotations, chat exports, credentials, and private project records. The Python helpers contain no network-upload logic; an agent using a cloud model, web search, or external tools is governed by that host's data handling and permissions. “Local file access” does not mean the entire AI workflow runs offline.

Research suggestions remain suggestions. Discussing a plan does not by itself authorize training runs, altered dataset splits, external messages, or recurring monitoring. ZotFlow should preserve already confirmed user decisions and identify sources, assumptions, missing evidence, and implementation constraints.

## Repository and validation

```text
SKILL.md                     Unified entry and routing rules
manifest.yaml                Workflow and helper map
references/                  Reading, context, figures, templates, quality rules
references/workflows/        Search, review, comparison, experiment workflows
scripts/                     Local readers, checks, and synthetic regression tests
```

Run these from the repository root; no real Zotero library is needed:

```bash
python -m unittest discover -s scripts -p "test_*.py" -v
python scripts/lint_note.py --selftest
```

Tests cover snapshot consistency/read-only behavior, annotation fidelity, paper/conversation association, branch retention, artifact routing, configurable indexes, and safe figure self-tests. They do not validate every plugin version or every scientific conclusion. For issues, include a reproducible description and sanitized fixture rather than a private database or chat export.

For a clean Git installation, update with `git pull --ff-only` inside the skill folder. Preserve or commit your local customizations before updating.

## License

[MIT](LICENSE). The license covers this repository's contents; referenced papers, third-party tools, models, datasets, and user-supplied material retain their own licenses.
