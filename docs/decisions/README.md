# Architecture Decision Records

This directory contains Architecture Decision Records (ADRs) for this
repository. ADRs document significant architectural decisions, the
context around them, and their consequences.

## Format

Each ADR is a markdown file named `ADR-NNNN-title-in-kebab-case.md` where
`NNNN` is a zero-padded sequence number starting at `0001`.

## Template

```markdown
# NNNN. Title of the decision

Date: YYYY-MM-DD

## Status

Proposed | Accepted | Superseded by [NNNN](./NNNN-other.md)

## Context

What is the issue that we're seeing that is motivating this decision?

## Decision

What is the change that we're actually proposing or doing?

## Consequences

What becomes easier or more difficult to do because of this change?
```

## Index

| ADR | Title | Status |
| --- | ----- | ------ |
| [0001](./ADR-0001-migrations-raw-sql.md) | Migrations are raw SQL | Accepted (deploy mechanism superseded by [0005](./ADR-0005-automated-migration-application.md)) |
| [0002](./ADR-0002-wcs-entity-substrate.md) | WCS entity substrate | Accepted |
| [0003](./ADR-0003-versioned-extractions-and-corrections.md) | Versioned extractions and corrections | Accepted (refined by [0006](./ADR-0006-operator-direct-edits.md)) |
| [0004](./ADR-0004-rebuild-wcs-corpus.md) | Rebuild WCS corpus from scratch | Accepted |
| [0005](./ADR-0005-automated-migration-application.md) | Automated migration application | Accepted |
| [0006](./ADR-0006-operator-direct-edits.md) | Operator direct edits on canonical state | Accepted |
| [0007](./ADR-0007-extraction-rows-null-instructor.md) | Extraction rows write NULL instructor_id; co-instructor derived at read time | Accepted |
| [0008](./ADR-0008-upstream-clerk-verification.md) | Upstream Clerk token verification to common-python-utils | Superseded by machine API keys (no ADR) |
