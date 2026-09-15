Set ws = CreateObject("WScript.Shell")
desktop = ws.SpecialFolders("Desktop")
Set fso = CreateObject("Scripting.FileSystemObject")

' 1. Delete old batch copy if exists
oldBat = desktop & "\PaperForge-run.bat"
If fso.FileExists(oldBat) Then
  fso.DeleteFile oldBat, True
End If

' 2. Create shortcut
Set shortcut = ws.CreateShortcut(desktop & "\PaperForge.lnk")
shortcut.TargetPath = "C:\Users\<user>\WorkBuddy\2026-06-13-21-30-08\paperforge\start_paperforge.bat"
shortcut.WorkingDirectory = "C:\Users\<user>\WorkBuddy\2026-06-13-21-30-08\paperforge"
shortcut.Description = "PaperForge - Academic Writing Assistant"
shortcut.Save()

' 3. Verify
resultPath = desktop & "\PaperForge.lnk"
If fso.FileExists(resultPath) Then
  WScript.Echo "SUCCESS: " & resultPath
Else
  WScript.Echo "FAILED: shortcut not found at " & resultPath
End If
