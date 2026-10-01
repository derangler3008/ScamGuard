// Firefox stellt die Extension-APIs als `browser.*` bereit, Chromium als `chrome.*` –
// beide liefern unter Manifest V3 Promises. Alle Module nutzen `ext`.
export const ext = globalThis.browser ?? globalThis.chrome;
