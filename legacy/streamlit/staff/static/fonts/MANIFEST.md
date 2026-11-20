# Kiosk font manifest

Self-hosted so the kiosk works offline. Files come from the pinned fontsource packages on jsdelivr. Verify with `sha256sum -c` on the sha256 column.

| File | Source URL | Exact version | sha256 | License |
| --- | --- | --- | --- | --- |
| `source-sans-3-latin-wght-normal.woff2` | https://cdn.jsdelivr.net/npm/@fontsource-variable/source-sans-3@5.3.1/files/source-sans-3-latin-wght-normal.woff2 | @fontsource-variable/source-sans-3 5.3.1 | `19143dca075972bc84e3fd9eab7416dd40cc565dccb2435a044bdfabe060c7d5` | SIL OFL 1.1 (`OFL-source-sans-3.txt`) |
| `source-sans-3-latin-ext-wght-normal.woff2` | https://cdn.jsdelivr.net/npm/@fontsource-variable/source-sans-3@5.3.1/files/source-sans-3-latin-ext-wght-normal.woff2 | @fontsource-variable/source-sans-3 5.3.1 | `a85a7459bdb3cdc1136751e151a506bae653fc29ada3ca86237477df6f1b59e6` | SIL OFL 1.1 (`OFL-source-sans-3.txt`) |
| `noto-sans-arabic-arabic-wght-normal.woff2` | https://cdn.jsdelivr.net/npm/@fontsource-variable/noto-sans-arabic@5.3.0/files/noto-sans-arabic-arabic-wght-normal.woff2 | @fontsource-variable/noto-sans-arabic 5.3.0 | `ce85091f020920b65762b387b194ef59457ea5b25b760f2dcc35240a94bb8669` | SIL OFL 1.1 (`OFL-noto-sans-arabic.txt`) |
| `noto-sans-arabic-latin-wght-normal.woff2` | https://cdn.jsdelivr.net/npm/@fontsource-variable/noto-sans-arabic@5.3.0/files/noto-sans-arabic-latin-wght-normal.woff2 | @fontsource-variable/noto-sans-arabic 5.3.0 | `fed070abc960c5f214fcb02b7b178cdab751ef7facd79a899f571bc21d38b8f8` | SIL OFL 1.1 (`OFL-noto-sans-arabic.txt`) |
| `OFL-source-sans-3.txt` | https://cdn.jsdelivr.net/npm/@fontsource-variable/source-sans-3@5.3.1/LICENSE | @fontsource-variable/source-sans-3 5.3.1 | `18aabf190848725e2576eefb5c29ba06aac1029d02132252a7f312eac2e50cf3` | SIL OFL 1.1 |
| `OFL-noto-sans-arabic.txt` | https://cdn.jsdelivr.net/npm/@fontsource-variable/noto-sans-arabic@5.3.0/LICENSE | @fontsource-variable/noto-sans-arabic 5.3.0 | `91053c23e8a0fe5fc9b5fdbe5ff74ceffd66f6f996c123f1a6ca4c23487c1fff` | SIL OFL 1.1 |
| `fonts.css` | Written for this directory | n/a | n/a | Project stylesheet |

- `fonts.css` declares Source Sans 3 (Latin, Latin Extended) and Noto Sans Arabic (Arabic, Latin), weights 400 to 700, `font-display: swap`. Unicode ranges are copied from the fontsource `index.css` of the same versions.
- Tabular digits are not set here. Consumers apply `font-variant-numeric: tabular-nums`.
- Only the subsets listed above are shipped. Other scripts fall back to the system font.
