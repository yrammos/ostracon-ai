-- nrch -- enrich the BibDesk database, so the git mirror has something to mirror.
--
-- Absorbs three manual steps: running the DEVONthink-URI script, running the
-- local-URL script, and the "Convert File and URL fields" dialogue. Deliberately
-- does NOT run nrmlz: enrichment touches the BibDesk database, normalization
-- touches the .bib on disk, and keeping them apart means a failed enrichment
-- cannot leave a half-normalized mirror, while nrmlz stays runnable with BibDesk
-- closed.
--
-- ENTRY POINTS
--   osascript nrch.applescript            enrich what needs it
--   osascript nrch.applescript --audit    ignore the stamp; re-examine everything
--   osascript nrch.applescript --selection   whatever is selected in BibDesk
--   ...and, when registered on BibDesk's "Save Document" script hook, the
--   `perform BibDesk action` handler below. Measured: that hook fires ~0.25 s
--   AFTER the bytes reach disk, so the scan reads fresh content; and it does not
--   fire on autosave, so this runs on deliberate saves only, never on a timer.
--
-- SCOPE is the union of two predicates, computed by nrch-scan.py in one pass:
--   A. changed since the stamp -- the only thing that catches an attachment
--      swapped for a different file, where counts stay equal but the URI is now
--      wrong.
--   B. a deficit of Local-Url*/Devonthink* against Bdsk-File-N -- what makes the
--      design self-healing when the stamp is lost, stale or simply wrong.
-- Neither alone suffices. See nrch-scan.py for why the Devonthink half of B is a
-- heuristic rather than a criterion, and why --audit exists.
--
-- The stamp is written ONLY after a clean run. If DEVONthink was unreachable or
-- anything threw, the old stamp stays, so the work is retried rather than
-- skipped. It is an optimisation; predicate B is the floor.

property masterRel : "Documents/Bibdesk/biblio.bib"
-- This script's own directory. It lives in ostracon-ai rather than ostracon-git
-- because it acts on the BibDesk database; nrmlz and strip-bibdesk.rsc, which
-- produce the git mirror, stay there. The stamp and logs sit alongside it.
property toolRel : "Dev/ostracon-ai/dev/"
-- The two helpers used to sit in BibDesk's Scripts folder, which put them in the
-- Scripts MENU -- where each one's bare `run` handler sweeps the whole corpus
-- unscoped: ~43 s for the local-url half, and the DEVONthink half writes a log to
-- the Desktop. Harmless as libraries, a trap as menu items, and strictly worse
-- than the "Enrich Selected Entries" entry that now calls this script with
-- --selection. So they live beside the only thing that loads them.
property libRel : "Dev/ostracon-ai/dev/lib/"
property devonScriptName : "Add x-devonthink URIs based on attachment paths.scpt"
property localScriptName : "Add local URLs.scpt"
property scannerName : "nrch-scan.py"
property stampName : ".nrch-stamp"
property logName : "nrch.log"
property devonLogName : "nrch-devonthink.log"


on run argv
	set mode to "default"
	set masterOverride to missing value
	try
		repeat with a in argv
			set a to a as text
			if a is "--audit" then
				set mode to "audit"
			else if a is "--selection" then
				set mode to "selection"
			else if a starts with "/" then
				-- An explicit .bib, so the whole thing can be exercised against a
				-- scratch document instead of the live bibliography.
				set masterOverride to a
			end if
		end repeat
	end try
	return my nrchRun(mode, masterOverride)
end run


using terms from application "BibDesk"
	on perform BibDesk action with publications thePubs for script hook theScriptHook
		-- thePubs is `missing value` for a document hook; scope comes from the
		-- file either way, so it is unused. Guard on the name so registering this
		-- against another hook by accident is inert rather than surprising.
		if (name of theScriptHook) is not "Save Document" then return
		try
			my nrchRun("default", missing value)
		on error errMsg number errNum
			my appendLog(my logPath(), "HOOK ERROR: " & errMsg & " (" & errNum & ")")
		end try
	end perform BibDesk action with publications
end using terms from


on homePath()
	return POSIX path of (path to home folder)
end homePath

