# ArkUI scene signals — combobul-specified

A system component for HarmonyOS NEXT that patches ArkUI to emit **scene
classification signals**: what kind of scene is on the display, so that
aggregation, power attribution and scheduling elsewhere have structure to
work from. This project reports scene composition. It does not decide
policy.

The spec graph is empty as of this commit. Code under `tools/` predates
combobul and is not yet bound to spec nodes; binding it is the work ahead.

There are no scene classes in this repository and that is deliberate. Three
asserted taxonomies - a C enum of 20, a PowerShell classifier of 23, and a
naming function of 28 shapes - have been deleted. Two feature vectors are
measured and each is clustered on its own; nothing confirms a class list.

## Where things are

| Path | What |
|---|---|
| `tools/cluster/rs_extract.py` | RenderService dump -> tree, surfaces, ownership |
| `tools/cluster/rs_pattern.py` | the pattern and surface vectors |
| `tools/cluster/vectors_build.py` | clustering, page payload |
| `tools/cluster/vectors_page.py` | the page |
| `tools/rs_capture.ps1` | capture on the device |
| `combobul/` | the spec engine (see below) |

Captures live on the capture machine at `C:\ark_ui\captures`, never in
this repository: they are screenshots of a real phone in real use.

## Skills drive workflows — use them

| Skill | When |
|---|---|
| `/cb-green` | Feature / change / refactor requests |
| `/cb-fix` | Bug-targeted work |
| `/cb-audit` | Read-only analysis and issue collection |

These skills encode the spec-first protocol, triage, planning, and reporting.
Invoke them instead of re-deriving the process from scratch.

## Inviolable rules

1. **No mutation outside an active `/cb-*` skill.** No file mutation, no
   `git` mutating command, no `ril apply-batch`, no install/uninstall,
   and no `silk` mutating command may be performed outside an active
   `/cb-green | /cb-fix | /cb-ops` invocation. The first two
   skills authorise full code/spec mutation under their phase protocols;
   `/cb-ops` is operational-only and authorises a fixed allowlist
   (git push/fetch/pull --ff-only/tag/stash, branch checkout, npm
   install/ci) per `CB-OPS-INV-OPERATIONAL-ONLY` — it does NOT authorise
   DAG mutation, source-tree edits, or arbitrary shell. The default
   Claude Code permission to "freely take local, reversible actions like
   editing files" does NOT apply in this project. Outside an active
   skill, only read-only investigation is permitted (Read, Grep, Glob,
   `ril query`, `npm run spec:check`, `git status`/`git log`/`git diff`,
   and similar non-mutating commands). Action-flavoured user messages
   do not implicitly authorise mutation — wait for an explicit `/cb-*`
   invocation. Rationale: see CS-079, `CS-078-INV-SILK-OWNS-SKILLS`,
   and `CS-OPS-SKILL-INTRODUCTION`.

2. **The spec verifies the code, not the reverse.** VIOLATED or UNVERIFIABLE
   means fix the code, not the spec. Never weaken a predicate, delete an
   invariant, or add `verified-by="structural"` to suppress a finding unless
   the property is genuinely enforced by construction.

3. **Specifications are DAG nodes, not markdown.** Author spec nodes via
   `ril apply-batch` before writing code. Markdown documents reasoning; the
   DAG is the specification. Pre/postconditions written in a case study are
   NOT a specification.

4. **No refactors without explicit human approval.** Report refactoring
   opportunities; do not perform them. A session grant ("refactor freely")
   does not carry across sessions.

5. **Engine code is never self-modified (CON-NO-SELF-WRITE).** Generated
   output goes to `spec/generated/`.

6. **Project isolation.** combobul source/docs must not contain artifacts
   from projects that use combobul. Use placeholder names (`ORD-INV-1`,
   `PROJECT_SPEC`) in examples.

7. **Commit discipline.** Run `npm run spec:check` before committing. One
   logical change per commit. Mutation `.cbm` files are committed alongside
   code changes. Run `ril rebuild` after clone or branch switch. Use
   `Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>`.

