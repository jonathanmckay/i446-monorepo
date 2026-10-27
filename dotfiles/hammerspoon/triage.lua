-- Daily Downloads triage (2026-10-27): runs ~/i446-monorepo/tools/triage/triage.py
-- --apply --card once a day after 03:30. Lives here, not in launchd, because a
-- launchd python has no TCC grant for ~/Downloads; children of hs.task inherit
-- Hammerspoon's. A 15-minute check (plus wake) catches up if the Mac slept
-- through 03:30. Log: ~/.local/state/jm/triage/hs.log
local HOME = os.getenv("HOME")
local STAMP = HOME .. "/.local/state/jm/triage/last-run"
local CMD = "/usr/bin/python3 " .. HOME .. "/i446-monorepo/tools/triage/triage.py --apply --card"
  .. " >> " .. HOME .. "/.local/state/jm/triage/hs.log 2>&1"

local M = {}

local function lastRun()
  local f = io.open(STAMP, "r")
  if not f then return "" end
  local s = f:read("*l") or ""
  f:close()
  return s
end

function M.run()
  if M.task and M.task:isRunning() then return end
  local today = os.date("%Y-%m-%d")
  os.execute("mkdir -p " .. HOME .. "/.local/state/jm/triage")
  local f = io.open(STAMP, "w"); f:write(today .. "\n"); f:close()
  M.task = hs.task.new("/bin/sh", function(rc)
    if rc ~= 0 then hs.alert.show("Downloads triage failed (" .. rc .. "), see triage/hs.log") end
  end, { "-c", "echo \"== $(date)\"; " .. CMD })
  M.task:start()
end

function M.runIfDue()
  local now = os.date("*t")
  if lastRun() ~= os.date("%Y-%m-%d") and (now.hour > 3 or (now.hour == 3 and now.min >= 30)) then
    M.run()
  end
end

M.timer = hs.timer.doEvery(15 * 60, M.runIfDue)
M.wake = hs.caffeinate.watcher.new(function(ev)
  if ev == hs.caffeinate.watcher.systemDidWake then hs.timer.doAfter(60, M.runIfDue) end
end):start()

return M
