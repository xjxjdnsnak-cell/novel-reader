---
description: List imported books or select a default Novel Reader book.
---

List imported books or select the default book:

!`python ./bin/novel-reader list --json`

If the user passed `use <book_id>` in `$ARGUMENTS`, run:

!`python ./bin/novel-reader select $ARGUMENTS`

Show a compact numbered table with title, book_id, chapter count, chunk count, and embedding status. If a book is selected, use that book_id by default for later Novel Reader operations.
