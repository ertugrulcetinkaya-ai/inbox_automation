-- AppleScript to fetch calendar/meeting candidate messages for one Mail account.
-- Read-only implementation: it never changes message state.

set fieldDelimiter to "__MAIL_DIGEST_FIELD__"
set lineBreakToken to "__MAIL_DIGEST_LINEBREAK__"
set targetEmail to "ertugrul@cetinkayalar.com"
set maxContentChars to 40000
set maxSourceChars to 100000
set lookbackDays to 30

on replaceText(theText, searchString, replacementString)
	set AppleScript's text item delimiters to searchString
	set textItems to every text item of theText
	set AppleScript's text item delimiters to replacementString
	set theText to textItems as text
	set AppleScript's text item delimiters to ""
	return theText
end replaceText

on flattenField(theText, fieldDelimiter, lineBreakToken)
	set theText to my replaceText(theText, lineBreakToken, " ")
	set theText to my replaceText(theText, return, lineBreakToken)
	set theText to my replaceText(theText, linefeed, lineBreakToken)
	set theText to my replaceText(theText, tab, " ")
	set theText to my replaceText(theText, fieldDelimiter, " ")
	return theText
end flattenField

tell application "Mail"
	set finalOutput to ""
	set allAccounts to every account
	set cutoffDate to (current date) - (lookbackDays * days)

	repeat with theAccount in allAccounts
		set isTargetAccount to false
		try
			set accountAddresses to email addresses of theAccount
			if targetEmail is in accountAddresses then set isTargetAccount to true
		end try

		if isTargetAccount is true then
			set accountName to name of theAccount
			set allMailboxes to every mailbox of theAccount

			repeat with theMailbox in allMailboxes
				set mailboxName to name of theMailbox

				-- Only the primary Inbox is in scope.
				if mailboxName is equal to "INBOX" or mailboxName is equal to "Inbox" then
					-- Meeting invitations are included even if they were already read.
					-- The Python layer performs the final meeting/date validation.
					set inboxMessages to (every message of theMailbox whose date received is greater than cutoffDate and ¬
						(subject contains "meeting" or subject contains "toplant" or subject contains "görüşme" or ¬
						subject contains "gorusme" or subject contains "invitation" or subject contains "invite" or ¬
						subject contains "calendar" or subject contains "takvim" or subject contains "appointment" or ¬
						subject contains "randevu" or subject contains "interview" or subject contains "mülakat" or ¬
						subject contains "mulakat" or subject contains "conference" or subject contains "konferans" or ¬
						subject contains "seminar" or subject contains "webinar" or subject contains "event" or ¬
						subject contains "etkinlik" or subject contains "schedule" or subject contains "planlama" or ¬
						subject contains "davetiye" or subject contains "zoom" or subject contains "teams" or ¬
						subject contains "webex" or subject contains "call" or subject contains "sync" or ¬
						content contains "BEGIN:VCALENDAR" or content contains "BEGIN:VEVENT" or ¬
						content contains "text/calendar" or content contains ".ics" or ¬
						content contains "microsoft teams" or content contains "teams toplant" or ¬
						content contains "zoom.us" or content contains "webex.com" or ¬
						content contains "join meeting" or content contains "katılın" or ¬
						content contains "toplantı" or content contains "toplanti"))

					repeat with theMsg in inboxMessages
						set theSender to sender of theMsg
						set theSubject to subject of theMsg
						set receivedDate to (date received of theMsg) as string
						set theContent to content of theMsg as string
						set sourceMessageId to ""
						try
							set sourceMessageId to (message id of theMsg) as string
						end try
						set rawSource to ""
						try
							set rawSource to source of theMsg as string
						end try

						-- Keep the transport one-record-per-line and bound the payload size.
						if (count of theContent) > maxContentChars then
							set theContent to text 1 thru maxContentChars of theContent
						end if
						if (count of rawSource) > maxSourceChars then
							set rawSource to text 1 thru maxSourceChars of rawSource
						end if

						set recordLine to (my flattenField(accountName, fieldDelimiter, lineBreakToken)) & fieldDelimiter & ¬
							(my flattenField(mailboxName, fieldDelimiter, lineBreakToken)) & fieldDelimiter & ¬
							(my flattenField(theSender, fieldDelimiter, lineBreakToken)) & fieldDelimiter & ¬
							(my flattenField(theSubject, fieldDelimiter, lineBreakToken)) & fieldDelimiter & ¬
							(my flattenField(receivedDate, fieldDelimiter, lineBreakToken)) & fieldDelimiter & ¬
							(my flattenField(theContent, fieldDelimiter, lineBreakToken)) & fieldDelimiter & ¬
							(my flattenField(sourceMessageId, fieldDelimiter, lineBreakToken)) & fieldDelimiter & ¬
							(my flattenField(rawSource, fieldDelimiter, lineBreakToken))
						set finalOutput to finalOutput & recordLine & linefeed
					end repeat
				end if
			end repeat
		end if
	end repeat

	return finalOutput
end tell
