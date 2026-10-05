' ===== Class module: CNeonAudit =====
' Logs edits made IN EXCEL to the Neon workbook for JM Dash's source
' dimension (source 3p-app / via excel). Pipeline writes (excel-http daemon,
' ix-osa, neon-osa-quiet.sh) run with Application.EnableEvents = False, so
' they never reach this handler. Reports go to the daemon's /observe via
' AppleScriptTask "neon-audit.scpt" (backgrounded curl; nothing calls back
' into Excel). Every error is swallowed: a broken audit must never interrupt
' typing.
Option Explicit

Public WithEvents App As Application

Private Const MAX_CELLS As Long = 500
Private snapAddr() As String, snapFormula() As String, snapValue() As String
Private snapN As Long

Private Function IsNeon(wb As Object) As Boolean
    ' "Neon<分>v12.2.xlsx", any version; not conflicted copies ("...-Ix.xlsx").
    Dim nm As String: nm = wb.Name
    IsNeon = (nm Like "Neon" & ChrW(&H5206) & "v#*.xlsx") And InStr(nm, "-") = 0 And InStr(nm, " ") = 0
End Function

Private Sub App_SheetSelectionChange(ByVal Sh As Object, ByVal Target As Range)
    ' Snapshot the selection so a change can report its true before-value.
    On Error Resume Next
    snapN = 0
    If Not IsNeon(Sh.Parent) Then Exit Sub
    If Target.Cells.CountLarge > MAX_CELLS Then Exit Sub
    ReDim snapAddr(1 To Target.Cells.CountLarge)
    ReDim snapFormula(1 To Target.Cells.CountLarge)
    ReDim snapValue(1 To Target.Cells.CountLarge)
    Dim c As Range
    For Each c In Target.Cells
        snapN = snapN + 1
        snapAddr(snapN) = Sh.Name & "!" & c.Address
        snapFormula(snapN) = CStr(c.Formula)
        snapValue(snapN) = CellValue(c)
    Next c
End Sub

Private Sub App_SheetChange(ByVal Sh As Object, ByVal Target As Range)
    On Error Resume Next
    If Not IsNeon(Sh.Parent) Then Exit Sub
    Dim ts As String: ts = Format(Now, "yyyy-mm-dd\Thh:nn:ss")
    ' Whole-row/column inserts, deletes, sorts: one structural event, no cells.
    If Target.Columns.Count = Sh.Columns.Count Or Target.Rows.Count = Sh.Rows.Count _
       Or Target.Cells.CountLarge > 5000 Then
        Post "{""sheet"":" & Q(Sh.Name) & ",""ts"":" & Q(ts) & ",""structural"":true,""rows"":" _
            & Q(Target.Address) & "}"
        Exit Sub
    End If
    Dim dc As Long: dc = DateCol(Sh.Name)
    Dim json As String, c As Range, a As Range, n As Long, i As Long, key As String
    For Each a In Target.Areas
        For Each c In a.Cells
            n = n + 1
            If n > MAX_CELLS Then Exit For
            If Len(json) > 0 Then json = json & ","
            json = json & "{""row"":" & c.Row & ",""col"":""" & ColLetter(c.Column) & """" _
                & ",""date"":" & DateText(Sh, c.Row, dc) _
                & ",""formula"":" & Q(CStr(c.Formula)) _
                & ",""value"":" & Q(CellValue(c))
            key = Sh.Name & "!" & c.Address
            For i = 1 To snapN
                If snapAddr(i) = key Then
                    json = json & ",""before_formula"":" & Q(snapFormula(i)) _
                        & ",""before_value"":" & Q(snapValue(i))
                    snapFormula(i) = CStr(c.Formula): snapValue(i) = CellValue(c)
                    Exit For
                End If
            Next i
            json = json & "}"
        Next c
        If n > MAX_CELLS Then Exit For
    Next a
    Post "{""sheet"":" & Q(Sh.Name) & ",""ts"":" & Q(ts) _
        & ",""truncated"":" & IIf(n > MAX_CELLS, "true", "false") & ",""cells"":[" & json & "]}"
End Sub

Public Sub Post(json As String)
    On Error Resume Next
    AppleScriptTask "neon-audit.scpt", "postEdit", json
End Sub

Private Function CellValue(c As Range) As String
    On Error Resume Next
    If IsError(c.Value) Then
        CellValue = c.Text
    Else
        CellValue = CStr(c.Value2)
    End If
End Function

Private Function DateCol(sheetName As String) As Long
    ' Date column per sheet, matching excel-http's lookup: 0n -> C; 0<fen>, 1n+, hcbi -> B.
    If sheetName = "0n" Then
        DateCol = 3
    ElseIf sheetName = "0" & ChrW(&H5206) Or sheetName = "1n+" Or sheetName = "hcbi" Then
        DateCol = 2
    End If
End Function

Private Function DateText(Sh As Object, r As Long, dc As Long) As String
    On Error Resume Next
    DateText = "null"
    If dc = 0 Then Exit Function
    Dim v As Variant: v = Sh.Cells(r, dc).Value
    If IsError(v) Then Exit Function
    If IsDate(v) Then
        DateText = """" & Month(v) & "/" & Day(v) & """"
    ElseIf Len(CStr(v)) > 0 Then
        DateText = Q(CStr(v))
    End If
End Function

Private Function ColLetter(ByVal n As Long) As String
    Dim s As String
    Do While n > 0
        s = Chr(65 + ((n - 1) Mod 26)) & s
        n = (n - 1) \ 26
    Loop
    ColLetter = s
End Function

Private Function Q(s As String) As String
    ' JSON string literal: escapes quote, backslash, and every control char
    ' (tab, CR/LF from Alt+Enter); non-ASCII passes through as UTF-8.
    Dim i As Long, ch As String, code As Long, out As String
    For i = 1 To Len(s)
        ch = Mid(s, i, 1): code = AscW(ch)
        If ch = """" Then
            out = out & "\"""
        ElseIf ch = "\" Then
            out = out & "\\"
        ElseIf code >= 0 And code < 32 Then
            out = out & "\u" & Right("000" & Hex(code), 4)
        Else
            out = out & ch
        End If
    Next i
    Q = """" & out & """"
End Function
