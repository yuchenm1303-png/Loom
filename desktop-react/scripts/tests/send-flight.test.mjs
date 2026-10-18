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
/** What the capsule is made of: one part for each thing that moves, and nothing else (a new effect has to be argued for here). */
const PARTS = ["path", "lean", "shell", "pop", "skin", "card", "halo"];
const numbers = (text) => String(text).match(/-?\d+(?:\.\d+)?(?:e-?\d+)?/g).map(Number);
/** How much of its way the body has covered at each frame: 0 at the field, 1 at its place (and a hair over while it overshoots). */
const covered = (motion, input) => {
  const distance = Math.hypot(input.dx, input.dy);
  return motion.path.map((frame) => {
    const [x, y] = numbers(frame.translate);
    return ((x - input.dx) * (-input.dx / distance) + (y - input.dy) * (-input.dy / distance)) / distance;
  });
};
/** The value of a part's opacity at a time (ms), the frames being spread evenly over the whole. */
const at = (motion, frames, ms) => frames[Math.min(frames.length - 1, Math.round((ms / motion.duration) * (frames.length - 1)))].opacity;

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
    for (const part of PARTS) {
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

test("a thing that is picked up, carried and put down: no second effect (no impact, no ripple, no glow of its own)", () => {
  const motion = capsuleMotion(FLIGHTS[0]);
  const made = Object.keys(motion).filter((key) => Array.isArray(motion[key]) && motion[key].length && typeof motion[key][0] === "object");
  assert.deepEqual(made.sort(), [...PARTS].sort(), "the parts the physics makes");
  // And the DOM builds exactly those: a shell of shadow, skin and card, and the text.
  assert.match(flight, /membrane\.append\(halo, skin, card\);/);
  for (const gone of ["send-flight-settle", "send-flight-ring"]) assert.ok(!(flight + css).includes(gone), `${gone} is not part of the flight`);
  const physics = read("sendFlightMotion.ts").replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
  assert.doesNotMatch(physics, /squash|\bring\b|\bsettle\b/, "the physics has no impact in it");
  // The accent is a glint in the hairline, not a light: nothing of the capsule is a gradient of it.
  assert.doesNotMatch(css.match(/\.send-flight-halo\s*\{[^}]*\}/)?.[0] ?? "", /accent/, "the shadow is neutral");
});

