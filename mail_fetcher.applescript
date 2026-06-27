-- AppleScript to fetch unread mail metadata as JSON
-- Read-only implementation

set outputList to {}
set accountsList to {}

tell application "Mail"
	set allAccounts to every account
	repeat with theAccount in allAccounts
		set accName to name of theAccount
		set accMailboxes to {}
		
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
				set unreadCount to count of unreadMsgs
				
				if unreadCount > 0 then
					set msgList to {}
					repeat with theMsg in unreadMsgs
						set theSender to sender of theMsg
						set theSubject to subject of theMsg
						set theDate to (date received of theMsg) as string
						set theSnippet to content of theMsg
						if (count of theSnippet) > 150 then
							set theSnippet to text 1 thru 150 of theSnippet
						end if
						
						-- Basic cleaning for JSON safety
						set theSender to my cleanText(theSender)
						set theSubject to my cleanText(theSubject)
						set theSnippet to my cleanText(theSnippet)
						set mbName to my cleanText(mbName)
						set accName to my cleanText(accName)
						
						set end of msgList to "{ \"sender\": \"" & theSender & "\", \"subject\": \"" & theSubject & "\", \"date\": \"" & theDate & "\", \"snippet\": \"" & theSnippet & "\" }"
					end repeat
					
					set end of accMailboxes to "{ \"name\": \"" & mbName & "\", \"unread_count\": " & unreadCount & ", \"messages\": [" & my joinList(msgList, ",") & "] }"
				end if
			end if
		end repeat
		
		set end of accountsList to "{ \"name\": \"" & accName & "\", \"mailboxes\": [" & my joinList(accMailboxes, ",") & "] }"
	end repeat
end tell

return "{ \"accounts\": [" & my joinList(accountsList, ",") & "] }"

on cleanText(txt)
	if txt is missing value then return "Unknown"
	
	-- 1. Escape Backslashes first
	set AppleScript's text item delimiters to "\\"
	set theParts to text items of txt
	set AppleScript's text item delimiters to "\\\\"
	set cleaned to theParts as string
	
	-- 2. Escape Double Quotes
	set AppleScript's text item delimiters to "\""
	set theParts to text items of cleaned
	set AppleScript's text item delimiters to "\\\""
	set cleaned to theParts as string
	
	-- 3. Replace Newlines, Carriage Returns, and Tabs with space
	set AppleScript's text item delimiters to {return, ASCII character 10, tab}
	set theParts to text items of cleaned
	set AppleScript's text item delimiters to " "
	set cleaned to theParts as string
	
	-- Reset delimiters
	set AppleScript's text item delimiters to ""
	return cleaned
end cleanText

on joinList(theList, delimiter)
	set AppleScript's text item delimiters to delimiter
	set joined to theList as string
	set AppleScript's text item delimiters to ""
	return joined
end joinList
