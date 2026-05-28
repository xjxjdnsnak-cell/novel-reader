---
name: novel-books
description: List imported Novel Reader books and optionally select the default book for later Novel Reader operations.
argument-hint: [use <book_id>]
allowed-tools: [Bash]
---

# Novel Reader Books

Use this command when the user wants to see imported books or choose which book to operate on.

## Arguments

The user invoked this with: `$ARGUMENTS`

Supported forms:

- no arguments: list imported books
- `use <book_id>`: select a default book

## Instructions

If no arguments were provided:

```powershell
python ./bin/novel-reader list --json
python ./bin/novel-reader select
```

Then show a compact table:

- number
- title
- book_id
- chapters
- chunks
- embedding status

If a current selection exists, show it at the top.

Ask the user to choose by replying with a book number or by running:

```text
/novel-books use <book_id>
```

If arguments match `use <book_id>`, run:

```powershell
python ./bin/novel-reader select <book_id>
```

Then confirm the selected title and book_id. For later Novel Reader requests in this conversation, use that selected `book_id` by default when the user does not provide one.
