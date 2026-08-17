-- AppleScript to fetch calendar/meeting candidate messages for one Mail account.
-- Read-only implementation: it never changes message state.

set fieldDelimiter to "__MAIL_DIGEST_FIELD__"
set lineBreakToken to "__MAIL_DIGEST_LINEBREAK__"
set targetEmail to "ertugrul@cetinkayalar.com"
set maxContentChars to 40000
set maxSourceChars to 100000
set lookbackDays to 30
set messageReadTimeoutSeconds to 20

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
			-- Resolve the primary Inbox directly. Enumerating the mailbox hierarchy can
			-- force Mail to walk a large folder hierarchy before any message work.
			set primaryMailbox to missing value
			try
				set primaryMailbox to mailbox "INBOX" of theAccount
			on error
				try
					set primaryMailbox to mailbox "Inbox" of theAccount
				on error
					set primaryMailbox to missing value
				end try
			end try

			if primaryMailbox is not missing value then
				set theMailbox to primaryMailbox
				set mailboxName to name of theMailbox

				-- Mail's date-based `whose` query scans all 13k+ messages in a
				-- mailbox. Messages are ordered newest-first, so locate the 30-day
				-- boundary with a binary search and inspect only the recent range.
				set messageCount to count of messages of theMailbox
				set firstNonRecent to messageCount + 1
				set lowIndex to 1
				set highIndex to messageCount
				repeat while lowIndex is less than or equal to highIndex
					set middleIndex to (lowIndex + highIndex) div 2
					if (date received of message middleIndex of theMailbox) > cutoffDate then
						set lowIndex to middleIndex + 1
					else
						set firstNonRecent to middleIndex
						set highIndex to middleIndex - 1
					end if
				end repeat

				set recentCount to firstNonRecent - 1
				if recentCount > 0 then
					set recentMessages to messages 1 thru recentCount of theMailbox
					repeat with theMsg in recentMessages
						try
							set theSubject to subject of theMsg
							if (theSubject contains "meeting" or theSubject contains "toplant" or theSubject contains "görüşme" or ¬
							theSubject contains "gorusme" or theSubject contains "invitation" or theSubject contains "invite" or ¬
							theSubject contains "calendar" or theSubject contains "takvim" or theSubject contains "appointment" or ¬
							theSubject contains "randevu" or theSubject contains "interview" or theSubject contains "mülakat" or ¬
							theSubject contains "mulakat" or theSubject contains "conference" or theSubject contains "konferans" or ¬
							theSubject contains "seminar" or theSubject contains "webinar" or theSubject contains "event" or ¬
							theSubject contains "etkinlik" or theSubject contains "schedule" or theSubject contains "planlama" or ¬
							theSubject contains "davetiye" or theSubject contains "zoom" or theSubject contains "teams" or ¬
							theSubject contains "webex" or theSubject contains "call" or theSubject contains "sync") then
							set receivedDateValue to date received of theMsg
							if receivedDateValue > cutoffDate then
								set theSender to sender of theMsg
								set receivedDate to receivedDateValue as string
								set theContent to ""
								try
									with timeout of messageReadTimeoutSeconds seconds
										set theContent to content of theMsg as string
									end timeout
								on error
									-- A single slow/remote message must not abort the whole digest.
									-- Python can still use the subject and the remaining records.
									set theContent to ""
								end try
								set sourceMessageId to ""
								try
									set sourceMessageId to (message id of theMsg) as string
								end try
								set rawSource to ""
								-- Reading `source` is much slower than reading the body. Only
								-- request it for subjects that strongly suggest a calendar
								-- invitation or when the body already exposes an ICS marker.
								-- Ordinary meeting messages still use the fast semantic parser.
								set needsRawSource to false
								if (theSubject contains "invitation" or theSubject contains "invite" or ¬
									theSubject contains "calendar" or theSubject contains "takvim" or ¬
									theSubject contains "davetiye" or theSubject contains ".ics") then
									set needsRawSource to true
								end if
								if (theContent contains "BEGIN:VCALENDAR" or theContent contains "BEGIN:VEVENT" or ¬
									theContent contains "text/calendar" or theContent contains "METHOD:REQUEST" or ¬
									theContent contains "METHOD:PUBLISH" or theContent contains "METHOD:CANCEL") then
									set needsRawSource to true
								end if
								if needsRawSource is true then
									try
									with timeout of messageReadTimeoutSeconds seconds
										set rawSource to source of theMsg as string
									end timeout
									end try
								end if

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
							end if
							end if
						on error
							-- A broken/remote Mail object must not abort the whole batch.
							-- Continue with the next recent message instead.
						end try
					end repeat
				end if
				end if
			end if
		end repeat

	return finalOutput
end tell
