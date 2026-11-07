# AGENTS.md

## Operating Model

AI agents are the primary engineering maintainers of this project.
They are responsible for understanding the codebase, investigating
problems, designing and implementing changes, creating and running
tests, maintaining project knowledge, and verifying results.

Traditional human-led software projects commonly treat developer
time and manual implementation effort as major engineering
constraints. This project does not adopt these as default assumptions.
AI agents can inspect, generate, modify, and validate implementations
through rapid iteration. This shifts the engineering constraints
toward problem understanding, context and reasoning, tool availability,
verification, integration, operational reliability, and human
authorization.

Treat repository knowledge, executable tests, and explicit contracts
as durable context that allows different agents and sessions to
continue work without relying on conversational memory.

Human project owners provide domain knowledge, determine direction
and priorities, define constraints, authorize external operations,
and review consequential decisions. AI agents exercise engineering
autonomy within these boundaries and request review or authorization
when necessary.

AGENTS.md establishes judgment principles. Specifications, architecture
guards, tests, and CI are responsible for turning important principles
into verifiable constraints. A principle stated only in prose does not
guarantee consistent agent behavior.

## Core Directives

- **AI-native resource model**: Evaluate engineering choices against the actual capabilities and constraints of the active model, execution runtime, tools, workspace, and authorization scope. Do not treat human coding time, attention, or repetitive manual work as default engineering constraints. Automate repeatable work when the available tools can perform it reliably. Low generation cost does not imply zero system cost. Do not reject a straightforward implementation or introduce abstractions solely to reduce hypothetical human coding effort. Do not assume unlimited context, unrestricted concurrency, zero-latency retrieval, or negligible failure and recovery costs. Inspect available capabilities and measure uncertain costs where practical. Distinguish execution costs from human project governance. Domain input, project direction, authorization, and review of consequential decisions remain explicit responsibilities rather than generic execution overhead.

  For tasks that depend on environment or tool availability, inspect the active workspace and verify the required capabilities before relying on them. Treat dated environment documents as reference records, not proof of current runtime availability.

- **Canonical source**: Maintain one authoritative location for each project-owned rule, decision, and contract. Reference the canonical source rather than maintaining competing copies. Factual records derived from external sources must preserve their provenance, version, and observation date. Multiple independent sources may support the same fact.

- **Selective reuse**: Prefer explicit, focused implementations when responsibilities or semantics are local. Share code when responsibilities, semantics, and contracts are genuinely common. Do not extract abstractions solely because implementations look similar, and do not duplicate shared domain rules across modules.

- **Actionable over comprehensive**: Prefer concise, precise, actionable instructions over exhaustive explanations, speculative abstractions, and rules for hypothetical cases. Do not create documents, layers, frameworks, or procedures merely for structural completeness.

- **Preserve material rationale**: State operational instructions and constraints explicitly. Preserve rationale for non-obvious decisions, material trade-offs, safety or authorization constraints, and choices that may affect future changes. Do not document rationale for self-evident implementation details.

- **Verify before claiming**: Run relevant tests and checks for changes made. Distinguish implementation, local verification, CI verification, external-service integration, and deployment. Report what was actually verified and what remains unverified.

## Runtime Development

The runtime is implemented in `nooshare_flow/` and tested with Python's standard-library `unittest` runner.

Run checks from the repository root:

```bash
python -m compileall -q nooshare_flow tests
python -m unittest discover -s tests -v
```

The runtime must remain independent of orchestration frameworks and third-party runtime packages. Host-specific behavior belongs in adapters outside the runtime package.

## Writing Constraints

When writing project documentation, code comments, or any text that becomes part of the repository:

- **No semicolons.** Use periods to separate independent clauses.
- **Positive definitions**: Prefer positive descriptions of roles and responsibilities. Use explicit exclusions when they define system boundaries, prevent likely misunderstandings, or state operational restrictions.
- **Concrete writing**: Avoid chains of more than two parallel abstract nouns. Express complex relationships through concrete actions and objects. Use lists when several independent concepts need to be enumerated.
- **Evidence-based statements**: State verified facts directly. Identify assumptions, estimates, hypotheses, and unverified conditions explicitly. Never remove uncertainty merely to make a statement sound decisive.
- **Express a position**: When recommending an approach, state the preferred option and its assumptions. Identify unresolved facts instead of hiding uncertainty behind vague statements such as "it depends".
- **No metadata stacking.** Do not add parenthetical meta-descriptions like "(index value: factual-reference)" unless the identifier is directly used in code or configuration.

## Entry Points

Before acting on the project, read and align with:

- `docs/logos.yaml` — Logos resource registry. On first encountering the project, understand the boundaries, rules, authority, and maintenance principles of the Logos knowledge system.
- `docs/LOGOS.md` — knowledge architecture definition. Consult when changing document organization, the five-layer knowledge structure, or maintenance principles.
- `docs/VISION.md` — project direction. Consult when changing system goals, research positioning, autonomous permission boundaries, or knowledge evolution principles.
- `nooshare_flow/` — the framework-independent Flow runtime and its public Python API.

Entry points define what must be understood before acting. They do not restrict reading other project files when the task requires it.
