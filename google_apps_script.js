/**
 * Google Apps Script Web App for Edoofa Audit Tool Sync
 * 
 * Instructions:
 * 1. Open your Google Sheet where you want findings to go.
 * 2. Click "Extensions" -> "Apps Script".
 * 3. Delete any default code and paste this code.
 * 4. Click "Deploy" (top right) -> "New deployment".
 * 5. Choose "Web app" as the type.
 * 6. Set "Execute as" to "Me (your email)".
 * 7. Set "Who has access" to "Anyone".
 * 8. Click "Deploy", authorize the permissions, and copy the "Web app URL".
 * 9. Paste this URL into the Edoofa Audit Tool dashboard settings.
 */

function doPost(e) {
  try {
    var payload = JSON.parse(e.postData.contents);
    var targetSheetId = payload.sheetId; // Optional: can target a specific sheet by ID
    var sheetName = "Audit Findings";
    
    var ss;
    if (targetSheetId) {
      ss = SpreadsheetApp.openById(targetSheetId);
    } else {
      ss = SpreadsheetApp.getActiveSpreadsheet();
    }
    
    var sheet = ss.getSheetByName(sheetName);
    if (!sheet) {
      sheet = ss.insertSheet(sheetName);
      // Append header row if the sheet is new
      sheet.appendRow([
        "Chat ID / Student ID",
        "Audit Date",
        "Framework Category",
        "Severity",
        "Finding Title",
        "Description",
        "Evidence (Quotes & Timestamps)",
        "Edoofa Business Impact",
        "Actionable Guidance"
      ]);
      // Style headers
      var headerRange = sheet.getRange(1, 1, 1, 9);
      headerRange.setFontWeight("bold");
      headerRange.setBackground("#312e81"); // Deep blue
      headerRange.setFontColor("#ffffff");
      headerRange.setHorizontalAlignment("center");
      sheet.setFrozenRows(1);
    }
    
    var rows = payload.rows;
    var todayStr = new Date().toISOString().split('T')[0];
    
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      sheet.appendRow([
        row.chatId,
        todayStr,
        row.category,
        row.severity,
        row.title,
        row.description,
        row.evidence,
        row.impact,
        row.guidance
      ]);
    }
    
    // Auto-resize columns
    sheet.autoResizeColumns(1, 9);
    
    return ContentService.createTextOutput(JSON.stringify({ 
      status: "success", 
      message: "Sync complete", 
      count: rows.length 
    })).setMimeType(ContentService.MimeType.JSON);
    
  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({ 
      status: "error", 
      message: err.toString() 
    })).setMimeType(ContentService.MimeType.JSON);
  }
}
