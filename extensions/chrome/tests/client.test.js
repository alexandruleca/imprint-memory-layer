import test from "node:test";
import assert from "node:assert/strict";
import {apiBase, requestAPI, trustedSender} from "../client.js";

test("only literal loopback HTTP API addresses are accepted", () => {
  assert.equal(apiBase("http://127.0.0.1:8420"), "http://127.0.0.1:8420");
  for (const value of ["https://example.com", "http://localhost:8420", "http://127.0.0.1.evil.test", "http://user:pass@127.0.0.1", "http://127.0.0.1/path", "http://127.0.0.1/?secret=x"]) {
    assert.throws(() => apiBase(value));
  }
});

test("content scripts and other extensions cannot use the privileged bridge", () => {
  const id = "a".repeat(32);
  const sender = {id, url: `chrome-extension://${id}/popup.html`};
  assert.equal(trustedSender(sender, id), true);
  assert.equal(trustedSender({...sender, tab: {id: 1}}, id), true);
  assert.equal(trustedSender({...sender, tab: {id: 1}, url: "https://example.com"}, id), false);
  assert.equal(trustedSender({...sender, url: "https://example.com"}, id), false);
  assert.equal(trustedSender({...sender, id: "b".repeat(32)}, id), false);
});

test("capture credentials stay on the fixed browser API and redirects are refused", async () => {
  let request;
  const result = await requestAPI({base: "http://127.0.0.1:8420", extensionId: "extension", token: "token"},
    "captures", {documents: []}, async (...args) => {
      request = args;
      return {ok: true, json: async () => ({job_id: "job"})};
    });
  assert.equal(result.job_id, "job");
  assert.equal(request[0], "http://127.0.0.1:8420/api/browser/captures");
  assert.equal(request[1].headers.Authorization, "Bearer token");
  assert.equal(request[1].redirect, "error");
  assert.equal(request[1].credentials, "omit");
});

test("backend validation errors reach the UI", async () => {
  await assert.rejects(requestAPI({token: "token"}, "status", undefined,
    async () => ({ok: false, json: async () => ({detail: "Pairing revoked"})})), /Pairing revoked/);
});