on toolPath()
	return my homePath() & toolRel
end toolPath

on logPath()
	return my toolPath() & logName
end logPath


on nrchRun(mode, masterOverride)
	if masterOverride is missing value then
		set masterFile to my homePath() & masterRel
		set stampFile to my toolPath() & stampName
	else
		-- Scratch runs get their own stamp, so a test never disturbs the real one.
		set masterFile to masterOverride
		set stampFile to masterOverride & ".nrch-stamp"
	end if
	set theLog to my logPath()
	set startedAt to (current date) as text
	my appendLog(theLog, "=== nrch (" & mode & ") " & startedAt & " ===")

	-- ---------- locate the master document, by path and never by index ----------
	set theDoc to my findDocument(masterFile)
	if theDoc is missing value then
		return my finish(theLog, "BibDesk has no document open for " & masterFile, true)
	end if

	-- ---------- scope ----------
	set newStamp to missing value
	set pendingKeys to ""
	if mode is "selection" then
		tell application "BibDesk" to set thePubs to selection of theDoc
		if thePubs is {} then
			return my finish(theLog, "Nothing selected in BibDesk.", true)
		end if
		set scopeNote to my plural((count of thePubs), "entry", "entries") & ¬
			" (the current selection)"
	else
		set oldStamp to ""
		if mode is not "audit" then set oldStamp to my readFile(stampFile)
		set scanOut to my runScanner(masterFile, oldStamp)
		if scanOut starts with "ERROR" then
			return my finish(theLog, "Scanner failed: " & scanOut, true)
		end if
		set {theKeys, newStamp, scopeNote, pendingKeys} to my parseScan(scanOut)
		if theKeys is {} then
			my writeFile(stampFile, newStamp)
			return my finish(theLog, "Nothing to do (" & scopeNote & ").", false)
		end if
		set thePubs to my resolveKeys(theDoc, theKeys, theLog)
		if thePubs is {} then
			return my finish(theLog, "None of the " & (count of theKeys) & ¬
				" flagged keys resolved to a publication.", true)
		end if
	end if

	-- ---------- enrich ----------
	set devonLib to my loadLib(my homePath() & libRel & devonScriptName, theLog)
	set localLib to my loadLib(my homePath() & libRel & localScriptName, theLog)
	if devonLib is missing value or localLib is missing value then
		return my finish(theLog, "Could not load the helper scripts.", true)
	end if

	set devonRec to devonLib's enrichPubs(thePubs, my toolPath() & devonLogName)
	set devonFatal to fatal of devonRec
	if devonFatal is not missing value then
		-- DEVONthink unreachable or similar: do NOT advance the stamp.
		return my finish(theLog, "DEVONthink step failed: " & devonFatal, true)
	end if

	set localWritten to localLib's addLocalURLs(thePubs)

	-- ---------- what "Convert File and URL fields" used to do ----------
	set linksAdded to my attachDevonthinkURLs(thePubs)

	set wrote to ((added of devonRec) > 0) or (localWritten > 0) or (linksAdded > 0)

	-- ---------- parse check, only when something changed ----------
	set parseNote to "skipped (nothing written)"
	if wrote then set parseNote to my bibtoolErrors(masterFile)

	-- ---------- report ----------
	-- Two renderings of the same numbers, because they serve different readers.
	-- The log wants one greppable line per run; a person wants sentences. The old
	-- single string ("devonthink+0/unresolved 0; local-url+0") served neither.
	set devonAdded to added of devonRec
	set devonUnres to unresolved of devonRec
	set totalWritten to devonAdded + localWritten + linksAdded

	set logLine to scopeNote
	if totalWritten = 0 then
		set logLine to logLine & "; nothing changed"
	else
		set logLine to logLine & "; +" & devonAdded & " DEVONthink, +" & localWritten & ¬
			" paths, +" & linksAdded & " URLs; parse " & parseNote
	end if
	if devonUnres > 0 then set logLine to logLine & "; " & devonUnres & " unresolved"
	if pendingKeys is not "" then set logLine to logLine & "; short going in: " & pendingKeys
	my appendLog(theLog, "SUMMARY: " & logLine)

	set summary to scopeNote & "." & linefeed & linefeed
	if totalWritten = 0 then
		set summary to summary & "Nothing needed changing."
	else
		set summary to summary & "Added " & my listPhrase({¬
			my plural(devonAdded, "DEVONthink link", "DEVONthink links"), ¬
			my plural(localWritten, "file path", "file paths"), ¬
			my plural(linksAdded, "clickable URL", "clickable URLs")}) & "." & linefeed & ¬
			"Parse check: " & parseNote & "."
	end if
	if devonUnres > 0 then
		set summary to summary & linefeed & linefeed & my plural(devonUnres, ¬
			"attachment could not be matched in DEVONthink.", ¬
			"attachments could not be matched in DEVONthink.")
	end if
	-- These keys come from the scan, which necessarily ran BEFORE the enrichment,
	-- so they describe what was short going in -- not what is still short now.
	-- The honest post-run figure is `unresolved` above: if that is 0, everything
	-- listed here was fixed by this very run. Labelling them "pending" was wrong
	-- and made a successful run read like a failed one.
	if pendingKeys is not "" then
		set summary to summary & linefeed & linefeed & ¬
			"Short of a full set going in, and now repaired: " & pendingKeys
	end if

	-- Stamp only now, and only in the scanner-driven modes: a --selection run
	-- says nothing about the rest of the corpus.
	if newStamp is not missing value and mode is not "selection" then
		my writeFile(stampFile, newStamp)
	end if

	return summary
