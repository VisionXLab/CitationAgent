# Design: API Quota Exceeded — Global Interrupt & User Notification

**Date:** 2026-03-15
**Status:** Approved

## Problem

When the LLM API quota is exhausted during a search:

1. `author_searcher.py` enters an infinite retry loop — quota errors trigger a recursive call without incrementing `retry_count`, causing the task to hang indefinitely waiting in 60-second sleep cycles.
2. `asyncio.gather` in both `author_searcher.py` and `citing_description_searcher.py` has no timeout — one stuck task blocks all parallel workers.
3. The task never terminates — users see repeated `"⚠️ API配额超限,等待60秒后重试..."` in the log stream but have no clear indication that they should stop waiting.
4. No actionable UI feedback — no prominent notification telling users what happened or what to do next.

## Non-Goals

- Changing the incremental caching behavior (both `author_info_cache.json` and `citing_description_cache.json` already save after every paper — no changes needed).
- Changing the silent timeout/retry behavior for transient network errors (keep as-is, per user preference).

## Solution: Shared `quota_exceeded_event` + Frontend Modal

### Core Mechanism

At the start of each task run, `task_executor.py` creates a single `asyncio.Event` named `quota_exceeded_event`. This event is passed to every Searcher that makes LLM API calls. When any Searcher exhausts its quota retries, it sets this event. All other workers detect it and exit cleanly. The executor then broadcasts a `quota_exceeded` WebSocket event to the frontend, which shows a Modal dialog.

---

## Backend Changes

### 1. `citationclaw/core/author_searcher.py`

**5 affected locations:** `search_fn`, `chat_fn`, `format_fn`, `verify_fn`, `_check_self_citation_llm`

Changes per location:
- Add `cancel_event: Optional[asyncio.Event]` to `AuthorSearcher.__init__`
- Replace infinite quota recursion with a bounded loop: **max 3 quota retries**, each waiting 60 seconds
- After 3 quota failures: set `cancel_event`, return `"QUOTA_EXCEEDED"`
- In `process_paper_task`: check `cancel_event.is_set()` at entry (inside `async with semaphore`); if set, return immediately without calling LLM

### 2. `citationclaw/core/citing_description_searcher.py`

Changes:
- In `_search_fn`: detect quota errors (`'rate'`/`'quota'`/`'limit'` in error message) alongside existing retry logic
- After all retries exhausted due to quota: call `cancel_check()` to signal the executor
- In `process_row`: check `cancel_check()` before LLM calls (already partially done, extend to quota case)

### 3. `citationclaw/app/task_executor.py`

Changes:
- Create `quota_exceeded_event = asyncio.Event()` at the start of `run()`
- Pass it to `AuthorSearcher` constructor and as `cancel_check=quota_exceeded_event.is_set` to `CitingDescriptionSearcher.search()`
- After each phase, check `quota_exceeded_event.is_set()`; if set:
  - Set `self.should_cancel = True`
  - Call `self.log_manager.broadcast_event("quota_exceeded", {"message": "API 配额不足，搜索已自动停止。已处理的数据已保存至本地缓存，充值后重新运行将自动续跑。"})`
  - Return early from `run()`

### 4. `citationclaw/app/log_manager.py`

No changes needed — `broadcast_event()` already supports arbitrary event types.

---

## Frontend Changes

### `citationclaw/static/js/main.js`

Add a handler for the `quota_exceeded` WebSocket event in the existing message dispatcher (alongside `year_traverse_prompt`, `all_done`, etc.):

- Show a Modal dialog (reuse existing modal CSS/structure):

```
┌─────────────────────────────────────────┐
│  ⚠️  API 配额不足，搜索已自动停止        │
│                                         │
│  已处理的数据已保存至本地缓存。          │
│  充值后重新运行，将自动续跑，            │
│  无需重复花费 Token。                   │
│                                         │
│              [ 我知道了 ]               │
└─────────────────────────────────────────┘
```

- Set progress bar to stopped/error state
- The modal has only a single dismiss button (no choice required)

---

## Coverage Matrix

| Scenario | Handled By | Result |
|----------|-----------|--------|
| Normal mode Phase 2 quota exceeded | `author_searcher` sets event → executor broadcasts | Modal shown, task stops |
| Year-traversal mode Phase 2 quota exceeded | Same — `should_cancel` already checked in year loop | Modal shown, task stops |
| Phase 4 citing description quota exceeded | `citing_description_searcher` via cancel_check | Modal shown, task stops |
| Transient network timeout (non-quota) | Existing silent retry — unchanged | No user notification |
| Restart after interruption | Both caches auto-hit, no LLM calls for processed papers | Resume without wasting tokens |

---

## Files to Modify

| File | Type of Change |
|------|---------------|
| `citationclaw/core/author_searcher.py` | Fix 5 infinite retry locations, add cancel_event |
| `citationclaw/core/citing_description_searcher.py` | Add quota detection + cancel_check propagation |
| `citationclaw/app/task_executor.py` | Create/pass quota_exceeded_event, handle stop + broadcast |
| `citationclaw/static/js/main.js` | Add quota_exceeded WebSocket handler + modal |
