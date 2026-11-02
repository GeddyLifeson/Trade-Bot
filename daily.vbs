' Runs daily.bat with no console window (scheduled task TradeBot-Daily).
CreateObject("WScript.Shell").Run """" & Replace(WScript.ScriptFullName, "daily.vbs", "daily.bat") & """", 0, True