end nrchRun


-- Match the document by file path. `document 1` is the frontmost window, which
-- is emphatically not the same thing, and acting on the wrong document here
-- would write fields into someone else's bibliography.
on findDocument(wantedPath)
	tell application "BibDesk"
		repeat with d in documents
			-- `path of` is the hidden text property and yields a POSIX path
			-- directly; `POSIX path of (file of d)` does NOT coerce, so the alias
			-- round-trip is the fallback rather than the obvious form.
			try
				if (path of d) is wantedPath then return d
			on error
				try
					if (POSIX path of ((file of d) as alias)) is wantedPath then return d
				end try
			end try
		end repeat
	end tell
	return missing value
end findDocument


on runScanner(masterFile, oldStamp)
	set cmd to "python3 " & quoted form of (my toolPath() & scannerName)
	if oldStamp is not "" then set cmd to cmd & " --stamp " & quoted form of oldStamp
	set cmd to cmd & " " & quoted form of masterFile
	try
		return do shell script cmd
	on error errMsg
		return "ERROR	" & errMsg
	end try
end runScanner


on parseScan(scanOut)
	set {theKeys, newStamp, changedN, deficitN, entriesN, pendingKeys} to ¬
		{{}, missing value, "?", "?", "?", ""}
	set savedDelims to AppleScript's text item delimiters
	repeat with ln in (paragraphs of scanOut)
		set AppleScript's text item delimiters to tab
		set parts to text items of (ln as text)
		set AppleScript's text item delimiters to savedDelims
		if (count of parts) ≥ 2 then
			set tagName to item 1 of parts
			if tagName is "STAMP" then
				set newStamp to item 2 of parts
			else if tagName is "KEY" then
				set end of theKeys to item 2 of parts
			else if tagName is "PENDING" then
				set pendingKeys to item 2 of parts
			else if tagName is "COUNTS" and (count of parts) ≥ 4 then
				set {changedN, deficitN, entriesN} to {item 2 of parts, item 3 of parts, item 4 of parts}
			end if
		end if
	end repeat
	set AppleScript's text item delimiters to savedDelims
	set scopeNote to my plural(entriesN, "entry", "entries") & ", of which " & ¬
		changedN & " changed and " & deficitN & " incomplete"
	return {theKeys, newStamp, scopeNote, pendingKeys}
end parseScan


-- One `whose` clause per key is one Apple event resolved inside BibDesk, which
-- beats pulling 5,700 cite keys over the bridge and scanning them in AppleScript.
-- A key can legitimately have vanished since the file was written, so each is
-- guarded rather than allowed to abort the run.
on resolveKeys(theDoc, theKeys, theLog)
	set found to {}
	tell application "BibDesk"
		repeat with k in theKeys
			try
				set end of found to (first publication of theDoc whose cite key is (k as text))
			on error
				my appendLog(theLog, "UNRESOLVED KEY: " & (k as text))
			end try
		end repeat
	end tell
	return found
