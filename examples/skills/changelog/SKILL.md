---
name: changelog
description: Write or update CHANGELOG.md entries from recent git commits. Use when the user asks for a changelog, release notes or "what changed".
---

# Writing a changelog entry

1. Find the last release: run `git describe --tags --abbrev=0`. If there is no tag, use the
   whole history.
2. List what changed since then with `git log <tag>..HEAD --oneline`.
3. Group the commits by their Conventional Commit type, using the headings in
   [`template.md`](template.md) (read that file from this skill's folder).
4. Write one short, user-facing line per change. Leave out `chore`, `ci` and `test` commits
   unless they change behavior.
5. Put the new section at the top of `CHANGELOG.md`, below the title. Create the file if it does
   not exist.

Show the entry to the user before saving it.
