/**
 * EnAACT Drive Watcher — Google Apps Script
 *
 * Polls the Cloud Run server every minute to check for new walk folders in Drive.
 * If new walks are found, triggers a full scheduler rerun.
 *
 * SETUP (one-time):
 *   1. Open script.google.com → New project → name it "EnAACT Drive Watcher"
 *   2. Paste this file's contents
 *   3. Add Script Properties (gear icon → Project Settings → Script Properties):
 *        SERVICE_URL  →  https://enact-walk-dashboard-uiy2p6yyja-ue.a.run.app
 *        GAS_SECRET   →  <token from GCP Secret Manager>
 *   4. Run setupTrigger() once from the editor to register the 1-minute poll
 *   5. Verify in Executions tab that it fires every minute
 */

// ── Config ────────────────────────────────────────────────────────────────────

var SERVICE_URL = PropertiesService.getScriptProperties().getProperty("SERVICE_URL") || "YOUR_CLOUD_RUN_URL_HERE";

// ── Poll handler ──────────────────────────────────────────────────────────────

/**
 * Called every minute by the time-based trigger.
 * Asks the Cloud Run server to check Drive for new walk folders.
 * If any are found, triggers a full scheduler rerun.
 */
function pollDrive() {
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
    Logger.log("Drive poll HTTP " + code);

    if (code === 200) {
      var data = JSON.parse(pollResp.getContentText());
      Logger.log("new_files: " + data.new_files);
      // Walk-log updated — dashboard rebuild happens server-side.
      // Scheduler only reruns on new forecast data or manual rejection.
    } else {
      Logger.log("Unexpected response: " + pollResp.getContentText());
    }
  } catch (err) {
    Logger.log("Error calling Cloud Run: " + err.toString());
  }
}

// ── Trigger registration ──────────────────────────────────────────────────────

/**
 * Run this function ONCE from the GAS editor to register a 1-minute poll trigger.
 * Re-running removes existing triggers first to avoid duplicates.
 */
function setupTrigger() {
  ScriptApp.getProjectTriggers().forEach(function(trigger) {
    ScriptApp.deleteTrigger(trigger);
  });

  ScriptApp.newTrigger("pollDrive")
    .timeBased()
    .everyMinutes(1)
    .create();

  Logger.log("1-minute poll trigger registered.");
  Logger.log("Service URL: " + SERVICE_URL);
}
