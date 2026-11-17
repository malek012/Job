@echo off
rem Creates the "Jobs" desktop shortcut with the Job Applier icon.
rem Double-click once. Run again if you move the Job_applier folder.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root = '%~dp0'.TrimEnd('\');" ^
  "$desk = [Environment]::GetFolderPath('Desktop');" ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$lnk = $ws.CreateShortcut((Join-Path $desk 'Jobs.lnk'));" ^
  "$lnk.TargetPath = (Join-Path $root 'web-ui\Launch-WebUI.bat');" ^
  "$lnk.WorkingDirectory = (Join-Path $root 'web-ui');" ^
  "$lnk.IconLocation = (Join-Path $root 'assets\jobs.ico') + ',0';" ^
  "$lnk.Description = 'Job Applier Control Center';" ^
  "$lnk.Save();" ^
  "Write-Host ('Created: ' + (Join-Path $desk 'Jobs.lnk'));" ^
  "$old = Join-Path $desk 'Jobs.bat';" ^
  "if (Test-Path $old) { Write-Host 'Note: the old Jobs.bat is still on your Desktop. You can delete it now - the new icon replaces it.' }"
pause
