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
