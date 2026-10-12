# TODO

## Done

- [x] Menu bar item (status, pause/resume, stop, quit)
- [x] Voice picker (menu bar → Voice)
- [x] Speed control (menu bar → Speed)
- [x] Read-along highlighting (floating panel)
- [x] Browser extension (`extension/`, Chrome and Brave)
- [x] Page text from the browser, so pages behind a login work
- [x] Pause/resume
- [x] App in /Applications with a settings window, Dock icon and app icon
- [x] Language and speaking-style settings, friendly voice names, "Play a sample"
- [x] Back / forward one passage
- [x] Chrome: controls and highlighting on the page itself, toolbar button, keyboard shortcuts
- [x] Right-click actions start the app if it is not running

## Known ceilings — fix only when they annoy

- [ ] Highlight follows the passage (1–3 sentences), not the word; the model gives no word timings
- [ ] On-page controls only show in the tab that started the read, and vanish if that tab navigates away
- [ ] Style changes tone but not pace (measured); use Speed for pace
- [ ] No Safari or Firefox extension (Safari needs an Xcode-built wrapper); Services menu and hotkeys work there
- [ ] A voice change takes effect at the next passage, not mid-sentence
- [ ] A sentence longer than ~250 characters is sent to the model whole
