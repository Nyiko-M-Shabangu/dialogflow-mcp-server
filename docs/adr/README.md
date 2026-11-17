# Architecture Decision Records

Each record states one design decision, its context and its consequences. Status reflects the code: **Accepted** means the decision is implemented and tested; **Proposed** means it is not built. Each record ends with implementation notes that point at the code and the tests.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-fail-closed-admin-check.md) | Fail-closed admin check in remote mode, admin tools registered by construction | Accepted |
| [0002](0002-pii-allowlist.md) | Redact track_order with an allowlist for non-admin callers | Accepted |
| [0003](0003-token-comparison.md) | Constant-time comparison of the raw Authorization header against both tokens | Accepted |
| [0004](0004-unfiltered-tools-list.md) | Keep tools/list unfiltered and enforce the role inside the tools | Accepted |
| [0005](0005-admin-tools-env-precedence.md) | MCP_ADMIN_TOOLS and MCP_ADMIN_TOKEN precedence; a blank value means unset | Accepted |
| [0006](0006-ci-and-db-tests.md) | CI on every push, database tests required in CI and guarded against the wrong database | Accepted |
| [0007](0007-session-token-binding.md) | Bind each MCP session to the credential that opened it | Proposed |
| [0008](0008-plain-log-handler-remote.md) | Plain single-line log handler in remote mode | Accepted |

For how the system works today, see [ARCHITECTURE.md](../ARCHITECTURE.md).