## Skill-active state

A skill is active iff **both** of these hold:

1. The most recent user turn started with `/cb-green`, `/cb-fix`,
   or `/cb-audit`, AND
2. The skill has not yet emitted its `FINAL_STATUS` section.

In any other state — including any follow-up turn after a skill has
reported `FINAL_STATUS`, the period before the user has invoked any
skill, and any conversation interrupted between phases — you are
**NOT** in a skill, even if the conversation is mid-task.

Action-flavoured user messages ("install X", "wire Y", "fix Z",
"continue") **do not imply skill activation**. They are requests that
must be routed through a fresh `/cb-*` invocation before any
mutation occurs. If unsure whether a skill is active, assume it is
not and request that the user invoke the appropriate skill
explicitly.

`/cb-audit` is read-only by design and never authorises mutation,
even while active.

## The RIL is the only spec interface

`spec/spec.db` is opaque binary — never read it directly. All spec reads
and writes go through the RIL CLI:

```bash
# Single node (properties only)
npm run ril -- query node <id> --json
# Full local subgraph (properties + edges + artifacts) — prefer over query node
npm run ril -- query node-info <id> [id2 ...] --json
# 1-hop neighborhood (all connected nodes)
npm run ril -- query neighborhood <id> --depth 1 --json
# Transitive dependents — run before proposing mutations
npm run ril -- query impact-analysis <id> [id2 ...] --json
# Dependency-bearing subgraph
npm run ril -- query footprint <id> --json
# Batch multiple reads in ONE subprocess (critical for efficiency)
npm run ril -- query-multi '[{"cmd":"node-info","args":["ID-1","ID-2"]},{"cmd":"impact-analysis","args":["ID-3"]}]' --json
# Mutation
npm run ril -- apply-batch '[{"type":"add-node", ...}]' --json
# Schema
npm run ril -- template <kind> --json
npm run ril -- list-kinds --json
```

### Efficient query patterns

- **Batch reads:** when querying 2+ nodes, use `query-multi` to run
  all reads in one subprocess instead of spawning N separate processes.
  Each `ril` invocation loads the entire spec.db — batching amortizes
  that cost.
- **Prefer `node-info` over `node`:** `node-info` returns edges and
  artifacts alongside properties. Use `query node` only when you need
  properties alone and want compact output.
- **Check blast radius first:** before proposing mutations, run
  `impact-analysis` on the target nodes to see transitive dependents.
  This prevents surprising downstream breakage.
- **Multi-ID commands:** `node-info`, `impact-analysis`,
  `invariant-context`, and `algorithm-context` all accept multiple IDs
  in one call. Use this instead of looping.

For authoring rules (required attributes, valid edge targets, invariants),
use `npm run ril -- template <kind> --json` which queries the live DB.
The RIL rejects mutations that violate AUTH-* rules with specific error
messages — trust its errors.

DAG data is ephemeral — do not cache. Always re-query.

## Engine reference

The DAG metamodel uses the following node kinds: `interface` (named
shape), `invariant` (a checkable predicate), `algorithm` (a named
procedure / phase), `error-code` (an emittable failure tag), plus
`module`, `automaton`, `port`, `relation`, `annotation`. Use `npm run ril -- list-kinds --json` to
enumerate the canonical set; use `npm run ril -- template <kind>` to
get attribute and edge constraints for any one of them.

## Extraction pipeline

The extraction subsystem (`SUB-EXT`) uses a four-pass, per-file
architecture. The monolithic functions (`runExt1OnPopulatedFactStore`,
`runExt2OnPopulatedFactStore`, `factStoreExt1ToMutations`) have been
DELETED — `INV-EXT-NO-MONOLITHIC-PIPELINE` bans them.

### Entry points

