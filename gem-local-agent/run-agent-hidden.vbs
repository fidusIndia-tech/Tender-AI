' Launches the GeM Tender Agent loop in the background with NO visible window.
' Started automatically at login by the Startup shortcut that install-agent.bat creates.
Option Explicit
Dim fso, sh, baseDir, py, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
baseDir = fso.GetParentFolderName(WScript.ScriptFullName)

' Prefer the local venv's windowless Python; fall back to system pythonw.
py = baseDir & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(py) Then
    py = fso.GetParentFolderName(baseDir) & "\.venv\Scripts\pythonw.exe"
End If
If Not fso.FileExists(py) Then py = "pythonw.exe"

sh.CurrentDirectory = baseDir
cmd = """" & py & """ agent.py --loop --interval-minutes 30"
' 0 = hidden window, False = do not wait.
sh.Run cmd, 0, False
