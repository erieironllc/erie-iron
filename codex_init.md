# Codex Init – Ekkolab Webservice

This file defines the trusted commands Codex can execute automatically once you’ve approved them for the current session.

After initial approval, Codex will skip confirmation for these commands for the remainder of the session.

---

## ✅ Notification and UX Helpers

### `./.codex/notify_done.sh`
Spoken and visual alert indicating Codex tasks have finished.

---

## ✅ Utility Commands

### `git status -sb`
Summarizes current repository status concisely.

### `say "codex done"`
Audible confirmation that all tasks in the current checklist are complete (used as fallback if notification fails).

---

## Usage

1. Keep this file at the project root:  
   `./codex_init.md`

2. When starting a new session, simply run `/init`