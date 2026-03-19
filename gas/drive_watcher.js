/**
 * EnAACT Drive Watcher — Google Apps Script
 *
 * Monitors the shared Google Drive folder for new walk files and immediately
 * triggers the Fly.io server to poll Drive and (if new walks found) rerun the
 * scheduler. This replaces the 60-second background polling loop in serve.py.
 *
 * SETUP (one-time):
 *   1. Open script.google.com → New project → name it "EnAACT Drive Watcher"
 *   2. Paste this file's contents
 *   3. Fill in FLYIO_URL and DRIVE_FOLDER_ID below (or leave as placeholders
 *      and set them via Project Settings → Script Properties instead)
 *   4. Add GAS_SECRET to Script Properties:
 *        Project Settings → Script Properties → Add property
 *        Name: GAS_SECRET   Value: <same token set in fly secrets>
 *   5. Run setupTrigger() once from the editor; grant Drive permissions when prompted
 *   6. Verify in Executions tab that the trigger fires on file uploads
 */

// ── Config ────────────────────────────────────────────────────────────────────

var FLYIO_URL = "https://enact-walk-dashboard.fly.dev";

// The Google Drive folder ID where collectors upload walk files.
// Same value as the GOOGLE_DRIVE_FOLDER_ID Fly.io secret.
var DRIVE_FOLDER_ID = "YOUR_DRIVE_FOLDER_ID_HERE";

// ── Event handler ─────────────────────────────────────────────────────────────

/**
 * Fires when any file in the Drive changes (add / edit / remove / trash).
 * Filters to "create" events only, then calls the Fly.io /api/drive/poll
 * endpoint. If new walk files are found, also calls /api/rerun.
 *
 * NOTE: The onChange Drive trigger fires for ANY change across the entire Drive
 * that the GAS project can see — not just the target folder. Spurious triggers
 * on unrelated files result in a fast no-op poll (Fly.io returns new_files: 0).
 */
function onDriveChange(e) {
  if (!e || e.changeType !== "create") return;

  var props  = PropertiesService.getScriptProperties();
  var secret = props.getProperty("GAS_SECRET") || "";

  var options = {
    method: "post",
    headers: {
      "Authorization": "Bearer " + secret,
      "Content-Type":  "application/json"
    },
    muteHttpExceptions: true
  };

  try {
    var pollResp = UrlFetchApp.fetch(FLYIO_URL + "/api/drive/poll", options);
    var code     = pollResp.getResponseCode();
    Logger.log("Drive poll triggered. HTTP " + code);

    if (code === 200) {
      var data = JSON.parse(pollResp.getContentText());
      Logger.log("new_files: " + data.new_files);

      if (data.new_files > 0) {
        // New walk files detected — trigger full scheduler rerun + rebuild
        var rerunResp = UrlFetchApp.fetch(FLYIO_URL + "/api/rerun", options);
        Logger.log("Rerun triggered. HTTP " + rerunResp.getResponseCode());
      }
    } else {
      Logger.log("Unexpected response: " + pollResp.getContentText());
    }
  } catch (err) {
    Logger.log("Error calling Fly.io: " + err.toString());
  }
}

// ── Trigger registration ──────────────────────────────────────────────────────

/**
 * Run this function ONCE from the GAS editor to register the Drive onChange
 * trigger. After that, it fires automatically on file uploads.
 *
 * Re-running this function removes existing triggers first to avoid duplicates.
 */
function setupTrigger() {
  // Remove any existing triggers registered by this script
  ScriptApp.getProjectTriggers().forEach(function(trigger) {
    ScriptApp.deleteTrigger(trigger);
  });

  // Register a new onChange trigger scoped to the user's Drive
  ScriptApp.newTrigger("onDriveChange")
    .forDrive()
    .onChange()
    .create();

  Logger.log("Drive onChange trigger registered successfully.");
  Logger.log("Fly.io URL : " + FLYIO_URL);
  Logger.log("Drive folder: " + DRIVE_FOLDER_ID);
}
