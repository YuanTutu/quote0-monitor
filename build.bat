@echo off
rem quote0-monitor one-click build:
rem   1. kill running exe (file lock blocks PyInstaller)
rem   2. PyInstaller onefile build (bundled renderers/providers/fonts)
rem   3. copy source config.json -> dist\config.json
rem      (minus burnout_dir/glm_dir: exe uses its built-in bundle on other machines)
rem NOTE for distribution: send ONLY dist\quote0-monitor.exe to others.
rem       dist\config.json contains YOUR real keys - never ship it.
cd /d %~dp0

taskkill /f /im quote0-monitor.exe >nul 2>&1

python -m PyInstaller --onefile --windowed --name quote0-monitor --clean --noconfirm ^
  --add-data "bundle;bundle" ^
  monitor.py
if errorlevel 1 goto :err

python -c "import json,pathlib; c=json.loads(pathlib.Path('config.json').read_text(encoding='utf-8')); [c.pop(k,None) for k in ('burnout_dir','glm_dir')]; pathlib.Path('dist/config.json').write_text(json.dumps(c,indent=2,ensure_ascii=False),encoding='utf-8'); print('config copied (module dirs stripped)')"

echo.
echo ================================================
echo  Build OK: dist\quote0-monitor.exe + config.json
echo  (exe ready to run on this machine right away)
echo  WARN: dist\config.json holds REAL keys.
echo  To distribute, send the exe file ONLY.
echo ================================================
pause
exit /b 0

:err
echo Build FAILED - see messages above.
pause
exit /b 1
