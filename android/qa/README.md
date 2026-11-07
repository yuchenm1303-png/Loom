# Testing the real app without a phone

The `qa` build is the real app pointed at a local stack: the real account service and gateway, plus a stand-in computer (`stack.py`). It accepts a pairing code by intent, because an emulator has no camera, and it is the only build that allows plain http.

```bash
cd android && ./gradlew assembleQa
```

```bash
python android/qa/stack.py /tmp/loom-qa
```

Then, with an emulator running:

```bash
APK=app/build/outputs/apk/qa/app-qa.apk bash qa/pairing.sh /tmp/loom-qa /tmp/loom-qa/shots
```

`stack.py` needs `fastapi`, `uvicorn`, `httpx` and `websockets`. Tokens last 60 seconds there, so each round of `pairing.sh` comes back after expiry: a phone must stay paired through all of them with no refused refresh and no revoked session.

To check the other direction, set `revoked_at` on the phone's row in `accounts.db`: the app must return to the pairing screen within about a minute.

## Mobile attachments

The composer accepts multiple images or files (10 MB each, 10 files and 50 MB total per message).
The phone limit leaves room for base64 and JSON within OkHttp's 16 MB outgoing queue.
Files upload serially; each card shows reading, transferring, ready or a retryable error.
Original filenames are retained even when the Host uses a generated temporary filename.
Removing a file or leaving a draft ignores late upload responses and releases thumbnail URLs.

Run the automated queue, bridge and rendered accessibility checks from `desktop-react`:

```bash
node --test scripts/tests/mobile-attachments.test.mjs scripts/tests/mobile-attachment-bridge.test.mjs scripts/tests/mobile-attachment-ui.test.mjs scripts/tests/pending-user-message.test.mjs
npm run typecheck
npm run build:mobile
```

Device acceptance checks (use a real Host; `stack.py` only stubs pairing/thread discovery):

- Select images, a PDF, spreadsheet, archive, empty file and a file with a long Chinese filename. Check thumbnails, file labels, scrolling, individual removal, and sending without text.
- Cancel the picker and reopen it. Select the same file twice. Paste an image into the composer.
- Select a file over 10 MB, exceed 10 files, and exceed 50 MB total. Rejected files must never start reading/uploading; accepted files stay in the queue.
- Disconnect during transfer, reconnect, and retry. Successfully uploaded files must stay ready. Remove an uploading file and navigate away; a late response must not resurrect it in another conversation.
- Tap send repeatedly. Uploading/failed attachments block sending; failed sends preserve the text and attachments. Attachments without text must also send while a task is running.
- Open an image, then press Android Back: only the preview closes. Test keyboard-visible layout, large font settings, and a narrow phone screen.
- Open a sent PDF/document through a compatible installed viewer. Without a matching app, show an error on the file card and allow retry. Unsupported audio/video previews fall back to system opening.
- Verify the file provider is unexported, exposes only `cache/attachments/`, and grants read access only. Downloaded copies older than 24 hours are removed on the next open.
