/**
 * Google Apps Script — KB Analytics Beacon
 *
 * Logs page views to a Google Sheet. Provides DAU summary.
 *
 * Setup:
 * 1. Create a new Google Sheet called "KB Analytics"
 * 2. Add headers to Row 1: Date | Time | User | Referrer | Screen
 * 3. Open Extensions → Apps Script
 * 4. Paste this code, save
 * 5. Click Deploy → New deployment → Web app
 *    - Execute as: Me (mckay@m5c7.com)
 *    - Who has access: Anyone
 * 6. Copy the deployment URL
 * 7. Paste it into build.py ANALYTICS_URL
 * 8. Re-run: python3 ~/m5x2-kb/build.py
 */

function doGet(e) {
  try {
    var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
    var now = new Date();
    var date = Utilities.formatDate(now, Session.getScriptTimeZone(), "yyyy-MM-dd");
    var time = Utilities.formatDate(now, Session.getScriptTimeZone(), "HH:mm:ss");

    sheet.appendRow([
      date,
      time,
      e.parameter.u || "unknown",
      e.parameter.r || "",
      e.parameter.s || ""
    ]);
  } catch(err) {
    // Fail silently — don't break the page
  }

  return ContentService.createTextOutput("ok")
    .setMimeType(ContentService.MimeType.TEXT);
}

/**
 * Generate DAU Summary sheet.
 * Run manually, or set a daily trigger (Triggers → Add Trigger → summarizeDAU → Time-driven → Day timer)
 */
function summarizeDAU() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var raw = ss.getSheetByName("Sheet1") || ss.getActiveSheet();
  var data = raw.getDataRange().getValues();

  var summary = ss.getSheetByName("DAU Summary");
  if (!summary) {
    summary = ss.insertSheet("DAU Summary");
  }
  summary.clear();
  summary.appendRow(["Date", "Unique Visitors", "Total Views"]);

  var days = {};
  for (var i = 1; i < data.length; i++) {
    var date = data[i][0];
    if (!date) continue;
    var dateStr = (date instanceof Date)
      ? Utilities.formatDate(date, Session.getScriptTimeZone(), "yyyy-MM-dd")
      : String(date);
    var user = String(data[i][2]);
    if (!days[dateStr]) days[dateStr] = { users: {}, views: 0 };
    days[dateStr].users[user] = true;
    days[dateStr].views++;
  }

  var dates = Object.keys(days).sort().reverse();
  for (var d = 0; d < dates.length; d++) {
    var dt = dates[d];
    summary.appendRow([
      dt,
      Object.keys(days[dt].users).length,
      days[dt].views
    ]);
  }
}
