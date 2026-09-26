Option Explicit

Dim shell, fso, scriptDir, batPath, webhookFile
Dim webhookUrl, file, text

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
batPath = fso.BuildPath(scriptDir, "start.bat")
webhookFile = fso.BuildPath(scriptDir, "discord_webhook.json")

' Ask for Discord webhook on first launch.
If Not fso.FileExists(webhookFile) Then
    webhookUrl = InputBox( _
        "Enter Discord Webhook URL." & vbCrLf & vbCrLf & _
        "It will be saved to discord_webhook.json.", _
        "Discord setup", _
        "")

    webhookUrl = Trim(webhookUrl)

    If webhookUrl = "" Then
        MsgBox "Discord Webhook URL was not entered." & vbCrLf & _
               "The program will not start.", _
               vbExclamation, "Chest Scanner"
        WScript.Quit
    End If

    If InStr(1, webhookUrl, "https://discord.com/api/webhooks/", vbTextCompare) <> 1 _
       And InStr(1, webhookUrl, "https://discordapp.com/api/webhooks/", vbTextCompare) <> 1 Then
        MsgBox "This does not look like a Discord Webhook URL." & vbCrLf & _
               "Please check the URL and run Zapusk.vbs again.", _
               vbExclamation, "Chest Scanner"
        WScript.Quit
    End If

    text = "{""webhook_url"":""" & webhookUrl & """}"

    Set file = fso.CreateTextFile(webhookFile, True, False)
    file.Write text
    file.Close
    Set file = Nothing
End If

' Start the program hidden.
If fso.FileExists(batPath) Then
    shell.Run """" & batPath & """", 0, False
Else
    MsgBox "start.bat was not found next to this script.", vbCritical, "Chest Scanner"
End If

Set fso = Nothing
Set shell = Nothing
