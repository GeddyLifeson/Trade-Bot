@echo off
rem Phase 1 daily refresh. Keys come from user environment variables (see README).
cd /d "%~dp0"
if not exist E:\TradeBot\logs mkdir E:\TradeBot\logs
python -m tradebot.cli daily >> E:\TradeBot\logs\daily.log 2>&1
