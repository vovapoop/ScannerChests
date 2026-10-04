Option Explicit

Dim fso, shell, baseDir, pyExe, mainPy

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
mainPy = baseDir & "\main.py"

pyExe = baseDir & "\.venv\Scripts\python.exe"

If Not fso.FileExists(pyExe) Then
    pyExe = baseDir & "\.venv\Scripts\pythonw.exe"
End If

If Not fso.FileExists(mainPy) Then
    MsgBox "main.py not found.", vbCritical, "Chest Scanner"
    WScript.Quit 1
End If

If Not fso.FileExists(pyExe) Then
    MsgBox ".venv Python not found. Run installer again.", vbCritical, "Chest Scanner"
    WScript.Quit 1
End If

shell.CurrentDirectory = baseDir
shell.Run """" & pyExe & """ """ & mainPy & """", 0, False

WScript.Quit 0