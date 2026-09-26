Option Explicit

Dim shell, fso
Dim scriptDir, mainPath, venvPythonW
Dim logPath, logFile, command

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = scriptDir

mainPath = fso.BuildPath(scriptDir, "main.py")
venvPythonW = fso.BuildPath(scriptDir, ".venv\Scripts\pythonw.exe")
logPath = fso.BuildPath(scriptDir, "launcher.log")

WriteLog logPath, "Launcher started: " & Now

If Not fso.FileExists(mainPath) Then
    WriteLog logPath, "ERROR: main.py not found: " & mainPath
    MsgBox "main.py not found:" & vbCrLf & vbCrLf & mainPath, vbCritical, "Chest Scanner"
    WScript.Quit 1
End If

' Existing virtual environment: start directly with pythonw.exe.
If fso.FileExists(venvPythonW) Then
    command = Quote(venvPythonW) & " " & Quote(mainPath)
    WriteLog logPath, "Launching venv pythonw: " & command
    shell.Run command, 0, False
    WScript.Quit 0
End If

' First run: let main.py create the virtual environment.
If CanRun(shell, "pyw.exe -3 --version") Then
    command = "pyw.exe -3 " & Quote(mainPath)
    WriteLog logPath, "Launching pyw: " & command
    shell.Run command, 0, False
    WScript.Quit 0
End If

If CanRun(shell, "pythonw.exe --version") Then
    command = "pythonw.exe " & Quote(mainPath)
    WriteLog logPath, "Launching pythonw: " & command
    shell.Run command, 0, False
    WScript.Quit 0
End If

WriteLog logPath, "ERROR: Python launcher not found."
MsgBox "Python was not found." & vbCrLf & vbCrLf & _
       "See launcher.log for details.", vbCritical, "Chest Scanner"
WScript.Quit 1

Function Quote(value)
    Quote = Chr(34) & value & Chr(34)
End Function

Function CanRun(shellObj, commandText)
    Dim resultCode, errorNumber
    On Error Resume Next
    Err.Clear
    resultCode = shellObj.Run(commandText, 0, True)
    errorNumber = Err.Number
    Err.Clear
    On Error GoTo 0
    CanRun = (errorNumber = 0 And resultCode = 0)
End Function

Sub WriteLog(path, message)
    Dim fileObj
    On Error Resume Next
    Set fileObj = fso.OpenTextFile(path, 8, True, -1)
    fileObj.WriteLine message
    fileObj.Close
    Set fileObj = Nothing
    On Error GoTo 0
End Sub
