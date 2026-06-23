# Handy Transcribe — manual UI checklist

Run this once after any change to the desktop UI. Automated tests cover the
backend and the file-dialog filter, but native dialogs and real clicks can only
be verified by hand. Walk it top to bottom; transcribe one real recording end
to end.

Launch the app:

```
~/.venvs/handy-transcribe/bin/python app.py
```

To see JavaScript errors and the console while testing, launch with devtools:

```
HANDY_DEBUG=1 ~/.venvs/handy-transcribe/bin/python app.py
```

If anything below fails silently with no on-screen message, that is itself a
bug — every action should either work or show a status banner.

## Adding files

- [ ] **Add files…** opens a native file picker, filtered to audio/video, and allows multi-select.
- [ ] Picking files opens the **Move / Copy** modal (when default disposition is "ask").
- [ ] Dragging files onto the window highlights the dropzone and adds them.
- [ ] **Copy** keeps the originals where they were; **Move** removes them from the source folder.
- [ ] "Remember my choice" makes future drops skip the modal (check Settings reflects it).
- [ ] The queue card lists the added filenames.
- [ ] **Clear** empties the queue and hides the card.

## Transcribing

- [ ] **Transcribe** shows the progress card; the bar and "Transcribing i / n" text advance.
- [ ] On finish, the results card lists each file with its duration.
- [ ] A `.md` and `.srt` appear in the library's Transcripts folder.
- [ ] An unsupported/corrupt file shows an error (status banner or item error), and the batch continues.

## Retention

- [ ] **Save audio** checkbox per result, **Save all**, and **Save none** all toggle correctly.
- [ ] Saved audio moves to "Audio Archive"; unsaved stays in "Audio Pending" with a delete date.

## Library tab

- [ ] Search by filename and by spoken content returns matching rows.
- [ ] Week filter, **This week**, and **Last week** filter the list.
- [ ] **Open transcript** opens the in-app viewer with the text; close works.
- [ ] **Open SRT**, **Play audio**, and **Reveal** open the right file/Finder.
- [ ] Per-row **Save / Unsave** updates the badge.
- [ ] **Clean expired** removes due audio and reports the count.

## Settings tab

- [ ] **Choose library folder** opens a folder picker and updates the path.
- [ ] Changing default disposition / auto-delete days and clicking **Save** persists (shows "Saved.").
- [ ] Reopening the app preserves the saved settings.
