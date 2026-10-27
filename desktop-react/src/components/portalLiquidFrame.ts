// Where a snapped lens sits and how large it may become. Kept apart from the
// renderer so the numbers can be checked without a browser.

export const SNAP_PADDING = 10;
export const READ_MAX_WIDTH = 420;
export const CONTROL_MAX_WIDTH = 300;
export const MAX_LENS_HEIGHT = 92;
// A reading lens has to keep the line away from the rim, where the reference
// shader spends all of its displacement. 58px leaves an 18px margin around a
// 20px line, so the words sit in the flat middle of the glass.
export const MIN_READ_LENS_HEIGHT = 58;

export type LensFrame = {
  width: number;
  height: number;
  centerX: number;
  centerY: number;
  strength: number;
  reading: boolean;
};

export const LENS_STRENGTH = { free: 0.72, control: 0.44, read: 0.22 } as const;

type Box = { left: number; top: number; width: number; height: number };

// One place decides how large the lens becomes and where it sits. Reading
// stops (a thread row, a title) are capped so they never become a full-width
// band; controls are framed whole and stay centred on themselves. The caller
// moves the centre of a reading stop onto its painted words.
export function lensFrameFor(
  target: { dataset: { liquidSnapPad?: string; liquidLens?: string } },
  rect: Box,
  viewport: { width: number; height: number } = { width: window.innerWidth, height: window.innerHeight },
): LensFrame {
  const requestedPad = Number(target.dataset.liquidSnapPad);
  const reading = target.dataset.liquidLens === "read";
  const pad = Number.isFinite(requestedPad)
    ? Math.min(14, Math.max(4, requestedPad))
    : (reading ? 10 : SNAP_PADDING);
  const maxWidth = reading ? READ_MAX_WIDTH : CONTROL_MAX_WIDTH;
  const width = Math.min(
    Math.max(44, viewport.width - 20),
    Math.min(maxWidth, Math.max(reading ? 150 : 44, rect.width + pad * 2)),
  );
  const height = Math.min(
    Math.max(38, viewport.height - 20),
    Math.min(MAX_LENS_HEIGHT, Math.max(reading ? MIN_READ_LENS_HEIGHT : 38, rect.height + pad * 2)),
  );

  return {
    width,
    height,
    centerX: rect.left + rect.width / 2,
    centerY: rect.top + rect.height / 2,
    strength: reading ? LENS_STRENGTH.read : LENS_STRENGTH.control,
    reading,
  };
}
