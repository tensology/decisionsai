# Jev Tool Guard

Use `jev_guard_tool_call` immediately before a consequential tool invocation. Do not use it to bypass an approval already required by the user, platform, or policy.

## Prepare the decision

1. Name the exact tool and action.
2. Summarize material arguments without including secrets.
3. List expected side effects and whether the action is reversible.
4. List safeguards already in place, such as dry runs, scoped targets, backups, or validation.
5. Include applicable user instructions and operational policy.

## Apply the verdict

- `allow`: invoke the tool only within the evaluated scope.
- `confirm`: obtain explicit user confirmation first.
- `review`: inspect arguments, safeguards, or policy, then evaluate again.
- `deny`: do not invoke the tool with the current plan.

If the arguments or target change after evaluation, call the guard again. Never send passwords, provider keys, access tokens, or unnecessary private data to Jev.
