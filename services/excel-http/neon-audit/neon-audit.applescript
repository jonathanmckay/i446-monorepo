-- Called from Excel VBA via AppleScriptTask "neon-audit.scpt", "postEdit", json.
-- Hands the manual-edit payload to the excel-http daemon in the background,
-- so typing in Excel never waits on the network and nothing calls back into Excel.
on postEdit(payload)
	do shell script "/usr/bin/curl -s -m 3 -X POST -H 'Content-Type: application/json' --data-binary " & quoted form of payload & " http://127.0.0.1:9876/observe >/dev/null 2>&1 &"
	return "ok"
end postEdit
