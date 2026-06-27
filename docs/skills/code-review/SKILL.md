---
name: code-review
description: |
  Code review and quality analysis. Use when the user asks for a code
  review, PR feedback, security audit, style compliance check, or
  architecture/design review. Provides structured review guidelines
  covering correctness, security, performance, style, and testing.
license: MIT
allowed-tools: search_codebase run_tests run_audit_pipeline propose_fix
metadata:
  priority: high
  depth: "full"
---

# Code Review

## Review checklist

### 1. Correctness
- [ ] Does the code do what it claims to do?
- [ ] Are there off-by-one errors, race conditions, or type mismatches?
- [ ] Are error paths handled (exceptions, None returns, edge cases)?
- [ ] Are async/sync boundaries respected? No blocking calls in async paths.

### 2. Security
- [ ] No hardcoded secrets, API keys, tokens, or passwords.
- [ ] User input is validated, sanitized, or escaped before use.
- [ ] SQL queries use parameterized statements (no string interpolation).
- [ ] File paths are sanitized (no path traversal).
- [ ] Dependencies are pinned and from trusted sources.

### 3. Performance
- [ ] No N+1 queries or unnecessary loops over large collections.
- [ ] Caching is used where appropriate for repeated computations.
- [ ] I/O is batched or parallelized when possible.
- [ ] Large objects are not held in memory longer than needed.

### 4. Style & maintainability
- [ ] Naming is clear and consistent with project conventions.
- [ ] Functions are small and single-responsibility.
- [ ] Dead code (unused imports, variables, functions) is removed.
- [ ] Comments explain *why*, not *what* (the code says what).
- [ ] Log levels are appropriate (debug vs info vs warning vs error).

### 5. Testing
- [ ] New code has corresponding tests.
- [ ] Tests cover edge cases and error paths, not just happy paths.
- [ ] Test names describe the scenario and expected outcome.
- [ ] Tests are deterministic (no random failures, no shared mutable state).
- [ ] Coverage is meaningful (tests the logic, not just the surface).

## Review workflow

```
1. Understand context
   - Read the PR description or user's intent
   - Identify the scope (files changed, new vs modified code)

2. Examine the diff
   - Check each file for correctness first (most important)
   - Then security, performance, style, testing in that order

3. Investigate surrounding code
   - Use search_codebase to find callers, callees, and related types
   - Verify API contracts are respected
   - Check for duplicated logic elsewhere

4. Run validation
   - run_tests to verify nothing is broken
   - run_audit_pipeline for lint/typecheck coverage

5. Report findings
   - Start with the most critical issues
   - Be specific: cite file paths, line numbers, and suggested fixes
   - Distinguish blockers from suggestions
```

## Output format

```
## Review: <file-or-scope>

### Blocker — must fix before merge
- **File:** `src/foo.py:42-50`
- **Issue:** ...
- **Suggestion:** use `async with` instead of `with`

### Warning — should address
- **File:** `src/bar.py:15`
- **Issue:** ...
- **Fix:** ...

### Suggestion — nice to have
- ...

### What's good
- ...
```

## Principles

1. **Be constructive.** Point out problems, but always offer a concrete suggestion or alternative.
2. **Prioritize by severity.** Blocker → Warning → Suggestion. Not every style nit needs to block.
3. **Know the project conventions.** Check `AGENTS.md`, `CONTEXT.md`, and existing code before applying style rules.
4. **Respect the author's intent.** If a design choice seems odd, ask before flagging it. There may be context you don't see.
5. **Review tests as thoroughly as production code.** Bad tests give false confidence.
