' ===== Standard module: NeonAuditBoot =====
' Arms the Neon audit listener. Workbook_Open calls NeonAuditStart; the
' excel-http watchdog calls NeonAuditPing to re-arm it if a VBA reset left
' it dead, and the ping tells the daemon the macro is alive.
Option Explicit

Public Auditor As CNeonAudit

Public Sub NeonAuditStart()
    On Error Resume Next
    If Auditor Is Nothing Then Set Auditor = New CNeonAudit
    Set Auditor.App = Application
End Sub

Public Sub NeonAuditPing()
    On Error Resume Next
    NeonAuditStart
    Auditor.Post "{""ping"":true,""ts"":""" & Format(Now, "yyyy-mm-dd\Thh:nn:ss") & """}"
End Sub
