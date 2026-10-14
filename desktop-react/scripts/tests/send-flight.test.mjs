// Source contracts of the send flight (src/sendFlight.ts, components/send-flight.css). The behaviour is held by
// send-flight-browser.mjs (a real page); these are what a page cannot see: where things are wired, what may move, and
// what must never be switched off by a late stylesheet.
import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const read = (file) => fs.readFileSync(new URL(`../../src/${file}`, import.meta.url), "utf8");
const strip = (css) => css.replace(/\/\*[\s\S]*?\*\//g, "");
const flight = read("sendFlight.ts");
const css = strip(read("components/send-flight.css"));

test("the flight's stylesheet is the last of the cascade, so what holds the entry back cannot be outranked by a later rule", () => {
  const cascade = read("renderer-styles.ts").split(/\r?\n/).filter((line) => line.startsWith("import "));
  assert.equal(cascade.at(-1), 'import "./components/send-flight.css";');
});

test("everything the flight moves is a transform, a scale or an opacity on the compositor", () => {
  const keyframes = [...flight.matchAll(/\.animate\(\s*\[([\s\S]*?)\]\s*,/g)].map((match) => match[1]);
  assert.ok(keyframes.length >= 8, `found ${keyframes.length} animations`);
  for (const frames of keyframes) {
    const properties = new Set([...frames.matchAll(/\b(translate|transform|scale|opacity|rotate|left|top|width|height|margin\w*|padding\w*|clipPath|filter)\s*:/g)].map((match) => match[1]));
    for (const property of properties) assert.ok(["translate", "transform", "scale", "opacity"].includes(property), `${property} is animated in: ${frames.trim().slice(0, 80)}`);
  }
  // The only thing written every frame is the ghost's own box (where the message is aimed), and only while it flies.
  assert.match(flight, /ghost\.style\.left = `\$\{entryBox\.left \+ aim\.x\}px`;/);
  assert.match(flight, /ghost\.style\.top = `\$\{finalTop \+ aim\.y\}px`;/);
  const keyframeBlocks = [...css.matchAll(/@keyframes\s+([\w-]+)\s*\{([\s\S]*?)\n\}/g)];
  assert.ok(keyframeBlocks.length >= 3);
  for (const [, name, body] of keyframeBlocks) assert.doesNotMatch(body, /\b(width|height|top|left|margin|padding|grid-template-rows|clip-path|filter|box-shadow)\s*:/, name);
});

test("a message flies only when motion is allowed, with a window to see it in, and says why when it does not", () => {
  for (const name of ["recordSendOrigin", "recordCardOrigin", "launchSendFlight"]) {
    const body = flight.slice(flight.indexOf(`export function ${name}(`));
    assert.match(body.slice(0, 700), /flightsAllowed\(\)/, `${name} asks whether a flight may be made`);
  }
  const allowed = flight.slice(flight.indexOf("function flightsAllowed()"));
  assert.match(allowed.slice(0, 200), /prefersReducedMotionNow\(\)/, "the motion preference");
  assert.match(allowed.slice(0, 200), /document\.hidden/, "a window that is shown");
  assert.match(allowed.slice(0, 200), /pageIsCssZoomed\(\)/, "a page that is not scaled with CSS zoom (the web fallback)");
  for (const reason of ["message too tall to fly", "already in place", "no text to anchor on", "entry has no width"]) assert.ok(flight.includes(`"${reason}"`), reason);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
  assert.match(css, /:root\[data-loom-reduced-motion="true"\]/);
});

test("the composers say where the text is before they clear it, and the transcript asks for the flight when an entry is born", () => {
  const base = read("components/ComposerBase.tsx");
  assert.ok(base.indexOf("recordSendOrigin(textareaRef.current)") > 0);
  assert.ok(base.indexOf("recordSendOrigin(textareaRef.current)") < base.indexOf("const request = onSend("), "recorded before onSend");
  assert.ok(base.indexOf("const request = onSend(") < base.indexOf('setValue("")'), "and before the field is cleared");
  const steering = read("components/Composer.tsx");
  assert.ok(steering.indexOf('recordSendOrigin(textareaRef.current, "steer")') > 0);
  assert.ok(steering.indexOf('recordSendOrigin(textareaRef.current, "steer")') < steering.indexOf("request = Promise.resolve(onSend("));
  const transcript = read("components/Transcript.tsx");
  assert.match(transcript, /launchSendFlight\(entry\)/);
  assert.match(transcript, /launchSendFlight\(entry, "steer"\)/);
  assert.match(transcript, /ref=\{isSteeringUserMessage\(block\.item\) && block\.item\.optimistic === true \? steeringEntryRef : undefined\}/);
  assert.match(transcript, /recordCardOrigin\(event\.currentTarget\)/);
  // Once per mount, and an unmount takes the flight with it.
  assert.match(transcript, /return \(\) => endSendFlight\(entry\);/);
});

test("the turn that has just been sent is live from the click, and no other turn can be taken for it", () => {
  const app = read("App.tsx");
  const confirmed = app.indexOf("const confirmedTurnRunning = Boolean(");
  const running = app.indexOf("const transcriptRunning = confirmedTurnRunning");
  assert.ok(confirmed > 0 && running > confirmed);
  const block = app.slice(running, running + 260);
  // A message sent from the home screen waits for thread/start before the app's own busy state is set: the sending
  // message itself is what makes the turn live, not that flag.
  assert.doesNotMatch(block, /loom\.turnActive/);
  assert.match(block, /item\.type === "user_message" && item\.status === "sending"/);
  // Before turn/started, currentTurnId still names the previous turn: it is withheld until the Host has confirmed one.
  assert.match(app, /currentTurnId=\{confirmedTurnRunning \? thread\?\.currentTurnId : undefined\}/);
});

test("the real entry is held back, its capsule row is reserved, and nothing else styles the flight", () => {
  assert.match(css, /\.entry-user_message\[data-flight="flying"\] \{\s*visibility: hidden;/);
  assert.match(css, /\.turn-block\[data-flight="flying"\] \.pending-thinking-presence\[data-motion-phase\] \{\s*grid-template-rows: 1fr;\s*opacity: 0;\s*transition: none;/);
  assert.match(css, /\.entry-user_message\[data-flight\] \{\s*animation: none !important;/);
  // One owner: no other stylesheet names the flight's classes or attribute.
  for (const name of fs.readdirSync(new URL("../../src/components/", import.meta.url)).filter((file) => file.endsWith(".css") && file !== "send-flight.css")) {
    const other = strip(read(`components/${name}`));
    assert.doesNotMatch(other, /send-flight|data-flight/, name);
  }
  // The home screen is copied whole, and nothing in the copy may play again.
  assert.match(css, /\.send-flight-home \*\s*\{[^}]*animation: none !important;/);
});

test("the flight keeps what it needs to be undone: it lands, cancels with its entry, and leaves nothing behind", () => {
  for (const needle of ["flights.delete(entry)", "ghost.remove()", "cancelAnimationFrame(tracker)", "window.clearTimeout(guard)", "window.clearTimeout(releaseTimer)", "queueMicrotask(", "if (!entry.isConnected) flights.get(entry)?.cancel();"]) {
    assert.ok(flight.includes(needle), needle);
  }
  // A paused or throttled page cannot strand the real entry: a safety net lands the flight a little after it should have.
  assert.match(flight, /const guard = window\.setTimeout\(land, duration \+ \d+\);/);
});
