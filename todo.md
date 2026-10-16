# TODO

Codes in brackets are the items of `REVIEW_REPORT.md`.

## Done

- [x] Menu bar item (status, pause/resume, stop, quit), voice, language and speed pickers
- [x] Browser extension (`extension/`, Chrome and Brave) with on-page controls and highlighting
- [x] Page text from the browser, so pages behind a login work
- [x] App in /Applications with Dock icon and app icon
- [x] Language and speaking-style settings, friendly voice names, "Play a sample"
- [x] Back / forward one passage
- [x] Services start the app if it is not running (the Chrome extension does not: see E1)
- [x] Reader window: the original text with its paragraphs, current passage highlighted [D4, D5]
- [x] Click anywhere in the text to read from there; the text stays after a read ends [D6]
- [x] Open file (PDF, text, Markdown, HTML, Word, RTF), Dock drop, Open With, Read Clipboard [D3, 4.5]
- [x] PDF: running headers, footers, page numbers, footnotes, captions and contents lines removed; paragraphs rebuilt from line positions; hyphenation (compounds keep their hyphen); ligatures, also those missing from the PDF's text; sentences joined over page breaks; web addresses and [12] marks shown but not spoken; text cached per file; page numbers and "go to page"; text recognition for scans. Checks: `uv run python test_pdf.py` [4.3, B8]
- [x] A file opened again carries on from where it was left [D7, D8 in part]
- [x] English/Spanish detected per passage when Language is Auto-detect [3]
- [x] Errors are visible: status line, menu bar, notification, Chrome bar, Services [B1, B11, B14, D12, S3, S4]
- [x] Voice that fails to load no longer hangs the app; the next read tries again [B1]
- [x] Damaged or outdated config falls back to defaults; written atomically [B2, B3]
- [x] Edit and Window menus, so copy and paste work [B4]
- [x] Last word no longer clipped at speeds other than 1× [B5]
- [x] Sentence ends followed by closing quotes; headings and list items get a pause in documents and pages [B9, B10]
- [x] Second copy of the app says so and exits [B12]; style saved on click-away [B13]
- [x] Log rotated at launch [B15]; `pbs -update` run by the installer [B16]
- [x] Voice, language and style changes apply at once [A8, D11]; finer speeds [A9]; neutral default style [D9]
- [x] Generation speed logged per passage [A1]
- [x] git repository, README [10]; Info.plist keys and document types [P2]; app quit before rebuild [P4]; extension icon [E6]

## Check by ear or by hand (could not be verified automatically)

- [ ] Does output follow headphones connected after launch? [B6] Not fixed: restarting PortAudio before each playback (the report's suggestion) halves the device buffer each time and made the voice robotic after three stream reopenings. If it does not follow, restart PortAudio only when the default device has changed.
- [ ] Clicks at the 1 s streaming seams [A2]
- [ ] Generation speed on long passages: read the "× real time" lines in the log [A1]
- [ ] Paste into the Style box, ⌘O, ⇧⌘V, drop on the Dock icon

## Next: natural Spanish (needs your ears first)

- [ ] Compare Spanish from Qwen Base with a cloned clip, Voxtral `es_*` (now in the Voice menu; 0.8× real time on the M4, see PLAN.md), Chatterbox v3 [2]
- [ ] Voice profiles: one for English, one for Spanish, picked by the detected language [2, D10, P5]
- [ ] Normalisation built from what sounds wrong: numbers, `$12.990`, dates, abbreviations, URLs [4.1]
- [ ] Pronunciation dictionary [4.1]
- [ ] Lower temperature or a fixed seed for steadier prosody [A4]

## Later

- [ ] Audio cache per passage, so back is instant and identical [A3]
- [ ] Pre-buffer if generation falls below real time [A1]; warm-up after load [A5]; unload when idle [A6]
- [ ] "Play a sample" replaces the document in the reader [D7]; Open Recent menu; progress bar and time left [D8]
- [ ] EPUB; drop a file on the window (the Dock icon works)
- [ ] PDF: tables and formulas are read as they come (telling them from text needs a layout model); tagged-PDF structure and the outline are not used (PDFKit does not give the first; the second needs a sections list in the window); text appears only once the whole file is extracted [4.3]
- [ ] Web: read the title first; consent walls on the `/page` fallback [4.4]
- [ ] Chrome: start the app from the extension (URL scheme or native messaging) [E1, B17]; pin the daemon to the extension's ID [E2]; keyboard shortcuts while the focus is inside an embedded frame, and the toolbar button reading a page whose content is in a frame (right-click inside the content works) [E3]; surface background errors [E5]; `chrome.commands` and a toolbar popup [D16, D17]; click a sentence on the page to read from there
- [ ] Menu bar only, SF Symbols icons [D1, D2]; global hotkeys and media keys [D15, A10]; launch at login [D14]; first-run download progress [D13]
- [ ] Export to an audio file [A11]
- [ ] Bundle Python in the app; compiled launcher and signing [P1, P3]
- [ ] Split `tts_daemon.py` into modules [10]

## Known ceilings — fix only when they annoy

- [ ] The model can stop speaking part-way through a paragraph and produce silence without ending (1 run in 10 at temperature 0.3, none in 30 at 0.5–0.9). The app detects it, drops the silence and carries on from the next sentence; the log says "went silent". A repetition penalty of 1.2 prevented it in 10 runs but its effect on the voice is unheard; it is one argument to `model.generate`
- [ ] Highlight follows the paragraph, not the word; the model gives no word timings
- [ ] On-page controls only show in the tab that started the read, and vanish if that tab navigates away
- [ ] Style changes tone but not pace (measured); use Speed for pace
- [ ] No Safari or Firefox extension (Safari needs an Xcode-built wrapper); Services menu and hotkeys work there
- [ ] One paragraph is one passage: back, forward, click-to-read and the highlight all move by paragraph. Paragraphs over 2000 characters are cut at sentence ends; a single sentence longer than that is sent whole
- [ ] PDF paragraphs are rebuilt from line spacing, heights and lengths; a book whose chapters are 1–2 pages long loses "Chapter N" lines as if they were running headers. Footnotes are dropped only when set smaller than the body at the foot of a page; captions only when they start "Figure 3:" / "Tabla 2."