end resolveKeys


-- Replaces the manual Convert step: every Devonthink* value becomes a linked URL
-- on its publication, which is what makes the links clickable in BibDesk.
-- Idempotent -- an existing linked URL is left alone.
on attachDevonthinkURLs(thePubs)
	set n to 0
	tell application "BibDesk"
		repeat with pubRef in thePubs
			set thisPub to contents of pubRef
			try
				set existing to {}
				repeat with u in (linked URLs of thisPub)
					set end of existing to (u as text)
				end repeat
				repeat with f in (every field of thisPub)
					set fName to name of f
					if fName starts with "Devonthink" then
						set v to (value of f) as text
						if v is not "" and existing does not contain v then
							add v to linked URLs of thisPub
							set end of existing to v
							set n to n + 1
						end if
					end if
				end repeat
			end try
		end repeat
	end tell
	return n
end attachDevonthinkURLs


-- bibtool as a VALIDATOR, never a rewriter. Rewriting the master would
-- re-serialize 103,908 lines into 137,037, uppercase the @comment blocks holding
-- the group hierarchy, and be undone by BibDesk's next save anyway.
--
-- Delegated to bib_audit.bibtool_errors via the scanner rather than reimplemented
-- here: that function already handles bibtool being absent, and reads both
-- stdout and stderr, which a grep for "*** BibTool ERROR" would miss.
on bibtoolErrors(masterFile)
	try
		set r to do shell script "python3 " & quoted form of (my toolPath() & scannerName) & ¬
			" --parse-check " & quoted form of masterFile
		set savedDelims to AppleScript's text item delimiters
		set AppleScript's text item delimiters to tab
		set parts to text items of r
		set AppleScript's text item delimiters to savedDelims
		if (count of parts) ≥ 2 then return item 2 of parts
		return r
	on error errMsg
		return "check failed: " & errMsg
	end try
end bibtoolErrors


-- "1 file path" / "0 file paths". AppleScript has no pluraliser and the summary
-- reads as machine output without one.
on plural(n, one, many)
	if n = 1 then return (n as text) & " " & one
	return (n as text) & " " & many
end plural

-- {"a", "b", "c"} -> "a, b and c". Drops any item that begins "0 ", so a run that
-- wrote only paths says so instead of reciting two zeroes.
on listPhrase(parts)
	set kept to {}
	repeat with i from 1 to (count of parts)
		set t to item i of parts
		if t does not start with "0 " then set end of kept to t
	end repeat
	if kept is {} then return "nothing"
	if (count of kept) = 1 then return item 1 of kept
	set lead to {}
	repeat with i from 1 to ((count of kept) - 1)
		set end of lead to item i of kept
	end repeat
	set AppleScript's text item delimiters to ", "
	set t to (lead as text)
	set AppleScript's text item delimiters to ""
	return t & " and " & (item -1 of kept)
end listPhrase


on loadLib(p, theLog)
	try
		return load script file ((POSIX file p) as text)
	on error errMsg
		my appendLog(theLog, "LOAD FAILED " & p & ": " & errMsg)
		return missing value
	end try
end loadLib


on finish(theLog, msg, isProblem)
	if isProblem then
		my appendLog(theLog, "ABORTED: " & msg)
	else
		my appendLog(theLog, msg)
	end if
	return msg
end finish


on readFile(p)
	try
		return (do shell script "cat " & quoted form of p)
	on error
		return ""
	end try
end readFile

on writeFile(p, t)
	if t is missing value then return
	try
		do shell script "printf '%s' " & quoted form of (t as text) & " > " & quoted form of p
	end try
end writeFile

on appendLog(p, t)
	try
		do shell script "printf '[%s] %s\\n' \"$(date '+%Y-%m-%d %H:%M:%S')\" " & ¬
			quoted form of (t as text) & " >> " & quoted form of p
	end try
end appendLog
