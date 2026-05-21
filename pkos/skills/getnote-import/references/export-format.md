# GetNote HTML Export Format

Reference for `import_getnote_export.py`. Verified against a real export archive
(2026-05, ~7100 notes).

## Archive layout

```
<export-dir>/
  <archive-name>/
    index.html              note list (not parsed by the importer)
    notes/
      <hash>.html           one file per note; <hash> = the getnote note id
      files/
        style.css
        script.js           display-only; does NOT decrypt jsonData
        <hash>              image blobs (no extension)
```

## Per-note HTML

Each `notes/<hash>.html` carries the note in its visible markup:

```html
<head><title>NOTE TITLE OR EMPTY</title></head>
<body>
<div id="jsonData" data-json="<base64 ciphertext>"></div>   <!-- encrypted; ignored -->
<div class="note-container">
  <div class="note">
    <h1>NOTE TITLE</h1>                          <!-- present only on titled notes -->
    <p>创建于：2025-03-24 13:19:36</p>
    <p>标签：
      <span class="tag">TAG A</span>
      <span class="tag">TAG B</span>
    </p>
    <hr>
    <div class="attachment">原文：<a href="URL">LINK TEXT</a></div>   <!-- link notes -->
    <p>...body summary / reflection / knowledge (rendered Markdown)...</p>
    <blockquote><p>...摘抄 / 原文 excerpt...</p></blockquote>          <!-- excerpt notes -->
  </div>
</div>
</body>
```

## Key facts

- **`jsonData` is dead data.** The export's own `script.js` never reads it. The
  visible HTML is the only source of truth — and it lacks the API `note_type`
  field, so note type is inferred from structure.
- **Title is often empty** (`<title></title>`, no `<h1>`) for quick captures and
  book excerpts — roughly 5100 of ~7100 notes. The importer derives a heading from
  the first body line in that case.
- **`<blockquote>`** holds the 摘抄 / 原文 excerpt (~4800 notes). A non-blockquote
  body `<p>` alongside it is getnote's summary/划重点.
- **`<div class="attachment">原文：`** marks a link note (~680 notes) — body is an
  AI summary of an external article.
- **Entities are double-escaped** in places (`&amp;amp;` for a literal `&`); the
  importer unescapes repeatedly.
- **Image / audio**: `<img>` blobs and mp3 attachments are ignored (text-only
  policy). An image-only note with no text is skipped.
