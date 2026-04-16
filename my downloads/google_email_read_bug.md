# Google Workspace Email Body Read Bug

## Issue
The `google_workspace` tool correctly:
- Connects successfully to Google account
- Lists inbox emails properly
- Returns valid working message_ids
- Works for all other actions (send, draft, calendar, drive)

BUT the `read_email` action **always fails with "message_id is required" error** even when a valid confirmed message_id is passed.

## Root Cause
This is an OAuth permission scope issue on the DecisionsAI side. The full email body read scope is NOT being requested during the Google account connection / reconnect flow.

Inbox listing works, but fetching the actual full email content is missing the required OAuth scope grant.

## Steps to reproduce:
1. Connect / reconnect Google account
2. Run `check_inbox` - works fine, returns email ids
3. Run `read_email` with one of the returned valid ids - always fails with "message_id is required"

## Affected build: current 16 April 2026 build