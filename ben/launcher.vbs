Set objShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")
strPath = objFSO.GetParentFolderName(WScript.ScriptFullName)
If strPath = "" Then strPath = objShell.CurrentDirectory
objShell.CurrentDirectory = strPath
objShell.Run "cmd /c cd /d """ & strPath & """ && python launcher.py", 0
