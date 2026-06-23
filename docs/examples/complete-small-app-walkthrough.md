# 小型应用完整流程示例

这个示例展示了一个小功能切片应有的形状。

它刻意保持紧凑。重点是展示具体内容，而不是展示一个很大的流程。

## 1. 原始输入

目标文件：

- `docs/input/source-pm-user-management.md`

```md
# PM Notes - User Management

Need a user management page for admins.

Admins should see users, search by name or email, and disable a user.
Disabled users cannot log in.
We do not need create/edit user profile in the first version.
```

## 2. 需求

目标文件：

- `docs/requirements/2026-05-21-user-management.md`

```md
# User Management Requirement

## Goal

Admins can view users, search users, and disable active users.

## In Scope

- user list page
- search by name or email
- disable action for active users
- disabled users cannot log in

## Out Of Scope

- creating users
- editing user profiles
- bulk actions

## Business Rules

- only admins can access the page
- disabled users are blocked at login
- disabling an already disabled user is not shown as an available action

## Roles / Permissions

- Admin: can access the page and disable active users.
- Non-admin: cannot access the page.

## Data / Model Impact

- Existing user model must expose `id`, `name`, `email`, and `status`.
- Disabling a user changes status from `active` to `disabled`.
- No new database table is required for this slice.

## API / Integration Impact

- Requires a user list query endpoint.
- Requires a disable-user command endpoint.
- No external system integration is required for this slice.

## States

- Empty: show an empty state when no users match search.
- Loading: show loading state while fetching users.
- Error: show retryable error if user list query fails.
- Permission denied: non-admin users see access denied instead of the user list.

## Open Questions

- None blocking for the first slice.

## Acceptance Criteria

- [ ] admin can open the user list page
- [ ] search filters by name or email
- [ ] admin can disable an active user
- [ ] disabled user cannot log in
- [ ] non-admin cannot access the page
```

## 3. Owner 文档更新

目标文件：

- `docs/design/app-overview.md`

示例更新：

```md
## Main Surfaces

- User Management: admin-only surface for listing, searching, and disabling users.

## Roles

- Admin: can access User Management and disable active users.
- Non-admin: cannot access User Management.
```

## 4. 待办池条目

目标文件：

- `docs/backlog/README.md`

示例行：

```md
| P0 | User Management first slice | `docs/requirements/2026-05-21-user-management.md` | `docs/design/app-overview.md` | `docs/plans/2026-05-21-user-management-plan.md` | `ready` | `plan-first` | `none` |
```

由于这个切片会改变认证可见的管理员行为，并且跨越页面、API、权限和测试，所以它是 `plan-first`，并且需要独立的计划审计和收尾审计。

## 5. 计划

目标文件：

- `docs/plans/2026-05-21-user-management-plan.md`

```md
# User Management Plan

> Plan Status: planned
> Last Reviewed: 2026-05-21
> Source: `docs/requirements/2026-05-21-user-management.md`
> Audit: required

## Current Baseline

- app has authentication
- app has admin role checks
- no user management page exists yet

## Goals

- land the first supported user list/search/disable slice

## Non-Goals

- user creation
- profile editing
- bulk actions

## Task Route

- Type: `implementation-only change`
- Owner Docs: `docs/design/app-overview.md`
- Skill Selection Basis: `plan-audit-prompt.md` and `closure-audit-prompt.md` apply as review methods; delivery phase uses `Skill: none`

## Infrastructure And Config Prereqs

- No infra prereqs beyond existing baseline

## Execution Plan

### Phase 1 - Land The Core Slice

Status: planned
Targets: `apps/...`, `packages/...`, `docs/...`
Skill: `none`

- Item Types: `Add | Proof`
- Prereqs: none

- [ ] add user list route/page
  - Skill: `none`
- [ ] wire search behavior
  - Skill: `none`
- [ ] add disable action
  - Skill: `none`
- [ ] enforce admin access
  - Skill: `none`
- [ ] add tests for search, disable, and non-admin access
  - Skill: `none`

Exit Criteria:

- [ ] user list/search/disable behavior lands for admins
- [ ] affected owner docs updated or `No owner-doc update required`
- [ ] `docs/logs/` updated

## Plan Audit

- Status: pending
- Reviewer / Agent: `<independent reviewer or subagent>`
- Evidence: `<audit file or task id>`

## Closure Gates

- [ ] in-scope behavior is complete
- [ ] relevant docs are aligned
- [ ] verification has run (specify which commands; customize for visual/UX domains if needed)
- [ ] no in-scope item downgraded to deferred/follow-up
- [ ] plan audit passed before implementation
- [ ] text consistency verified: status, phases, gates, and log all agree
- [ ] closure audit was independent
- [ ] closure evidence exists in files

## Deferred But Adjudicated

None.

## Closure

Status Note: complete only after plan and closure audits both pass.

Closure Audit Evidence:

- Reviewer / Agent: `<independent reviewer or subagent>`
- Evidence: `<task id / log link / audit file>`

Follow-up:

- none
```

## 6. 日志记录

目标文件：

- `docs/logs/2026/05-21.md`

```md
# Development Log - 2026-05-21

### 2026-05-21

- Implemented first User Management slice from `docs/requirements/2026-05-21-user-management.md`.
- Added admin-only user list, name/email search, and disable action.
- Updated `docs/design/app-overview.md` with the supported User Management surface.
- Verification: `<real test command>` passed for user list/search/disable and non-admin access.
```

## 7. 收尾审计说明

这个切片有计划，因此在计划标记完成前还需要做收尾审计。

如果审计内容非平凡、存在争议，或者对后续会话有价值，就使用单独的审计文件。否则，把独立评审者/子代理的证据记录在计划和日常日志里。