test("the capsule starts as the field and ends as the bubble: the first frame is at rest where the text was, the last is at rest where the entry is", () => {
  for (const input of FLIGHTS) {
    const motion = capsuleMotion(input);
    const label = JSON.stringify(input);
    assert.deepEqual(numbers(motion.path[0].translate), [input.dx, input.dy], `${label}: the first frame is the field`);
    assert.deepEqual(numbers(motion.path.at(-1).translate).map(Math.abs), [0, 0], `${label}: the last frame is the entry`);
    for (const part of ["shell", "lean", "skin", "card", "halo"]) {
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
    assert.deepEqual([motion.skin[0].opacity, motion.card[0].opacity, motion.halo[0].opacity], [0, 0, 0], label);
    assert.deepEqual([motion.skin.at(-1).opacity, motion.card.at(-1).opacity, motion.halo.at(-1).opacity], [0, 1, 0], label);
    assert.deepEqual([motion.lift[0], motion.lift.at(-1)], [0, 0], `${label}: it is on the page at both ends`);
  }
});

test("the capsule travels like a body: it leaves at once and without a lurch, arrives in a fraction of a second, and comes to rest without a bounce", () => {
  for (const input of FLIGHTS) {
    const motion = capsuleMotion(input);
    const label = JSON.stringify(input);
    const frameMs = motion.duration * (motion.path[1].offset - motion.path[0].offset);
    const frame = (offset) => numbers(motion.path[Math.round(offset * (motion.path.length - 1))].translate);
    // Distance still to go, along the way, at each frame.
    const left = motion.path.map((item) => Math.hypot(...numbers(item.translate)));
    assert.ok(frameMs > 3 && frameMs < 12, `${label}: sampled every ${frameMs} ms`);
    assert.ok(motion.arrive >= 320 && motion.arrive <= 470, `${label}: arrives after ${motion.arrive} ms`);
    assert.ok(motion.duration <= motion.arrive * 1.3 && motion.duration > motion.arrive * 1.1, `${label}: comes to rest in ${motion.duration} ms`);
    // It leaves at once: by the first 16 ms it has moved, and it has not lurched (no frame jumps by more than the speed limit).
    const gone = (ms) => motion.distance - left[Math.round(ms / frameMs)];
    assert.ok(gone(16) > 0.5, `${label}: it has moved by 16 ms (${gone(16).toFixed(2)} px)`);
    assert.ok(gone(16) < 0.025 * motion.distance + 8, `${label}: and not lurched (${gone(16).toFixed(1)} px)`);
    const fastest = Math.max(...left.slice(1).map((value, index) => Math.abs(value - left[index]))) / frameMs * 1000;
    assert.ok(fastest < 5200, `${label}: top speed ${fastest.toFixed(0)} px/s`);
    assert.ok(Math.abs(fastest - motion.peak.speed) < 0.05 * motion.peak.speed + 60, `${label}: the stated peak speed (${motion.peak.speed.toFixed(0)}) is the real one (${fastest.toFixed(0)})`);
    assert.ok(motion.peak.at > 40 && motion.peak.at < 0.5 * motion.arrive, `${label}: fastest early (${motion.peak.at.toFixed(0)} ms)`);
    // It has all but arrived when it first reaches its place, and what is left is a hair of overshoot, if any, and rest.
    assert.ok(left[Math.round(motion.arrive / frameMs)] < 0.03 * motion.distance + 6, `${label}: at arrival ${left[Math.round(motion.arrive / frameMs)].toFixed(1)} px are left`);
    assert.ok(motion.overshoot.x <= 2 && motion.overshoot.y <= 2, `${label}: overshoot ${JSON.stringify(motion.overshoot)}: it is put down, it does not bounce`);
    assert.ok(Math.hypot(...frame(0.97)) < 1.2, `${label}: at 97% of the time it is within a pixel of rest`);
    // No axis ever turns back on itself except in that overshoot: the way from the field to the entry is one way.
    for (const axis of [0, 1]) {
      const sign = Math.sign(numbers(motion.path[0].translate)[axis]);
      if (!sign) continue;
      const reachedAt = motion.path.findIndex((item) => numbers(item.translate)[axis] * sign <= 0);
      for (let index = 1; index < (reachedAt < 0 ? motion.path.length : reachedAt); index += 1) {
        assert.ok(Math.abs(numbers(motion.path[index].translate)[axis]) <= Math.abs(numbers(motion.path[index - 1].translate)[axis]) + 1e-6, `${label}: axis ${axis} only approaches before it arrives`);
      }
    }
  }
});

test("the shell is a little elastic and not wild: larger while it is lifted, longer as it leaves, a hint of a lean, and every frame follows the last smoothly", () => {
  for (const input of FLIGHTS) {
    const motion = capsuleMotion(input);
    const label = JSON.stringify(input);
    assert.ok(motion.strain.stretch > 0.003 && motion.strain.stretch <= 0.04, `${label}: stretch ${(motion.strain.stretch * 100).toFixed(1)}%`);
    assert.ok(motion.strain.grow > 0.004 && motion.strain.grow <= 0.03, `${label}: it is ${(motion.strain.grow * 100).toFixed(1)}% larger while lifted`);
    assert.ok(motion.strain.grow * input.width <= 8, `${label}: a wide capsule grows by a few pixels, not a few percent of its width`);
    const lean = motion.lean.map((item) => numbers(item.rotate)[0]);
    assert.ok(Math.max(...lean.map(Math.abs)) <= 1.2, `${label}: leans at most ${Math.max(...lean.map(Math.abs)).toFixed(2)}°`);
    assert.ok(new Set(lean.map(Math.sign).filter(Boolean)).size <= 1, `${label}: it leans one way only`);
    // Smoothness: nothing a frame later is far from where it was (a 60 Hz display would show it as a jump).
    const step = (frames, key, limit, what) => {
      for (let index = 1; index < frames.length; index += 1) {
        const before = numbers(frames[index - 1][key]);
        const after = numbers(frames[index][key]);
        for (let part = 0; part < before.length; part += 1) assert.ok(Math.abs(after[part] - before[part]) <= limit, `${label}: ${what} jumps by ${Math.abs(after[part] - before[part])} at ${index}`);
      }
    };
    step(motion.shell, "transform", 0.01, "the shell");
    step(motion.lean, "rotate", 0.12, "the lean");
    step(motion.halo, "translate", 3, "the shadow");
    step(motion.halo, "opacity", 0.2, "the shadow's strength");
    step(motion.pop, "scale", 0.02, "the pop");
    step(motion.skin, "opacity", 0.5, "the skin");
    step(motion.card, "opacity", 0.2, "the card");
  }
});

test("it is picked up as it leaves and put down as it arrives: its height above the page is the shadow, and it touches down as it stops", () => {
  for (const input of FLIGHTS) {
    const motion = capsuleMotion(input);
    const label = JSON.stringify(input);
    const way = covered(motion, input);
    const ms = (index) => (index / (motion.path.length - 1)) * motion.duration;
    assert.ok(motion.lift.every((value) => value >= 0 && value <= 1), `${label}: the height stays between the page and 1`);
    assert.ok(at(motion, motion.halo, 120) > 0.9, `${label}: it is in the air by 120 ms (${at(motion, motion.halo, 120)})`);
    // In the air for the first half of its way, whatever else it is doing...
    motion.lift.forEach((value, index) => { if (way[index] < 0.55 && ms(index) > 60) assert.ok(value > 0.9, `${label}: lifted at ${Math.round(ms(index))} ms (${value}), in the first half of its way`); });
    // ...and on the page again by the time it has nearly stopped: set down exactly as it arrives, never before and never after.
    motion.lift.forEach((value, index) => { if (way[index] >= 0.985) assert.ok(value < 0.03, `${label}: still lifted (${value}) with ${(100 * (1 - way[index])).toFixed(1)}% of the way to go`); });
    motion.lift.forEach((value, index) => { if (way[index] <= 0.62 && ms(index) > 60) assert.ok(value > 0.85, `${label}: put down too early (${value}) with ${(100 * (1 - way[index])).toFixed(0)}% of the way to go`); });
    // It goes up once and comes down once.
    const peak = motion.lift.indexOf(Math.max(...motion.lift));
    for (let index = peak + 1; index < motion.lift.length; index += 1) assert.ok(motion.lift[index] <= motion.lift[index - 1] + 1e-3, `${label}: the height rises again at ${Math.round(ms(index))} ms`);
    // The shadow is that height, and nothing else.
    motion.halo.forEach((frame, index) => assert.equal(frame.opacity, motion.lift[index], `${label}: the shadow is the height`));
  }
});

test("the material is the height's: lifted and whole in the air, and gives way to the bubble's own fill as it is put down, never hollow and never back", () => {
  for (const input of FLIGHTS) {
    const motion = capsuleMotion(input);
    const label = JSON.stringify(input);
    const way = covered(motion, input);
    const ms = (index) => (index / (motion.path.length - 1)) * motion.duration;
    motion.skin.forEach((frame, index) => {
      const card = motion.card[index].opacity;
      if (way[index] <= 0.75 && ms(index) > motion.duration * 0.1) assert.ok(frame.opacity > 0.995, `${label}: the skin is whole at ${Math.round(ms(index))} ms (${frame.opacity})`);
      if (way[index] <= 0.7) assert.equal(card, 0, `${label}: the bubble's own fill waits (${Math.round(ms(index))} ms)`);
      // The pill is a solid thing: once it has come up, there is never a moment when what is left of it and of the bubble's fill leaves the page showing through.
      if (ms(index) > 40) assert.ok(frame.opacity + card >= 0.999, `${label}: hollow at ${Math.round(ms(index))} ms: skin ${frame.opacity} + card ${card}`);
      if (way[index] >= 0.985) assert.deepEqual([frame.opacity, card], [0, 1], `${label}: only the bubble's own fill is left (${Math.round(ms(index))} ms)`);
    });
    // One way only: once the pill starts to give way it does not come back, and the bubble's fill does not go.
    const from = Math.round(motion.skin.length * 0.2);
    for (let index = from + 1; index < motion.skin.length; index += 1) {
      assert.ok(motion.skin[index].opacity <= motion.skin[index - 1].opacity + 1e-3, `${label}: the pill comes back at ${Math.round(ms(index))} ms`);
      assert.ok(motion.card[index].opacity >= motion.card[index - 1].opacity - 1e-3, `${label}: the bubble's fill goes at ${Math.round(ms(index))} ms`);
    }
    // It gives way when it is low and slow, and not before: the pill is the shape of the flight, and it is gone by arrival.
    assert.ok(at(motion, motion.skin, motion.arrive * 0.97) < 0.02, `${label}: no pill left at arrival`);
  }
});

test("a message laid out another way changes layout behind the capsule that comes up round it, in a few frames and never as two whole layouts at once", () => {
  const range = (name) => {
    const match = flight.match(new RegExp(`const ${name}: \\[number, number\\] = \\[(\\d+), (\\d+)\\];`));
    assert.ok(match, name);
    return [Number(match[1]), Number(match[2])];
  };
  const out = range("SWAP_OUT_MS");
  const into = range("SWAP_IN_MS");
  assert.ok(Math.max(out[1], into[1]) <= 60, `the exchange is over by ${Math.max(out[1], into[1])} ms`);
  assert.ok(into[0] >= out[0] - 8 && into[0] < out[1], "the new layout starts as the old one leaves, not before and not after");
  // The curve they trade on (EASE_CROSS), solved for its y at a time: a cubic Bezier.
  const [x1, y1, x2, y2] = flight.match(/const EASE_CROSS = "cubic-bezier\(([^)]*)\)"/)[1].split(",").map(Number);
  const bezier = (p) => (t) => 3 * (1 - t) ** 2 * t * p[0] + 3 * (1 - t) * t * t * p[1] + t ** 3;
  const eased = (progress) => {
    let low = 0;
    let high = 1;
    for (let round = 0; round < 40; round += 1) {
      const mid = (low + high) / 2;
      if (bezier([x1, x2])(mid) < progress) low = mid; else high = mid;
    }
    return bezier([y1, y2])((low + high) / 2);
  };
  const run = (time, [from, to]) => (time <= from ? 0 : time >= to ? 1 : eased((time - from) / (to - from)));
  for (const input of FLIGHTS) {
    const motion = capsuleMotion(input);
    // The skin's opacity between two frames (they are eight milliseconds apart; the exchange is quicker than that).
    const between = (time) => {
      const position = (time / motion.duration) * (motion.skin.length - 1);
      const low = Math.floor(position);
      const part = position - low;
      return motion.skin[low].opacity * (1 - part) + motion.skin[Math.min(motion.skin.length - 1, low + 1)].opacity * part;
    };
    let worstBoth = 0;
    let dimmest = 9;
    for (let time = 0; time <= 80; time += 1) {
      const skin = between(time);
      const old = (1 - skin) * (1 - run(time, out));
      const now = run(time, into);
      worstBoth = Math.max(worstBoth, Math.min(old, now));
      dimmest = Math.min(dimmest, old + now);
    }
    assert.ok(worstBoth < 0.36, `${JSON.stringify(input)}: the two layouts are both seen at ${(worstBoth * 100).toFixed(0)}% at once`);
    assert.ok(dimmest > 0.5, `${JSON.stringify(input)}: and the text is never fainter than ${(dimmest * 100).toFixed(0)}% on the way`);
  }
  // The field's text is under the shell: the skin that comes up is what it goes out behind.
  assert.match(flight, /if \(fieldLayer\) ghost\.prepend\(fieldLayer\);/);
  assert.doesNotMatch(flight, /ghost\.append\(fieldLayer\)/);
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

test("the placeholder stays back until the text has left, in the composer that arrives and in the one that stays (guidance)", () => {
  // Guidance is sent from a composer that does not change: there is no crossfade, and the field's own placeholder would
  // come back under the text that is leaving. The rule that holds it back names both the composer that fades in and the
  // one that stays.
  assert.match(css, /\.composer-mode-crossfade\[data-sent\] > :is\(\.crossfade-in, \.crossfade-current\) \.composer textarea::placeholder \{\s*animation: loom-flight-placeholder/);
  const keyframes = css.match(/@keyframes loom-flight-placeholder \{([\s\S]*?)\n\}/)?.[1] ?? "";
  assert.match(keyframes, /0%, \d+% \{ opacity: 0; \}/);
  assert.doesNotMatch(keyframes, /100%/, "it ends where the placeholder's own style is, whatever that is");
  // The crossfade names the two classes this relies on.
  const crossfade = read("components/Crossfade.tsx");
  assert.match(crossfade, /"crossfade-in" : "crossfade-current"/);
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
