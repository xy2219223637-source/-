# 收尾审计提示

在独立检查某个计划切片是否真的完成时使用这个提示。

所有已创建的计划都需要收尾审计。

```text
Read `AGENTS.md`, `docs/index.md`, the active requirement/design docs, the active plan, the latest related log entry, and the live changed code.

Audit whether the claimed implementation is truly closed.

Check `docs/context/ai-autonomy-policy.md` reviewer availability. Cold replay is not a second reviewer and never approves protected areas, unresolved product risk, or source-of-truth conflicts.

Focus on:
- whether live behavior matches the stated requirement
- whether the plan's closure gates are actually satisfied
- whether proof exists in files and verification results, not only in chat
- whether docs were updated where the supported baseline changed
- whether any remaining gap is still in scope
- whether task routing and recorded skill usage still match the delivered work
- whether any autonomy or backlog state was loosened without durable evidence
- whether verification failures or unrun commands are being hidden

Return findings first, ordered by severity.
If closure is blocked, say `needs revision` and list the exact missing proof or changes.
If the slice is acceptable, say `passes closure audit` and note any residual risks.
```
