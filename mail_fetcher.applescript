-- AppleScript to fetch unread mail metadata as delimited records
-- Read-only implementation
-- Permanent fix for JSON control-character failures

set fieldDelimiter to "__MAIL_DIGEST_FIELD__"

tell application "Mail"
	set allAccounts to every account
	set finalOutput to ""
	
	repeat with theAccount in allAccounts
		set accName to name of theAccount
		
		-- Get all mailboxes for this account
		set allMailboxes to every mailbox of theAccount
		repeat with theMailbox in allMailboxes
			set mbName to name of theMailbox
			
			-- Filter for primary inbox only (Allow-list approach)
			set isAllowed to false
			if mbName is equal to "INBOX" or mbName is equal to "Inbox" then
				set isAllowed to true
			end if
			
			if isAllowed is true then
				set unreadMsgs to (every message of theMailbox whose read status is false)
				
				repeat with theMsg in unreadMsgs
					set theSender to sender of theMsg
					set theSubject to subject of theMsg
					set theDate to (date received of theMsg) as string
					set theSnippet to content of theMsg
					if (count of theSnippet) > 200 then
						set theSnippet to text 1 thru 200 of theSnippet
					end if
					
					-- Append record to final output string
					set finalOutput to finalOutput & (accName & fieldDelimiter & mbName & fieldDelimiter & theSender & fieldDelimiter & theSubject & fieldDelimiter & theDate & fieldDelimiter & theSnippet) & linefeed
				end repeat
			end if
		end repeat
	end repeat
	return finalOutput
end tell
