#!/usr/bin/env node

import { appendFileSync, readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

export const supportedTypes = new Set([
  "legal",
  "community",
  "safety",
  "appAnnouncement",
]);

export const topicByType = Object.freeze({
  legal: "carryaware-legal",
  community: "carryaware-community",
  safety: "carryaware-safety",
  appAnnouncement: "carryaware-app",
});

function requiredString(value, field, updateID) {
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(
      "Update " + (updateID ?? "(unknown)") + " requires " + field,
    );
  }

  return value.trim();
}

export function validateFeed(feed) {
  if (!feed || typeof feed !== "object" || !Array.isArray(feed.updates)) {
    throw new Error("Feed must be an object with an updates array");
  }

  const ids = new Set();

  for (const update of feed.updates) {
    const id = requiredString(update?.id, "id");

    if (ids.has(id)) {
      throw new Error("Duplicate update id: " + id);
    }
    ids.add(id);

    requiredString(update.type, "type", id);
    if (!supportedTypes.has(update.type)) {
      throw new Error("Update " + id + " has unsupported type: " + update.type);
    }

    requiredString(update.title, "title", id);
    requiredString(update.summary, "summary", id);
    requiredString(update.date, "date", id);

    if (typeof update.isImportant !== "boolean") {
      throw new Error("Update " + id + " requires boolean isImportant");
    }

    if (update.sourceURL) {
      let source;
      try {
        source = new URL(update.sourceURL);
      } catch {
        throw new Error("Update " + id + " has an invalid sourceURL");
      }

      if (!["https:", "http:"].includes(source.protocol)) {
        throw new Error("Update " + id + " sourceURL must use HTTP or HTTPS");
      }
    }
  }

  return feed;
}

export function selectUpdates(currentFeed, previousFeed, manualID = "") {
  validateFeed(currentFeed);

  if (manualID) {
    const selected = currentFeed.updates.find(
      (update) => update.id === manualID,
    );
    if (!selected) {
      throw new Error("Manual update id not found: " + manualID);
    }
    return [selected];
  }

  const previousIDs = new Set(
    previousFeed && Array.isArray(previousFeed.updates)
      ? previousFeed.updates.map((update) => update.id)
      : currentFeed.updates.map((update) => update.id),
  );

  return currentFeed.updates.filter(
    (update) => update.isImportant && !previousIDs.has(update.id),
  );
}

function shortenedBody(summary, maximumLength = 240) {
  if (summary.length <= maximumLength) {
    return summary;
  }

  return summary.slice(0, maximumLength - 1).trimEnd() + "…";
}

function dataString(value) {
  if (value === undefined || value === null) {
    return "";
  }

  return String(value);
}

export function buildFCMMessage(update) {
  const topic = topicByType[update.type];
  if (!topic) {
    throw new Error("No Firebase topic for update type: " + update.type);
  }

  const message = {
    message: {
      topic,
      notification: {
        title: update.title,
        body: shortenedBody(update.summary),
      },
      data: {
        updateID: update.id,
        updateType: update.type,
        updateTitle: update.title,
        updateSummary: update.summary,
        updateCategory: dataString(update.category),
        updateStatus: dataString(update.status),
        updateDate: update.date,
        sourceTitle: dataString(update.sourceTitle),
        sourceURL: dataString(update.sourceURL),
        isImportant: String(update.isImportant),
      },
      apns: {
        headers: {
          "apns-priority": "10",
        },
        payload: {
          aps: {
            sound: "default",
          },
        },
      },
    },
  };

  const payloadSize = Buffer.byteLength(JSON.stringify(message), "utf8");
  if (payloadSize > 3800) {
    throw new Error(
      "Update " + update.id + " produces a " + payloadSize
        + "-byte push payload; limit it to 3800 bytes",
    );
  }

  return message;
}

function loadJSON(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}

function previousFeedFromGit(beforeSHA, feedPath) {
  if (!/^[0-9a-f]{40}$/i.test(beforeSHA) || /^0+$/.test(beforeSHA)) {
    return null;
  }

  try {
    const content = execFileSync(
      "git",
      ["show", beforeSHA + ":" + feedPath],
      { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] },
    );
    return JSON.parse(content);
  } catch {
    return null;
  }
}

async function sendSelectedUpdates(feed, updateIDs) {
  const accessToken = process.env.GOOGLE_OAUTH_ACCESS_TOKEN;
  const projectID = process.env.GCP_PROJECT_ID;

  if (!accessToken) {
    throw new Error("GOOGLE_OAUTH_ACCESS_TOKEN is required");
  }
  if (!projectID) {
    throw new Error("GCP_PROJECT_ID is required");
  }

  const selected = updateIDs.map((id) => {
    const update = feed.updates.find((candidate) => candidate.id === id);
    if (!update) {
      throw new Error("Selected update id not found: " + id);
    }
    return update;
  });

  for (const update of selected) {
    const endpoint = "https://fcm.googleapis.com/v1/projects/"
      + encodeURIComponent(projectID) + "/messages:send";
    const response = await fetch(endpoint, {
      method: "POST",
      headers: {
        Authorization: "Bearer " + accessToken,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(buildFCMMessage(update)),
    });

    if (!response.ok) {
      const responseText = await response.text();
      throw new Error(
        "FCM rejected " + update.id + ": HTTP " + response.status + " "
          + responseText.slice(0, 500),
      );
    }

    console.log(
      "Published " + update.id + " to " + topicByType[update.type],
    );
  }
}

function writeGitHubOutputs(updates) {
  const outputPath = process.env.GITHUB_OUTPUT;
  if (!outputPath) {
    throw new Error("GITHUB_OUTPUT is required");
  }

  appendFileSync(
    outputPath,
    "should_send=" + (updates.length > 0) + "\n",
  );
  appendFileSync(
    outputPath,
    "update_ids=" + JSON.stringify(updates.map((update) => update.id)) + "\n",
  );
}

async function main(argv) {
  const [command, feedPath] = argv;

  if (!command || !feedPath) {
    throw new Error(
      "Usage: update-notifications.mjs <validate|prepare|send> <feed-path>",
    );
  }

  const feed = validateFeed(loadJSON(feedPath));

  switch (command) {
  case "validate":
    console.log("Validated " + feed.updates.length + " updates");
    return;

  case "prepare": {
    const manualID = process.env.MANUAL_UPDATE_ID ?? "";
    const previousFeed = manualID
      ? null
      : previousFeedFromGit(process.env.BEFORE_SHA ?? "", feedPath);
    const updates = selectUpdates(feed, previousFeed, manualID);
    writeGitHubOutputs(updates);
    console.log("Selected " + updates.length + " update(s) for delivery");
    return;
  }

  case "send": {
    const updateIDs = JSON.parse(process.env.UPDATE_IDS ?? "[]");
    if (!Array.isArray(updateIDs)) {
      throw new Error("UPDATE_IDS must be a JSON array");
    }
    await sendSelectedUpdates(feed, updateIDs);
    return;
  }

  default:
    throw new Error("Unknown command: " + command);
  }
}

const isMainModule =
  process.argv[1] &&
  fileURLToPath(import.meta.url) === process.argv[1];

if (isMainModule) {
  main(process.argv.slice(2)).catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
