import assert from "node:assert/strict";
import test from "node:test";

import {
  buildFCMMessage,
  selectUpdates,
  validateFeed,
} from "./update-notifications.mjs";

function update(overrides = {}) {
  return {
    id: "community-story-1",
    type: "community",
    title: "Community Story",
    summary: "A concise summary.",
    category: "Community News",
    status: "Published",
    date: "2026-09-05",
    sourceTitle: "Example",
    sourceURL: "https://example.com/story",
    isImportant: true,
    ...overrides,
  };
}

test("validates all supported alert types", () => {
  const feed = {
    version: 2,
    updates: [
      update({ id: "legal-1", type: "legal" }),
      update({ id: "community-1", type: "community" }),
      update({ id: "safety-1", type: "safety" }),
      update({ id: "app-1", type: "appAnnouncement" }),
    ],
  };

  assert.equal(validateFeed(feed), feed);
});

test("rejects duplicate update IDs", () => {
  const duplicate = update();
  assert.throws(
    () => validateFeed({ updates: [duplicate, { ...duplicate }] }),
    /Duplicate update id/,
  );
});

test("selects only newly added important updates", () => {
  const existing = update({ id: "existing" });
  const newlyAdded = update({ id: "new" });
  const informational = update({ id: "info", isImportant: false });

  const selected = selectUpdates(
    { updates: [newlyAdded, informational, existing] },
    { updates: [existing] },
  );

  assert.deepEqual(selected.map((item) => item.id), ["new"]);
});

test("manual selection can resend one exact update", () => {
  const first = update({ id: "first" });
  const second = update({ id: "second" });

  const selected = selectUpdates(
    { updates: [first, second] },
    { updates: [first, second] },
    "second",
  );

  assert.deepEqual(selected.map((item) => item.id), ["second"]);
});

test("builds the matching topic and app payload", () => {
  const message = buildFCMMessage(
    update({ id: "safety-1", type: "safety" }),
  );

  assert.equal(message.message.topic, "carryaware-safety");
  assert.equal(message.message.data.updateID, "safety-1");
  assert.equal(message.message.data.updateType, "safety");
  assert.equal(message.message.apns.payload.aps.sound, "default");
});
