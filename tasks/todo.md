# Task: Fix Claude Code Review for Forked PRs & Revert mcp-publisher

## Problem
- Claude Code Review workflow fails on forked PRs due to GitHub security restrictions (no OIDC token access)
- Need to revert mcp-publisher from version 1.3.3 to 1.2.3

## Plan

### Tasks
- [x] Check current mcp-publisher version in CI workflow (found: 1.3.3 in .github/workflows/ci.yml)
- [ ] Update `.github/workflows/claude-code-review.yml` to use `pull_request_target` event
  - Change event from `pull_request` to `pull_request_target`
  - Add security comment/warning about reviewing untrusted code
- [ ] Revert mcp-publisher version from 1.3.3 to 1.2.3 in `.github/workflows/ci.yml`
  - Update download URL to use v1.2.3 release
- [ ] Create new branch for these changes
- [ ] Commit the changes
- [ ] Create pull request

## Implementation Details

### Change 1: Claude Code Review Workflow
Update the `on:` trigger in `.github/workflows/claude-code-review.yml`:
```yaml
on:
  pull_request_target:  # Changed from pull_request
    types: [opened, synchronize]
```

**Security Note**: `pull_request_target` runs in the context of the base repository and has access to secrets. This allows forked PRs to be reviewed but requires careful monitoring.

### Change 2: mcp-publisher Version
In `.github/workflows/ci.yml`, change the download URL:
- From: `v1.3.3/mcp-publisher_1.3.3_...`
- To: `v1.2.3/mcp-publisher_1.2.3_...`

## Review
(To be completed after changes are made)
