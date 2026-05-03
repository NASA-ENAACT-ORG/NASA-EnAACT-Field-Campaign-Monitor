/**
 * EnAACT Forecast Monitor - Google Apps Script
 *
 * Runs whenever the forecast spreadsheet is edited (debounced 2 min), plus a
 * daily 4 AM safety-net check. Each rerun POSTs /api/force-rebuild on Cloud
 * Run, which triggers weather + dashboard/site rebuild without the scheduler.
 *
 * SETUP (one-time):
 *   1. Open script.google.com -> New project -> name it "EnAACT Forecast Monitor"
 *   2. Paste this file's contents
 *   3. Add Script Properties (gear icon → Project Settings → Script Properties):
 *        SERVICE_URL  →  https://enact-walk-dashboard-uiy2p6yyja-ue.a.run.app
 *        GAS_SECRET   →  <token from GCP Secret Manager>
 *   4. Run setupTrigger() once from the editor to register both triggers.
 *      First run will prompt for an additional OAuth scope to read/edit the
 *      spreadsheet — accept it.
 *   5. Verify in the Triggers panel that exactly two triggers exist:
 *        • onForecastEdit    (From spreadsheet, On edit)
 *        • dailySafetyCheck  (Time-driven, day timer, 4am to 5am)
 *
 * NOTE: No FORECAST_MTIME property needs to be seeded manually — on the first
 * safety-net run it will treat the sheet as changed and trigger a rerun, which
 * is safe.
 */

var SPREADSHEET_ID    = "1-AQk9LXHlzeakHBvwdhFLeDrZojkZj3vG2h6cAOumm4";
var DEBOUNCE_MINUTES  = 2;

// ── Edit-driven path ──────────────────────────────────────────────────────────

/**
 * Installable onEdit handler. Fires on every cell edit in the spreadsheet.
 * Schedules a one-shot debouncedRerun trigger ~DEBOUNCE_MINUTES from now,
 * unless one is already pending. This collapses bursts of edits into a single
 * downstream rerun.
 */
function onForecastEdit(e) {
  var pending = ScriptApp.getProjectTriggers().some(function(t) {
    return t.getHandlerFunction() === "debouncedRerun";
  });
  if (pending) return;

  ScriptApp.newTrigger("debouncedRerun")
    .timeBased()
    .after(DEBOUNCE_MINUTES * 60 * 1000)
    .create();

  Logger.log("Edit detected — debounced rerun scheduled in " + DEBOUNCE_MINUTES + " min.");
}

/**
 * Fired by the one-shot trigger scheduled in onForecastEdit. Cleans up its
 * own trigger so the next edit can schedule a fresh one, then triggers the
 * Cloud Run rerun.
 */
function debouncedRerun() {
  ScriptApp.getProjectTriggers().forEach(function(t) {
    if (t.getHandlerFunction() === "debouncedRerun") {
      ScriptApp.deleteTrigger(t);
    }
  });
  triggerRerun_("edit");
}

// ── Daily safety-net path ─────────────────────────────────────────────────────

/**
 * Called once daily at ~4 AM by the time-based trigger. Backstop in case the
 * onEdit trigger fails to fire (auth lapse, script disabled, edits via API).
 * Compares spreadsheet mtime against FORECAST_MTIME and only triggers a rerun
 * if the sheet has changed since the last successful rerun.
 */
function dailySafetyCheck() {
  var props = PropertiesService.getScriptProperties();

  var file;
  try {
    file = DriveApp.getFileById(SPREADSHEET_ID);
  } catch (err) {
    Logger.log("ERROR: Could not access spreadsheet - " + err.toString());
    return;
  }

  var currentMtime = file.getLastUpdated().getTime();
  var storedMtime  = parseInt(props.getProperty("FORECAST_MTIME") || "0", 10);

  Logger.log("[safety-net] Spreadsheet mtime: " + currentMtime + "  stored: " + storedMtime);

  if (currentMtime <= storedMtime) {
    Logger.log("[safety-net] No changes detected — skipping rerun.");
    return;
  }

  Logger.log("[safety-net] Forecast spreadsheet updated since last rerun — triggering.");
  triggerRerun_("safety-net");
}

// ── Shared rerun logic ────────────────────────────────────────────────────────

/**
 * POST /api/force-rebuild on Cloud Run with the GAS bearer token. On HTTP 200,
 * persist the spreadsheet's current mtime to FORECAST_MTIME so the safety-net
 * path knows the latest state has been processed.
 */
function triggerRerun_(reason) {
  var props      = PropertiesService.getScriptProperties();
  var secret     = props.getProperty("GAS_SECRET")  || "";
  var serviceUrl = props.getProperty("SERVICE_URL") || "";


  var options = {
    method:             "post",
    headers: {
      "Authorization":  "Bearer " + secret,
      "Content-Type":   "application/json"
    },
    payload:            "{}",
    muteHttpExceptions: true
  };

  try {
    var resp = UrlFetchApp.fetch(serviceUrl + "/api/force-rebuild", options);
    var code = resp.getResponseCode();
    Logger.log("[" + reason + "] Rerun HTTP " + code);

    if (code === 200) {
      var file = DriveApp.getFileById(SPREADSHEET_ID);
      var mtime = file.getLastUpdated().getTime();
      props.setProperty("FORECAST_MTIME", mtime.toString());
      Logger.log("[" + reason + "] Forecast state updated (mtime=" + mtime + ").");
    } else {
      Logger.log("[" + reason + "] Rerun failed. Response: " + resp.getContentText());
    }
  } catch (err) {
    Logger.log("[" + reason + "] ERROR calling Cloud Run: " + err.toString());
  }
}

// Trigger registration

/**
 * Run this function ONCE from the GAS editor to register both triggers:
 *   • onForecastEdit   — installable onEdit on the forecast spreadsheet
 *   • dailySafetyCheck — daily time-based at ~4 AM
 *
 * Re-running deletes any existing triggers for handlers this script owns
 * (including the legacy checkForecast handler) so the bootstrap is idempotent.
 */
function setupTrigger() {
  var ownedHandlers = {
    "onForecastEdit":   true,
    "debouncedRerun":   true,
    "dailySafetyCheck": true,
    "checkForecast":    true  // legacy — remove if present
  };

  ScriptApp.getProjectTriggers().forEach(function(trigger) {
    if (ownedHandlers[trigger.getHandlerFunction()]) {
      ScriptApp.deleteTrigger(trigger);
    }
  });

  ScriptApp.newTrigger("onForecastEdit")
    .forSpreadsheet(SPREADSHEET_ID)
    .onEdit()
    .create();

  ScriptApp.newTrigger("dailySafetyCheck")
    .timeBased()
    .everyDays(1)
    .atHour(4)
    .create();

  Logger.log("Triggers registered:");
  Logger.log("  • onForecastEdit   (spreadsheet onEdit, debounced " + DEBOUNCE_MINUTES + " min)");
  Logger.log("  • dailySafetyCheck (daily ~4 AM, mtime-gated)");
  Logger.log("Service URL: " + PropertiesService.getScriptProperties().getProperty("SERVICE_URL"));
}
