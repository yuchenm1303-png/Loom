#!/usr/bin/env bash
# Does a paired phone stay paired? Pairs the qa build for real, then lets its access token
# expire and comes back the ways a person does. Needs android/qa/stack.py and an emulator.
#   android/qa/pairing.sh <stack workdir> <shot dir>
set -u
WORK=$1; SHOTS=$2; ROUNDS=${ROUNDS:-4}
ADB=${ADB:-adb}
PACKAGE=com.smirel.loom.qa
ACTIVITY=$PACKAGE/com.smirel.loom.MainActivity
mkdir -p "$SHOTS"

state() {
  python_cmd=${PY:-python}
  "$python_cmd" - "$WORK/db.json" <<'EOF'
import json, sys
rows = json.load(open(sys.argv[1], encoding="utf-8"))
print(f"sessions={len(rows)} revoked={sum(1 for row in rows if row['revoked'])}")
EOF
}
events() { "$ADB" exec-out run-as "$PACKAGE" cat shared_prefs/loom_connection.xml 2>/dev/null | tr -d '\r' | sed 's/&quot;/"/g' | grep -oE '"detail":"[^"]*","event":"[a-z-]+"|"event":"[a-z-]+","detail":"[^"]*"|"event":"[a-z-]+"' | sed -E 's/.*"event":"([a-z-]+)".*/\1/' | tail -6 | tr '\n' ' '; echo; }
paired() { "$ADB" exec-out run-as "$PACKAGE" cat shared_prefs/loom_session.xml 2>/dev/null | grep -c 'name="session"'; }

"$ADB" uninstall "$PACKAGE" >/dev/null 2>&1
"$ADB" install -r "$APK" | tail -1
rm -f "$WORK/code.txt"; touch "$WORK/pair"
for _ in $(seq 1 40); do [ -f "$WORK/code.txt" ] && break; sleep 0.5; done
code=$(cat "$WORK/code.txt")
"$ADB" shell "am start -n $ACTIVITY --es qa_pair '$code'" | tail -1
sleep 9
"$ADB" exec-out screencap -p >"$SHOTS/paired.png"
echo "after pairing: paired=$(paired) $(state)"
events

for round in $(seq 1 "$ROUNDS"); do
  sleep 68   # the access token has expired by now
  case $((round % 2)) in
    1) "$ADB" shell am force-stop "$PACKAGE" ;;
    0) "$ADB" shell input keyevent KEYCODE_HOME; sleep 2 ;;
  esac
  "$ADB" shell am start -n "$ACTIVITY" >/dev/null
  sleep 8
  "$ADB" exec-out screencap -p >"$SHOTS/round-$round.png"
  echo "round $round: paired=$(paired) $(state)"
  events
done
