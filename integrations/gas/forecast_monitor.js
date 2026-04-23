/**
 * EnAACT Forecast Monitor — Google Apps Script
 *
 * Runs once a day at 2:30 AM. Checks whether the forecast spreadsheet has been
 * modified since the last check. If it has, triggers a full rerun on Cloud Run
 * (weather → scheduler → dashboard rebuild).
 *
 * SETUP (one-time):
 *   1. Open script.google.com → New project → name it "EnAACT Forecast Monitor"
 *   2. Paste this file's contents
 *   3. Add Script Properties (gear icon → Project Settings → Script Properties):
 *        SERVICE_URL  →  https://enact-walk-dashboard-uiy2p6yyja-ue.a.run.app
 *        GAS_SECRET   →  <token from GCP Secret Manager>
 *   4. Run setupTrigger() once from the editor to register the daily 2:30 AM trigger
 *   5. Verify in Executions tab that it fires each night
 *
 * NOTE: No FORECAST_MTIME property needs to be seeded manually — on the first run
 * it will treat the sheet as changed and trigger a rerun, which is safe.
 */

var SPREADSHEET_ID = "1-AQk9LXHlzeakHBvwdhFLeDrZojkZj3vG2h6cAOumm4";

// ── Check handler ─────────────────────────────────────────────────────────────

/**
 * Called once daily at ~2:30 AM by the time-based trigger.
 * Reads the spreadsheet's last-modified timestamp from Drive and compares it
 * against the value stored in ScriptProperties. Triggers /api/rerun only if
 * the sheet has changed since the last successful check.
 */
function checkForecast() {
  var props      = PropertiesService.getScriptProperties();
  var secret     = props.getProperty("GAS_SECRET")    || "";
  var serviceUrl = props.getProperty("SERVICE_URL")   || "";

  // ── 1. Read spreadsheet mtime via DriveApp ──────────────────────────────────
  var file;
  try {
    file = DriveApp.getFileById(SPREADSHEET_ID);
  } catch (err) {
    Logger.log("ERROR: Could not access spreadsheet — " + err.toString());
    return;
  }

  var currentMtime = file.getLastUpdated().getTime(); // ms since epoch
  var storedMtime  = parseInt(props.getProperty("FORECAST_MTIME") || "0", 10);

  Logger.log("Spreadsheet mtime: " + currentMtime + "  stored: " + storedMtime);

  // ── 2. Skip if nothing changed ──────────────────────────────────────────────
  if (currentMtime <= storedMtime) {
    Logger.log("No changes detected in forecast spreadsheet — skipping rerun.");
    return;
  }

  Logger.log("Forecast spreadsheet updated — triggering full rerun on Cloud Run.");

  // ── 3. POST /api/force-rebuild ──────────────────────────────────────────────
  var options = {
    method:           "post",
    headers: {
      "Authorization": "Bearer " + secret,
      "Content-Type":  "application/json"
    },
    payload:            "{}",
    muteHttpExceptions: true
  };

  try {
    var resp = UrlFetchApp.fetch(serviceUrl + "/api/force-rebuild", options);
    var code = resp.getResponseCode();
    Logger.log("Rerun HTTP " + code);

    if (code === 200) {
      // Persist new mtime only on success so a failed rerun retries next cycle.
      props.setProperty("FORECAST_MTIME", currentMtime.toString());
      Logger.log("Forecast state updated (mtime=" + currentMtime + ").");
    } else {
      Logger.log("Rerun failed — will retry tomorrow. Response: " + resp.getContentText());
    }
  } catch (err) {
    Logger.log("ERROR calling Cloud Run: " + err.toString());
  }
}

// ── Trigger registration ──────────────────────────────────────────────────────

/**
 * Run this function ONCE from the GAS editor to register the daily 2:30 AM trigger.
 * Re-running removes any existing checkForecast triggers first to avoid duplicates.
 */
function setupTrigger() {
  ScriptApp.getProjectTriggers().forEach(function(trigger) {
    if (trigger.getHandlerFunction() === "checkForecast") {
      ScriptApp.deleteTrigger(trigger);
    }
  });

  ScriptApp.newTrigger("checkForecast")
    .timeBased()
    .everyDays(1)
    .atHour(2)
    .nearMinute(30)
    .create();

  Logger.log("Daily 2:30 AM forecast check trigger registered.");
  Logger.log("Service URL: " + PropertiesService.getScriptProperties().getProperty("SERVICE_URL"));
}
