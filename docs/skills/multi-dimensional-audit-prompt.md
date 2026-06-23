# 多维审计提示

当普通的对象级审计不够用，而工作需要同时从多个维度接受挑战时，使用这个提示。

这是一个通用默认提示。复制模板后，要根据项目真实的 owner 文档、受保护区域、验证栈、部署模型和已知风险区域来调整它。

```text
Read `AGENTS.md`, `docs/index.md`, the active requirement and owner docs, the relevant plan or changed area, and the latest verification or audit evidence.

Audit the work across multiple dimensions, not only one artifact at a time.

Check at least these dimensions:
- requirement correctness
- owner-doc alignment
- architecture or boundary impact
- verification adequacy
- regression risk
- routing and skill-selection correctness
- backlog or autonomy-policy drift

Do not assume the template's default dimensions are enough for every repository. Add project-specific dimensions when the copied project has protected domains, integration-heavy flows, security-sensitive paths, regulated workflows, or unusual deployment constraints.

Return findings first, ordered by severity.
If blocking issues are found, say `needs revision` and list the exact files, dimensions, and missing evidence.
If no blocking issue remains, say `passes multi-dimensional audit` and list residual risks by dimension.
```
