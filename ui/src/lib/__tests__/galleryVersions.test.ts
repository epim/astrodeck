import assert from "node:assert/strict";
import { framesQuery, selectionQuery, thumbPath } from "../gallery";
import { viewPath } from "../frameView";

assert.notEqual(thumbPath("a.fits", 256, 1700000000.1), thumbPath("a.fits", 256, 1700000000.2));
assert.notEqual(viewPath("a.fits", 640, 1700000000.1), viewPath("a.fits", 640, 1700000000.2));
assert.match(thumbPath("a.fits", 256, "123:456:789:0"), /v=123%3A456%3A789%3A0/);
assert.match(viewPath("a.fits", 640, "123:456:789:0"), /v=123%3A456%3A789%3A0/);
assert.match(framesQuery({ cursor: "abc:200", q: "M31" }), /cursor=abc%3A200/);
const query = selectionQuery({ mode: "picked", paths: ["M31/a.fits"], snapshot: "abc", q: "M31", nightFrom: "2026-09-18" });
assert.match(query, /snapshot=abc/);
assert.match(query, /night_from=2026-09-18/);
assert.match(query, /path=M31%2Fa.fits/);
console.log("galleryVersions.test: 8/8 passed");
export const results = { passed: 8, failed: 0, total: 8 };
