# Working Rules

- When the direct cause is obvious from source data or configuration, say it plainly first in caps:
  `CAN'T DO THIS BECAUSE OF X.`
- Do not work around, force, patch, reimport, or make a clever system change before stating the simple cause.
- For source-of-truth issues like an Excel row saying `Display=N`, answer from the source first. Only propose fixes after the cause is clear.
- Outbound messages to staff/clients: write as Paul (I not we), mid-chat tone, no preamble — see `.cursor/rules/comms-voice.mdc`.

# Production Server Sudo

- The local file `.env-pass` contains the production server sudo password. Reference the file only. Never read, display, log, quote, commit, copy, or disclose its contents.
- Before using it, confirm `.env-pass` is ignored by Git and has file mode `600`. Stop if either check fails.
- Use it only for a user-authorized production operation that genuinely requires sudo. Supply it through standard input to remote `sudo -S`; never place it in a command argument, environment variable, generated script, or tool output.
- After a production restart, verify `merrypak` and `merrypak_celery` are active and confirm the public site responds successfully.
