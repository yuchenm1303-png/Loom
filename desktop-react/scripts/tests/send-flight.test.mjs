// Source contracts of the send flight (src/sendFlight.ts, sendFlightMotion.ts, components/send-flight.css). The
// behaviour is held by send-flight-browser.mjs (a real page); these are what a page cannot see: where things are
// wired, what may move, the physics of the capsule (run on its own, it reads nothing from a page), and what must
// never be switched off by a late stylesheet.
import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

const read = (file) => fs.readFileSync(new URL(`../../src/${file}`, import.meta.url), "utf8");
const strip = (css) => css.replace(/\/\*[\s\S]*?\*\//g, "");
const flight = read("sendFlight.ts");
const css = strip(read("components/send-flight.css"));
const compiled = ts.transpileModule(read("sendFlightMotion.ts"), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { capsuleMotion, springStep } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

/** The flights a person makes: a short message close to the field, the common one, a long climb, a wide capsule, a hop. */
const FLIGHTS = [
  { dx: -367, dy: 372, width: 487, height: 44 },
  { dx: -568, dy: 628, width: 286, height: 44 },
  { dx: -300, dy: 90, width: 610, height: 66 },
  { dx: -120, dy: 40, width: 200, height: 44 },
  { dx: -40, dy: 520, width: 330, height: 44 },
  { dx: 200, dy: -300, width: 330, height: 44 },
  { dx: -250, dy: 260, width: 610, height: 132 },
];
const numbers = (text) => String(text).match(/-?\d+(?:\.\d+)?(?:e-?\d+)?/g).map(Number);

test("the flight's stylesheet is the last of the cascade, so what holds the entry back cannot be outranked by a later rule", () => {
  const cascade = read("renderer-styles.ts").split(/\r?\n/).filter((line) => line.startsWith("import "));
  assert.equal(cascade.at(-1), 'import "./components/send-flight.css";');
});

test("everything the flight moves is a transform, a scale, a rotation or an opacity on the compositor", () => {
  const allowed = ["translate", "transform", "scale", "rotate", "opacity"];
  const keyframes = [...flight.matchAll(/\.animate\(\s*\[([\s\S]*?)\]\s*,/g)].map((match) => match[1]);
  assert.ok(keyframes.length >= 4, `found ${keyframes.length} inline animations`);
  for (const frames of keyframes) {
    const properties = new Set([...frames.matchAll(/\b(translate|transform|scale|opacity|rotate|left|top|width|height|margin\w*|padding\w*|clipPath|filter|borderRadius|boxShadow)\s*:/g)].map((match) => match[1]));
    for (const property of properties) assert.ok(allowed.includes(property), `${property} is animated in: ${frames.trim().slice(0, 80)}`);
  }
  // The capsule's own parts come from the physics module, which answers in frames: every property of every frame.
  for (const input of FLIGHTS) {
    const motion = capsuleMotion(input);
    for (const part of ["path", "lean", "shell", "pop", "skin", "settle", "card", "halo", "ring"]) {
      assert.ok(motion[part].length > 20, `${part} has frames`);
      for (const frame of motion[part]) for (const property of Object.keys(frame)) assert.ok(property === "offset" || allowed.includes(property), `${part} animates ${property}`);
    }
  }
  // The only thing written every frame is the ghost's own box (where the message is aimed), and only while it flies.
  assert.match(flight, /ghost\.style\.left = `\$\{entryBox\.left \+ aim\.x\}px`;/);
  assert.match(flight, /ghost\.style\.top = `\$\{finalTop \+ aim\.y\}px`;/);
  const keyframeBlocks = [...css.matchAll(/@keyframes\s+([\w-]+)\s*\{([\s\S]*?)\n\}/g)];
  assert.ok(keyframeBlocks.length >= 2);
  for (const [, name, body] of keyframeBlocks) assert.doesNotMatch(body, /\b(width|height|top|left|margin|padding|grid-template-rows|clip-path|filter|box-shadow)\s*:/, name);
});

test("the capsule starts as the field and ends as the bubble: the first frame is at rest where the text was, the last is at rest where the entry is", () => {
  for (const input of FLIGHTS) {
    const motion = capsuleMotion(input);
    const label = JSON.stringify(input);
    assert.deepEqual(numbers(motion.path[0].translate), [input.dx, input.dy], `${label}: the first frame is the field`);
    assert.deepEqual(numbers(motion.path.at(-1).translate).map(Math.abs), [0, 0], `${label}: the last frame is the entry`);
    for (const part of ["shell", "lean", "skin", "settle", "card", "halo", "ring"]) {
      const first = motion[part][0];
      const last = motion[part].at(-1);
      assert.equal(first.offset, 0);
      assert.equal(last.offset, 1);
      if (part === "shell") {
        for (const frame of [first, last]) assert.deepEqual(numbers(frame.transform).map((value) => Math.round(value * 1e4) / 1e4 || 0), [1, 0, 0, 1, 0, 0], `${label}: the shell is not deformed at rest`);
      }
      if (part === "lean") for (const frame of [first, last]) assert.ok(Math.abs(numbers(frame.rotate)[0]) < 1e-3, `${label}: it does not lean at rest`);
    }
    // What is on show: nothing of the capsule yet (it comes up in the frames that follow, on the text), and at the end the bubble's own fill alone.
    assert.deepEqual([motion.skin[0].opacity, motion.settle[0].opacity, motion.card[0].opacity, motion.halo[0].opacity, motion.ring[0].opacity], [0, 0, 0, 0, 0], label);
    assert.deepEqual([motion.skin.at(-1).opacity, motion.settle.at(-1).opacity, motion.card.at(-1).opacity, motion.halo.at(-1).opacity, motion.ring.at(-1).opacity], [0, 0, 1, 0, 0], label);
  }
});

test("the capsule travels like a body: it leaves at once and without a lurch, arrives in a fraction of a second, overshoots a little and rests", () => {
  for (const input of FLIGHTS) {
    const motion = capsuleMotion(input);
    const label = JSON.stringify(input);
    const frameMs = motion.duration * (motion.path[1].offset - motion.path[0].offset);
    const at = (offset) => numbers(motion.path[Math.round(offset * (motion.path.length - 1))].translate);
    // Distance still to go, along the way, at each frame.
    const left = motion.path.map((frame) => Math.hypot(...numbers(frame.translate)));
    assert.ok(frameMs > 3 && frameMs < 12, `${label}: sampled every ${frameMs} ms`);
    assert.ok(motion.arrive >= 320 && motion.arrive <= 470, `${label}: arrives after ${motion.arrive} ms`);
    assert.ok(motion.duration <= motion.arrive * 1.5 && motion.duration > motion.arrive * 1.2, `${label}: settles in ${motion.duration} ms`);
    // It leaves at once: by the first 16 ms it has moved, and it has not lurched (no frame jumps by more than the speed limit).
    const gone = (ms) => motion.distance - left[Math.round(ms / frameMs)];
    assert.ok(gone(16) > 0.5, `${label}: it has moved by 16 ms (${gone(16).toFixed(2)} px)`);
    assert.ok(gone(16) < 0.025 * motion.distance + 8, `${label}: and not lurched (${gone(16).toFixed(1)} px)`);
    const fastest = Math.max(...left.slice(1).map((value, index) => Math.abs(value - left[index]))) / frameMs * 1000;
    assert.ok(fastest < 5200, `${label}: top speed ${fastest.toFixed(0)} px/s`);
    assert.ok(Math.abs(fastest - motion.peak.speed) < 0.05 * motion.peak.speed + 60, `${label}: the stated peak speed (${motion.peak.speed.toFixed(0)}) is the real one (${fastest.toFixed(0)})`);
    assert.ok(motion.peak.at > 40 && motion.peak.at < 0.5 * motion.arrive, `${label}: fastest early (${motion.peak.at.toFixed(0)} ms)`);
    // It has all but arrived when it first touches down, and what is left is a few pixels of overshoot and rest.
    assert.ok(left[Math.round(motion.arrive / frameMs)] < 0.03 * motion.distance + 6, `${label}: at touchdown ${left[Math.round(motion.arrive / frameMs)].toFixed(1)} px are left`);
    assert.ok(motion.overshoot.x <= 6 && motion.overshoot.y <= 6, `${label}: overshoot ${JSON.stringify(motion.overshoot)}`);
    assert.ok(motion.overshoot.x + motion.overshoot.y > 0.2, `${label}: it does overshoot, a little: that is what makes it settle instead of stop`);
    assert.ok(Math.hypot(...at(0.97)) < 1.2, `${label}: at 97% of the time it is within a pixel of rest`);
    // No axis ever turns back on itself except in that overshoot: the way from the field to the entry is one way.
    for (const axis of [0, 1]) {
      const sign = Math.sign(numbers(motion.path[0].translate)[axis]);
      if (!sign) continue;
      const reachedAt = motion.path.findIndex((frame) => numbers(frame.translate)[axis] * sign <= 0);
      for (let index = 1; index < (reachedAt < 0 ? motion.path.length : reachedAt); index += 1) {
        assert.ok(Math.abs(numbers(motion.path[index].translate)[axis]) <= Math.abs(numbers(motion.path[index - 1].translate)[axis]) + 1e-6, `${label}: axis ${axis} only approaches before it arrives`);
      }
    }
  }
});

test("the capsule's shell is elastic and not wild: it stretches as it leaves, squashes as it lands, leans a little, and every frame follows the last smoothly", () => {
  for (const input of FLIGHTS) {
    const motion = capsuleMotion(input);
    const label = JSON.stringify(input);
    assert.ok(motion.strain.stretch > 0.01 && motion.strain.stretch <= 0.08, `${label}: stretch ${(motion.strain.stretch * 100).toFixed(1)}%`);
    assert.ok(motion.strain.squash > 0.02 && motion.strain.squash <= 0.09, `${label}: squash ${(motion.strain.squash * 100).toFixed(1)}%`);
    assert.ok(motion.strain.squash * input.height <= 5, `${label}: a tall bubble is not flattened by more than a few pixels (${(motion.strain.squash * input.height).toFixed(1)} px)`);
    const lean = motion.lean.map((frame) => numbers(frame.rotate)[0]);
    assert.ok(Math.max(...lean.map(Math.abs)) <= 2, `${label}: leans at most ${Math.max(...lean.map(Math.abs)).toFixed(2)}°`);
    assert.ok(new Set(lean.map(Math.sign).filter(Boolean)).size <= 1, `${label}: it leans one way only`);
    // Smoothness: nothing a frame later is far from where it was (a 60 Hz display would show it as a jump).
    const step = (frames, key, limit, what) => {
      for (let index = 1; index < frames.length; index += 1) {
        const before = numbers(frames[index - 1][key]);
        const after = numbers(frames[index][key]);
        for (let part = 0; part < before.length; part += 1) assert.ok(Math.abs(after[part] - before[part]) <= limit, `${label}: ${what} jumps by ${Math.abs(after[part] - before[part])} at ${index}`);
      }
    };
    step(motion.shell, "transform", 0.02, "the shell");
    step(motion.lean, "rotate", 0.15, "the lean");
    step(motion.halo, "translate", 3, "the halo");
    step(motion.pop, "scale", 0.02, "the pop");
    step(motion.skin.slice(0, Math.round(motion.arrive / motion.duration * (motion.skin.length - 1))), "opacity", 0.26, "the skin coming up");
    step(motion.settle, "opacity", 0.25, "the settle");
    step(motion.card, "opacity", 0.2, "the card");
    // The skin gives way in order: the pill until the shell has relaxed into the bubble's own corners, which are in
    // before the pill goes (so nothing dips), and the bubble's own fill takes over from them last.
    const opacity = (frames, ms) => frames[Math.round((ms / motion.duration) * (frames.length - 1))].opacity;
    const tail = motion.duration - motion.arrive;
    const later = (share) => motion.arrive + share * tail;
    assert.ok(opacity(motion.skin, 60) > 0.99 && opacity(motion.skin, motion.arrive) > 0.99, `${label}: the skin is whole from 60 ms to touchdown`);
    assert.ok(opacity(motion.settle, motion.arrive - 40) < 0.01, `${label}: the bubble's own corners wait for touchdown`);
    assert.ok(opacity(motion.settle, later(0.31)) > 0.99, `${label}: and are whole before the pill goes (${opacity(motion.settle, later(0.31))})`);
    assert.ok(opacity(motion.skin, later(0.27)) > 0.99 && opacity(motion.skin, later(0.47)) < 0.01, `${label}: the pill goes once they cover it`);
    // The pill is removed only while the corners above it are whole: what is taken away is never seen, and nothing dips.
    motion.skin.forEach((frame, index) => {
      if (frame.offset * motion.duration > motion.arrive && frame.opacity < 0.999 && frame.opacity > 0) assert.ok(motion.settle[index].opacity > 0.99, `${label}: the pill is leaving at ${Math.round(frame.offset * motion.duration)} ms while the corners are at ${motion.settle[index].opacity}`);
    });
    assert.ok(opacity(motion.card, later(0.1)) < 0.01 && opacity(motion.card, later(0.95)) > 0.99, `${label}: the bubble's own fill takes over from the material`);
    assert.ok(opacity(motion.halo, 60) > 0.6 && opacity(motion.halo, later(0.85)) < 0.02, `${label}: the shadow is there while it is in the air and gone when it is down`);
    assert.ok(opacity(motion.settle, motion.duration - 1) < 0.01 && opacity(motion.skin, motion.duration - 1) === 0, `${label}: and all of it is over before the end`);
  }
});

test("the spring the paths are made of is a damped one: it starts at rest, overshoots as little as asked, and settles", () => {
  const w = 12;
  const z = 0.84;
  assert.equal(springStep(0, w, z), 0);
  assert.ok(springStep(0.005, w, z) < 0.005, "it starts from rest (no instant kick)");
  const samples = Array.from({ length: 2000 }, (_, index) => springStep(index / 1000, w, z));
  const overshoot = Math.max(...samples) - 1;
  assert.ok(overshoot > 0.005 && overshoot < 0.012, `overshoots by ${(overshoot * 100).toFixed(2)}%`);
  assert.ok(Math.abs(samples.at(-1) - 1) < 1e-3, "and is back at 1");
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
  const confirmed = app.search(/const confirmedTurnRunning = (?:useMemo\(\(\) => )?Boolean\(/);
  const running = app.search(/const transcriptRunning = (?:useMemo\(\(\) => )?confirmedTurnRunning/);
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
