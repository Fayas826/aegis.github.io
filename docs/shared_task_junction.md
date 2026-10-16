# AEGIS shared task junction

The swarm now has one durable task-scoped coordination record backed by the
existing `AegisSQLiteStore.tasks` table.

## Ownership

- `TaskContext`: runtime identity and cancellation/deadline metadata.
- `SharedTaskJunction`: durable task document, handoff chain, events, evidence,
  cancellation request, and terminal result.
- `SwarmOrchestrator`: execution loop and agent delegation.
- `OutcomeFeedback`: terminal projection into learning, routing evidence, and
  health stores.
- `CentralBrain`: cross-task knowledge graph, not the task lifecycle database.

The junction is deliberately task-scoped. Agent A can hand off to Agent B with
structured context, and Agent B receives the same `task_id`, `correlation_id`,
goal, and merged parameters. A new process can read the task record from the
SQLite store after the original process exits.

## Chained reaction

The supported chain is:

```text
goal -> agent_started -> agent_result or handoff -> next agent -> evidence -> terminal result
```

Every terminal result is marked `SUCCESS`, `FAILED`, `TIMEOUT`, or `CANCELLED`.
A late success after cancellation is recorded as cancelled, not reported as a
successful mission. Handoff depth remains bounded by the existing swarm limit.

## What this does not claim

This junction does not make every registered agent capable. It makes identity,
context, evidence, and recovery durable for agents that have a real executable
tool and pass their capability/health checks. Browser authentication, CAPTCHA,
MFA, payments, and protected OS surfaces remain explicit boundaries.
