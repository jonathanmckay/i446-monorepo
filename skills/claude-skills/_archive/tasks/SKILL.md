---
name: "tasks"
description: "Show total tasks finished today (including recurring). Usage: /tasks"
user-invocable: true
---

# Tasks Done Today (/tasks)

Show how many Todoist tasks have been completed today, including recurring tasks.

## Steps

### Step 1: Get completed tasks from activity log

Use `find-activity` with `eventType: completed`, `objectType: task`, `limit: 100`.

The activity log does **not** support date filtering — it returns events in reverse chronological order. Filter the results client-side: keep only events where `eventDate` falls on today (America/Los_Angeles timezone, i.e. UTC-7 or UTC-8 depending on DST).

If `hasMore` is true and the oldest event on the page is still from today, paginate with the `cursor` to fetch more.

### Step 2: Report

Output a single line:

```
<total> tasks done today (<one-off> + <recurring> recurring)
```
