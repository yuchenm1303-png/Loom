const CARD_SELECTOR = ".starter-card";

let activeCard: HTMLElement | null = null;
let pointerX = 0;
let pointerY = 0;
let frame = 0;

function cardFromTarget(target: EventTarget | null): HTMLElement | null {
  return target instanceof Element ? target.closest<HTMLElement>(CARD_SELECTOR) : null;
}

function clearCard(card: HTMLElement | null) {
  if (!card) return;
  delete card.dataset.starterPointer;
  card.style.removeProperty("--starter-x");
  card.style.removeProperty("--starter-y");
}

function flushPointerGlow() {
  frame = 0;
  const card = activeCard;
  if (!card || !card.isConnected) return;

  const rect = card.getBoundingClientRect();
  if (!rect.width || !rect.height) return;

  const x = Math.max(0, Math.min(1, (pointerX - rect.left) / rect.width));
  const y = Math.max(0, Math.min(1, (pointerY - rect.top) / rect.height));

  card.style.setProperty("--starter-x", `${(x * 100).toFixed(1)}%`);
  card.style.setProperty("--starter-y", `${(y * 100).toFixed(1)}%`);
}

function schedulePointerGlow() {
  if (frame) return;
  frame = window.requestAnimationFrame(flushPointerGlow);
}

function leaveActiveCard() {
  if (frame) {
    window.cancelAnimationFrame(frame);
    frame = 0;
  }
  clearCard(activeCard);
  activeCard = null;
}

document.addEventListener("pointermove", (event) => {
  const nextCard = cardFromTarget(event.target);
  const usableCard = nextCard && !nextCard.matches(":disabled") ? nextCard : null;

  if (usableCard !== activeCard) {
    clearCard(activeCard);
    activeCard = usableCard;
    if (activeCard) activeCard.dataset.starterPointer = "true";
  }

  if (!activeCard) return;
  if (document.documentElement.dataset.loomReducedMotion === "true") {
    clearCard(activeCard);
    activeCard = null;
    return;
  }

  pointerX = event.clientX;
  pointerY = event.clientY;
  schedulePointerGlow();
}, { passive: true });

document.addEventListener("pointerout", (event) => {
  const fromCard = cardFromTarget(event.target);
  if (!fromCard || fromCard !== activeCard) return;
  const toCard = cardFromTarget(event.relatedTarget);
  if (toCard === fromCard) return;
  leaveActiveCard();
}, { passive: true });

window.addEventListener("blur", leaveActiveCard);
