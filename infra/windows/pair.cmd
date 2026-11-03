@echo off
rem Pair this PC with the CA firm's RAJ server, then start the background connector.
rem Usage: pair.cmd [server-url] [pairing-code] [tally-url]   (asks for anything missing)
setlocal
net session >nul 2>&1
if errorlevel 1 (
  echo Asking Windows for administrator rights...
  powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0' -ArgumentList '%*'"
  exit /b
)
set "EXE=%~dp0raj-connector.exe"
set "DATA=%ProgramData%\RAJ"
set "SERVER=%~1"
set "CODE=%~2"
set "TALLY=%~3"
if "%TALLY%"=="" set "TALLY=http://127.0.0.1:9000"
echo.
echo  RAJ Connector - pair this PC with your CA firm
echo  ----------------------------------------------
if "%SERVER%"=="" set /p "SERVER=Firm server address (e.g. https://raj.example.in): "
if "%CODE%"=="" set /p "CODE=Pairing code from the firm (RAJ-....): "
if not exist "%DATA%" mkdir "%DATA%"
icacls "%DATA%" /inheritance:r /grant:r *S-1-5-18:(OI)(CI)F /grant:r *S-1-5-32-544:(OI)(CI)F >nul
echo.
echo Checking Tally at %TALLY% ...
"%EXE%" companies --tally "%TALLY%"
if errorlevel 1 (
  echo.
  echo Tally is not reachable. Open TallyPrime with the client's company, enable port 9000
  echo ^(F1 Help ^> Settings ^> Connectivity^), then run "Pair this PC with the firm" again.
  pause
  exit /b 1
)
"%EXE%" --data "%DATA%" service uninstall >nul 2>&1
echo.
"%EXE%" --data "%DATA%" pair --server "%SERVER%" --code "%CODE%" --tally "%TALLY%"
if errorlevel 1 (
  echo Pairing failed. Check the server address and ask the firm for a new pairing code.
  pause
  exit /b 1
)
"%EXE%" --data "%DATA%" service install
if errorlevel 1 ( pause & exit /b 1 )
echo.
echo Done. This PC is paired and the RAJ connector now runs in the background (also after restart).
echo Posting stays OFF until both the firm and this PC enable it.
pause
