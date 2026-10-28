// The one switch between the mock and Pam's real backend.
// Set NEXT_PUBLIC_USE_MOCK=false in .env.local to talk to the real thing.
// (Next inlines NEXT_PUBLIC_* at build time, so restart `npm run dev` after changing it.)

export const USE_MOCK = process.env.NEXT_PUBLIC_USE_MOCK !== "false"; // mock unless told otherwise

export const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

export const DEVICE_ID = process.env.NEXT_PUBLIC_DEVICE_ID || "incubator-01";

// start the sample job as soon as the page loads. always on for the in-browser mock,
// `npm run dev:mock` turns it on for the mock server too
export const AUTO_START_SAMPLE = USE_MOCK || process.env.NEXT_PUBLIC_AUTO_START === "true";
