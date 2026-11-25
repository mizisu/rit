### Background

Document descriptions can be hard to read in a narrow terminal.

Cause: The description keeps its original line width when the terminal shrinks, so the ends of long sentences and API paths can disappear beyond the right edge.

`GET /api/example/documents/changes?include=summary&sort=updated`

- On wide screens, author details appear beside the document.
- On narrow screens, the same details appear below the document.
- Readers should not need to scroll sideways in either layout.

### Changes

The description layout now follows these rules.

- To open a document:
  1. Select a document from the list.
  2. Read the changes in the description.
  3. Move to the next document.
- Place the first paragraph below the heading.
- Leave a blank line between paragraphs.
- Align list markers with the first line of each item.

### Review checklist

Resize the terminal and check the following:

- The heading and body share a left edge.
- Long sentences wrap without losing characters.
- The last character of the API path stays visible.
- Nested list numbers line up in one column.
- The selected line stays visible after scrolling.

### Notes

This example uses only local documents.

- Keep the terminal height fixed.
- Measure text width in terminal cells.
- Keep numbered instructions short.
- Follow the last item with a regular paragraph.

Restore the original terminal size when finished.
