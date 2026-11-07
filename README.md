# NooShare Shell

NooShare Shell is the public project shell for NooShare. It publishes project direction, Agent operating guidance, knowledge-system rules, and a small framework-independent Flow runtime.

The Flow runtime is an executable capability layer. It can be called directly by Python applications, command-line entry points, Agents, tests, or orchestration adapters. It does not implement a scheduler or depend on a particular orchestration framework.

This repository is not the complete NooShare application, a domain-specific ETL implementation, or a mirror of internal development work.

## Flow runtime

The runtime is published as the `nooshare-flow` Python package. It requires Python 3.12 or later and has no runtime third-party dependencies.

Install from a local checkout:

```bash
python -m pip install .
```

Define and execute a capability:

```python
from nooshare_flow import Flow, FlowContext, run_flow


def normalize_text(value: str, context: FlowContext) -> str:
    return " ".join(value.split()).casefold()


normalize = Flow(
    name="text.normalize",
    version="1",
    execute=normalize_text,
)

execution = run_flow(normalize, "  Hello   World  ")
print(execution.output)
print(execution.record.run_id, execution.record.status.value)
```

A Flow receives its input and an execution context. It returns an ordinary Python value. Successful calls return that value together with a run record. Failed calls raise `FlowExecutionError`, which retains the failed run record and chains the original exception.

A Flow can invoke another Flow through `run_flow`. Child contexts retain parent run identity. This supports explicit composition without introducing a workflow graph or scheduler.

### Runtime boundary

The runtime owns Flow identity, invocation context, execution lifecycle, and portable success or failure records.

The caller owns scheduling, retries, concurrency, persistence, cancellation policy, external resource lifecycle, and authorization. These concerns must not be inferred from a Flow definition.

Dagster, Prefect, CLI programs, Agents, and ordinary Python callers can invoke the same Flow through `run_flow`. An adapter should translate its host's input and configuration into the Flow input, call the runtime, then translate the returned output and run record into host-native results. The runtime does not import or install those host frameworks.

The initial implementation intentionally does not provide a DAG engine, task queue, workflow registry, distributed execution, automatic retries, or a persistence backend.

## Development

Run the standard-library test suite:

```bash
python -m compileall -q nooshare_flow tests
python -m unittest discover -s tests -v
```

## Contents

- `AGENTS.md` — operating guidance for AI agents maintaining this repository.
- `docs/LOGOS.md` — the project's knowledge architecture and maintenance principles.
- `docs/logos.yaml` — the registry of published project knowledge.
- `docs/VISION.md` — the public direction of the NooShare project.
- `nooshare_flow/` — the framework-independent Flow runtime.

## Public scope

This repository contains material intended for public release. Implementation source, private datasets, credentials, operational configuration, sensitive infrastructure details, and unpublished decisions belong in their respective authoritative locations unless they have been explicitly reviewed and approved for publication.

Public documents and code should be reviewed for accuracy, disclosure, and ongoing maintainability. A statement of project direction does not imply that the corresponding capability has already been implemented.

## Licensing

No license is currently provided. Public visibility does not itself grant permission to reuse, modify, or distribute the contents. Licensing will be added when the applicable rights and intended reuse terms have been confirmed.

## Contributions and support

This repository does not currently define an external contribution process, support commitment, or response-time guarantee. GitHub Issues may be used for project discussion, but no support service level is implied.
