/**
 * EnAACT Drive Watcher — Google Apps Script
 *
 * Monitors the shared Google Drive folder for new walk files and immediately
 * triggers the Cloud Run server to poll Drive and (if new walks found) rerun the
 * scheduler.
 *
 * SETUP (one-time):
 *   1. Open script.google.com → New project → name it "EnAACT Drive Watcher"
 *   2. Paste this file's contents
 *   3. Fill in SERVICE_URL below with your Cloud Run service URL
 *      (or set it via Project Settings → Script Properties instead)
 *   4. Add GAS_SECRET to Script Properties:
 *        Project Settings → Script Properties → Add property
 *        Name: GAS_SECRET   Value: <same token set in GCP Secret Manager>
 *   5. Run setupTrigger() once from the editor; grant Drive permissions when prompted
 *   6. Verify in Executions tab that the trigger fires on file uploads
 */

// ── Config ────────────────────────────────────────────────────────────────────

// Your Cloud Run service URL (e.g. https://enact-walk-dashboard-xxxxx.a.run.app)
var SERVICE_URL = PropertiesService.getScriptProperties().getProperty("SERVICE_URL") || "YOUR_CLOUD_RUN_URL_HERE";

// ── Event handler ─────────────────────────────────────────────────────────────

/**
 * Fires when any file in the Drive changes (add / edit / remove / trash).
 * Filters to "create" events only, then calls the Cloud Run /api/drive/poll
 * endpoint. If new walk files are found, also calls /api/rerun.
 *
 * NOTE: The onChange Drive trigger fires for ANY change across the entire Drive
 * that the GAS project can see — not just the target folder. Spurious triggers
 * on unrelated files result in a fast no-op poll (server returns new_files: 0).
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
    var pollResp = UrlFetchApp.fetch(SERVICE_URL + "/api/drive/poll", options);
    var code     = pollResp.getResponseCode();
    Logger.log("Drive poll triggered. HTTP " + code);

    if (code === 200) {
      var data = JSON.parse(pollResp.getContentText());
      Logger.log("new_files: " + data.new_files);

      if (data.new_files > 0) {
        // New walk files detected — trigger full scheduler rerun + rebuild
        var rerunResp = UrlFetchApp.fetch(SERVICE_URL + "/api/rerun", options);
        Logger.log("Rerun triggered. HTTP " + rerunResp.getResponseCode());
      }
    } else {
      Logger.log("Unexpected response: " + pollResp.getContentText());
    }
  } catch (err) {
    Logger.log("Error calling Cloud Run: " + err.toString());
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
  Logger.log("Service URL: " + SERVICE_URL);
}
