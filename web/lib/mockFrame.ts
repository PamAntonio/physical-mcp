// Draws a fake 1280x720 camera frame of the demo incubator as an SVG data URL.
// NOT a real photo. The buttons sit exactly on the bboxes in shared/fixtures/jobSample.json,
// so the camera overlay lines up. Mock mode only; the real backend sends real frames.
//
// screenText goes on the LCD (null = blank, the camera view paints live text on top).
// pressBox draws a ring where the finger is about to press, for "before" thumbnails.

import type { BBox } from "@contract/types";

export function mockFrameUrl(screenText: string | null, pressBox?: BBox): string {
  let lcd = "";
  if (screenText) {
    lcd = `<text x="540" y="232" font-family="Consolas, monospace" font-size="30" fill="#6dff9e" text-anchor="middle" letter-spacing="1">${escapeXml(screenText)}</text>`;
  }

  let ring = "";
  if (pressBox) {
    const [x, y, w, h] = pressBox;
    const centerX = x + w / 2;
    const centerY = y + h / 2;
    ring = `<circle cx="${centerX}" cy="${centerY}" r="${Math.max(w, h) / 2 + 10}" fill="#5cc8ff" fill-opacity="0.18" stroke="#5cc8ff" stroke-width="4"/>
      <path d="M${centerX + 6} ${centerY + 8} l90 120 l26 -20 l-90 -120 Z" fill="#c9b8a8" stroke="#6b5d52" stroke-width="2"/>`;
  }

  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
  <defs>
    <linearGradient id="wall" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2b2f33"/><stop offset="1" stop-color="#1c1f22"/></linearGradient>
    <linearGradient id="bench" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#4a4038"/><stop offset="1" stop-color="#2e2823"/></linearGradient>
    <linearGradient id="body" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#e3e5e6"/><stop offset="1" stop-color="#b9bdc0"/></linearGradient>
    <linearGradient id="glass" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#3b4a52"/><stop offset="0.5" stop-color="#26323a"/><stop offset="1" stop-color="#1b242a"/></linearGradient>
    <linearGradient id="btn" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#5a6168"/><stop offset="1" stop-color="#3a4046"/></linearGradient>
    <linearGradient id="btnStart" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#3f9a5a"/><stop offset="1" stop-color="#2a6e3f"/></linearGradient>
    <radialGradient id="vignette" cx="0.5" cy="0.5" r="0.75"><stop offset="0.6" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity="0.55"/></radialGradient>
  </defs>
  <rect width="1280" height="720" fill="url(#wall)"/>
  <rect y="600" width="1280" height="120" fill="url(#bench)"/>
  <rect y="598" width="1280" height="4" fill="#5c5046"/>
  <rect x="352" y="80" width="660" height="530" rx="18" fill="#00000055" transform="translate(8 10)"/>
  <rect x="352" y="80" width="660" height="530" rx="18" fill="url(#body)"/>
  <rect x="352" y="80" width="660" height="40" rx="18" fill="#d0d3d5"/>
  <text x="372" y="107" font-family="Arial, Helvetica, sans-serif" font-size="16" font-weight="700" fill="#6b7177" letter-spacing="3">INCUBATOR</text>
  <rect x="384" y="150" width="312" height="260" rx="10" fill="#2a2f34" stroke="#14171a" stroke-width="3"/>
  <rect x="400" y="180" width="280" height="84" rx="4" fill="#0b1a10" stroke="#0a0d0b" stroke-width="3"/>
  <rect x="404" y="184" width="272" height="20" fill="#ffffff" opacity="0.04"/>
  ${lcd}
  <g font-family="Arial, Helvetica, sans-serif" font-weight="700" text-anchor="middle" fill="#eef0f2">
    <rect x="412" y="288" width="58" height="40" rx="6" fill="url(#btn)"/><text x="441" y="313" font-size="15">SET</text>
    <rect x="486" y="288" width="44" height="40" rx="6" fill="url(#btn)"/><path d="M508 299 L518 316 L498 316 Z"/>
    <rect x="540" y="288" width="44" height="40" rx="6" fill="url(#btn)"/><path d="M562 317 L572 300 L552 300 Z"/>
    <rect x="596" y="288" width="70" height="40" rx="6" fill="url(#btn)"/><text x="631" y="313" font-size="15">ENTER</text>
    <rect x="412" y="344" width="70" height="40" rx="6" fill="url(#btn)"/><text x="447" y="369" font-size="15">TIMER</text>
    <rect x="596" y="344" width="70" height="40" rx="6" fill="url(#btnStart)"/><text x="631" y="369" font-size="15">START</text>
  </g>
  <rect x="724" y="146" width="264" height="430" rx="12" fill="#a9aeb2"/>
  <rect x="740" y="162" width="232" height="398" rx="8" fill="url(#glass)"/>
  <path d="M760 180 L820 180 L760 300 Z" fill="#ffffff" opacity="0.06"/>
  <rect x="790" y="300" width="132" height="6" rx="3" fill="#56636b"/>
  <rect x="790" y="420" width="132" height="6" rx="3" fill="#56636b"/>
  <rect x="996" y="300" width="10" height="110" rx="5" fill="#8d9296"/>
  <rect x="380" y="606" width="60" height="10" rx="3" fill="#1a1a1a"/>
  <rect x="924" y="606" width="60" height="10" rx="3" fill="#1a1a1a"/>
  ${ring}
  <rect width="1280" height="720" fill="url(#vignette)"/>
  <text x="24" y="40" font-family="Consolas, monospace" font-size="18" fill="#ffffff" opacity="0.55">CAM-0  MOCK FRAME</text>
</svg>`;

  return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
}

// screen text like "TEMP 25.0 > 37.0" has characters that would break the SVG
function escapeXml(text: string): string {
  let out = "";
  for (const char of text) {
    if (char === "&") out += "&amp;";
    else if (char === "<") out += "&lt;";
    else if (char === ">") out += "&gt;";
    else out += char;
  }
  return out;
}
