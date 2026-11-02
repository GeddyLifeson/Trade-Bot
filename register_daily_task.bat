@echo off
rem Registers TradeBot-Daily: runs daily.vbs (no window) every day at 07:00.
schtasks /create /tn TradeBot-Daily /tr "wscript.exe //B \"%~dp0daily.vbs\"" /sc daily /st 07:00 /f
pause
