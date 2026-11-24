# Integration classification

Use this reference after identifying the candidate type.

| Candidate type | Review | Default next step |
|---|---|---|
| Markdown skill | License, useful triggers, overlap, linked files. | Import or adapt if it adds a clear workflow. |
| Local CLI or script | Commands, dependencies, writes, outputs, and maintenance. | Propose the smallest practical setup. |
| MCP, plugin, or daemon | Transport, endpoint or process, tool scope, authentication, network use, persistence, and removal. | Explain the operating model and ask whether to enable it. |
| Browser or account integration | Access scope, session handling, platform constraints, and user setup. | Keep login and credential setup explicit; offer a bounded connector when useful. |

## Decision guide

- **Accept:** clear value, manageable requirements, and no material overlap.
- **Adapt:** useful core, but remove unnecessary instructions or dependencies.
- **Defer:** useful but requires a decision about setup, cost, access, or maintenance.
- **Reject:** no unique value, unsupported claims, or an implementation that does not fit the requested use case.

A runtime component is not a rejection by itself. Describe what it needs and let the user choose whether the capability is worth it.
