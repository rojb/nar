"""English texts shown to the user. Keys and placeholders must match messages_es.py."""

from __future__ import annotations

MESSAGES: dict[str, str] = {
    "repl.banner": "nar · {provider} · {model}. Type 'exit' to leave.",
    "warning.prefix": "warning:",
    "approval.needed": "Approval needed: {title}",
    "approval.question": "Allow {tool}? [y]es / [n]o / [a]lways allow {tool} this session: ",
    "approval.session_noted": "{tool} will not ask again in this session",
    "approval.invalid": "Please answer y, n or a.",
    "approval.reason": "Reason (optional, Enter to skip): ",
    "approval.shortened": "(preview shortened)",
    "approval.more_lines.one": "... {count} more line not shown",
    "approval.more_lines.other": "... {count} more lines not shown",
    "trust.header": "This project has tool files in {folder}:",
    "trust.warning": "They run as code with your permissions, as soon as you allow them.",
    "trust.question": "Run these files? [y]es / [n]o: ",
    "key.not_set": "{env} is not set.",
    "key.paste": "Paste your {provider} API key (hidden; Enter to cancel): ",
    "key.save": "Save {env} for your user, for the next time? [y]es / [n]o: ",
    "key.reason.ctrl_v": "Ctrl+V does not paste here: paste with a right-click",
    "key.reason.invisible": "the key has invisible characters; paste it again",
    "key.reason.spaces": "a key has no spaces; paste it again",
    "menu.cant_answer": "{failed} can't answer. Continue with another provider?",
    "menu.stay": "Enter: stay on {failed}",
    "menu.choose": "Choose 1-{count}: ",
    "provider.status.key_set": "key set",
    "provider.status.no_key": "no key: it will ask for one",
    "provider.status.local": "local, no key",
}
