import test from "node:test";
import assert from "node:assert/strict";
import {apiBase, originHost, requestAPI, trustedSender} from "../client.js";
import {meetingTitle} from "../files.js";

test("only literal loopback HTTP API addresses are accepted", () => {
  assert.equal(apiBase("http://127.0.0.1:8420"), "http://127.0.0.1:8420");
  for (const value of ["https://example.com", "http://localhost:8420", "http://127.0.0.1.evil.test", "http://user:pass@127.0.0.1", "http://127.0.0.1/path", "http://127.0.0.1/?secret=x"]) {
    assert.throws(() => apiBase(value));
  }
});

test("content scripts and other extensions cannot use the privileged bridge", () => {
  const id = "a".repeat(32);
  const root = `chrome-extension://${id}/`;
  const sender = {id, url: `${root}popup.html`};
  assert.equal(trustedSender(sender, id, root), true);
  assert.equal(trustedSender({...sender, tab: {id: 1}}, id, root), true);
  assert.equal(trustedSender({...sender, tab: {id: 1}, url: "https://example.com"}, id, root), false);
  assert.equal(trustedSender({...sender, url: "https://example.com"}, id, root), false);
  assert.equal(trustedSender({...sender, id: "b".repeat(32)}, id, root), false);
  // Firefox: the ID names the add-on, the origin carries a per-install UUID.
  const gecko = "capture@imprintmcp.alexandruleca.com";
  const firefox = "moz-extension://6a8a5c4e-1b2d-4c3e-9f00-123456789abc/";
  assert.equal(trustedSender({id: gecko, url: `${firefox}options.html`}, gecko, firefox), true);
  assert.equal(trustedSender({id: gecko, url: "moz-extension://00000000-0000-4000-8000-000000000000/options.html"}, gecko, firefox), false);
  assert.equal(originHost(root), id);
  assert.equal(originHost(firefox), "6a8a5c4e-1b2d-4c3e-9f00-123456789abc");
});

test("requests use the fixed local browser API and redirects are refused", async () => {
  let request;
  const result = await requestAPI({base: "http://127.0.0.1:8420", extensionId: "extension"},
    "captures", {documents: []}, async (...args) => {
      request = args;
      return {ok: true, json: async () => ({job_id: "job"})};
    });
  assert.equal(result.job_id, "job");
  assert.equal(request[0], "http://127.0.0.1:8420/api/browser/local/captures");
  assert.equal(request[1].headers["X-Imprint-Extension-Id"], "extension");
  assert.equal(request[1].headers.Authorization, undefined);
  assert.equal(request[1].redirect, "error");
  assert.equal(request[1].credentials, "omit");
});

test("backend validation errors reach the UI", async () => {
  await assert.rejects(requestAPI({}, "status", undefined,
    async () => ({ok: false, json: async () => ({detail: "Unknown workspace"})})), /Unknown workspace/);
});

test("meeting titles use the active provider tab's title", () => {
  assert.equal(meetingTitle({url: "https://teams.microsoft.com/v2/", title: "(2) Planning call | Microsoft Teams"}), "Planning call");
  assert.equal(meetingTitle({url: "https://meet.google.com/abc-defg-hij", title: "Google Meet"}), "Google Meet · abc-defg-hij");
  assert.equal(meetingTitle({url: "https://example.com/", title: "Planning call | Microsoft Teams"}), "");
});