| Pass | Function | Location |
|---|---|---|
| ext-0 (grammar) | `projectAndInsertPhpL0V2` | `src/lmp/ext-0/php/php-l0-v2.ts` |
| ext-1 (semantic) | `runExt1ForFile` | `src/lmp/ext-1/orchestrator.ts` |
| ext-2 per-file | `runExt2PerFileProjection` | `src/lmp/ext-2/orchestrator.ts` |
| ext-2 cross-file | `runExt2CrossFileLinking` | `src/lmp/ext-2/orchestrator.ts` |

Each pass is independently invocable. The driver
(`src/lmp/core/languages/php/extract-via-fact-ir-driver.ts`)
orchestrates them per `INV-EXT-NO-CROSS-LAYER-INTEGRATION`.

### Trace logging

`ExtTraceLog` (`src/lmp/ext/types.ts`) collects per-file, per-layer
trace entries. Create via `createExtTraceLog()`. Each entry records
layer (`ext-0` | `ext-1` | `ext-2-per-file` | `ext-2-cross-file`),
file, outcome, and timing.

### CLI

`magellan extract --per-file` runs the four-pass pipeline. The `layer`
option (`--layer ext-0|ext-1|ext-2`) runs a single layer independently,
replacing the old `stopAfter` option.

## Validation

- `npm run spec:check` — L2 RI + L3 semantic. Must pass before every commit.
- `npm run spec:validate` — L2 DAG referential integrity (AUTH-* + RI-*).
- `npm run spec:semantic-validate` — L3 semantic checks (predicate
  quality, disconnected nodes, severity coherence).
- L4 is under review; treat as transitional. L5 (conformance) and L∞
  (spec:trace) have been retired. Traceability is handled by ext-2
  cross-file linking.

L1/L2 boundary: L1 = 31 AUTH-* client rules (formalized in
`auth/core/lean4/metamodel/`). L2 = server computes structural labels
and enforces RI-*/RI-L-*/RI-TS-*/RI-GAP-*.

## Vulnerability analysis subsystem (SUB-VULN)

The vulnerability analysis engine lives under `src/vuln/` (CLI alias:
`npm run badger`). It analyzes fact-stores and graph snapshots to
produce CWE findings. The subsystem follows the three-pillar pattern:

| Pillar | Path | Spec module | Contents |
|---|---|---|---|
| Authority | `auth/vuln/` | SUB-VULN-AUTH-{CORE,ENGINE,RULES} | Invariants, interfaces |
| Implementation | `src/vuln/` | SUB-VULN-IMPL-{CORE,ENGINE,RULES} | Algorithms, annotations |
| Test | `tests/vuln/` | SUB-VULN-TEST-{CORE,ENGINE,RULES} | Test algorithms |

Source tiers: `core/` (CLI, types, merger, registry), `engine/`
(pattern-analyzer, motif-to-cwe, reachability, blast-radius,
specdb-rule-engine), `rules/` (SQL rule catalog, per-language rules).

## Repository layout

| Path | Purpose |
|---|---|
| `spec/spec.db` | Unified spec graph (local cache, gitignored — rebuild via `ril rebuild`) |
| `spec/mutations/` | Immutable mutation log (Git-tracked, source of truth) |
| `spec/snapshots/` | Periodic spec checkpoints (Git-tracked) |
| `.combobul/cache/` | Session-scoped caches (auto-generated, gitignored) |
| `spec/generated/` | Generated output — never edit |
| `docs/` | Prose, decisions, case studies, foundations |
| `src/` | Engine source |
| `auth/core/lean4/` | Core formal proofs (axioms, graph, metamodel) |
| `auth/lmp/lean4/` | LMP formal proofs (admissibility, ext-0 languages) |
| `src/lmp/` | LMP implementation (ext, bridge, core) |
| `tests/lmp/` | LMP tests (unit + integration) |
| `src/vuln/` | Vulnerability analysis implementation |
| `tests/vuln/` | Vulnerability analysis tests |
| `auth/vuln/` | Vulnerability analysis authority (future proofs) |

