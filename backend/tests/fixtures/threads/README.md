# Sanitized Threads parser fixtures

The three control-post fixtures are anonymized structural snapshots modeled from
the authorized page's accessibility tree. Names, phone numbers, and business
identifiers are replaced before the exact-text assertion; line breaks, emoji,
bullet order, and surrounding DOM relationships are preserved. Other HTML
fixtures are minimal post-page structural variations for quotes, reply context,
link previews, media-only posts, short legal content, and delayed content. They
are anonymized test fixtures, not raw production HTML.

The search-card browser regression for external links is reduced from post
`DO6Kh4DkgQl`, captured on the Oracle `/search?q=drkornilovich` page with
`ru-RU` on 2026-09-28. Its body has multiple paragraphs and external link
labels wrapped in `span[translate=no]` (including `l.threads.com` redirects).
That attribute alone does not identify reply context. The parser now exempts
labels inside external anchors while keeping ambiguous reply context rejected.
The regression runs the search capture script with both English and Russian
browser locales and column labels. Raw HTML and session credentials are not
stored in the repository.
